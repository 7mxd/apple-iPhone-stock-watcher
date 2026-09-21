# Apple iPhone Stock Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poll Apple UAE's store-pickup endpoint on a GitHub Actions cron and push an ntfy alert the moment a watched iPhone SKU becomes collectable at a watched store.

**Architecture:** A single scheduled job makes one HTTP GET to `/ae/shop/retail/pickup-message`, which returns all five UAE stores at once. Pure functions resolve watch rules to part numbers, match observations against rules, and diff against a `state.json` committed back to the repository. Only the outer edges (`apple.py`, `notify.py`, `cli.py`) touch the network or filesystem; everything worth testing is pure.

**Tech Stack:** Python 3.11+, `requests`, `PyYAML`, `pytest`. No web framework, no browser automation, no database.

**Spec:** `docs/superpowers/specs/2026-09-21-apple-iphone-stock-watcher-design.md`

## Global Constraints

- Python 3.11 or newer (the code uses `X | None` type syntax and `zoneinfo`).
- Dependencies limited to `requests`, `PyYAML`, `pytest`. Add nothing else.
- **Match rule is a denylist:** a store is a hit when `pickupDisplay not in {"unavailable", "ineligible"}`. Never match on a positive literal. See spec 3.4.
- **`screen` is not a filterable watch field.** `familyType` and `dimensionScreensize` are 1:1, so only `model` is exposed. See spec 3.3.
- **Location selectors are mutually exclusive.** A watch uses exactly one of `stores`, `cities`, `emirates`. Supplying two is a load-time error.
- `cities: [Abu Dhabi]` resolves to R706 and R595 only. `emirates: [Abu Dhabi]` additionally includes R785 (Al Jimi Mall, city Al Ain).
- State is keyed by `part_number|store_number`, never by watch.
- All displayed timestamps use the `Asia/Dubai` timezone.
- Never commit the ntfy topic. It lives in the `NTFY_TOPIC` environment variable.
- Every Apple request sends a desktop browser `User-Agent`. Requests without one have been observed to fail.

---

### Task 1: Scaffolding, domain models, store table

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/applewatch/__init__.py`, `src/applewatch/models.py`, `src/applewatch/stores.py`, `stores.yml`
- Test: `tests/test_models.py`, `tests/test_stores.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Store(number, name, city, emirate)`, `Sku(part_number, model, capacity, color, screen)` with `.label` and `.buy_url`, `Availability(part_number, store_number, pickup_display, quote)` with `.is_hit`, `Watch(...)`, `Event(...)`, constants `NEGATIVE_PICKUP_STATES` and `PRIORITY_TIERS`, and `load_stores() -> dict[str, Store]`.

- [ ] **Step 1: Create the project skeleton**

`pyproject.toml`:

```toml
[project]
name = "applewatch"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["requests>=2.31", "PyYAML>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

`.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
*.egg-info/
```

Create an empty `src/applewatch/__init__.py` and an empty `tests/__init__.py`.

- [ ] **Step 2: Write the failing tests for models**

`tests/test_models.py`:

```python
from applewatch.models import Availability, Sku

TARGET = Sku(
    part_number="MJXC4AH/A",
    model="iphone18promax",
    capacity="512gb",
    color="burgundy",
    screen="6_9inch",
)


def test_sku_label_is_human_readable():
    assert TARGET.label == "iPhone 18 Pro Max 512GB Burgundy"


def test_sku_buy_url_matches_apples_real_url():
    assert TARGET.buy_url == (
        "https://www.apple.com/ae/shop/buy-iphone/iphone-18-pro"
        "/6.9-inch-display-512gb-burgundy"
    )


def test_unavailable_is_not_a_hit():
    obs = Availability("MJXC4AH/A", "R706", "unavailable", "Currently unavailable")
    assert obs.is_hit is False


def test_ineligible_is_not_a_hit():
    obs = Availability("MJX94AH/A", "R597", "ineligible", "Currently unavailable")
    assert obs.is_hit is False


def test_unrecognised_state_is_a_hit():
    """The positive value was never observed live, so anything not on the
    denylist must count as stock. See spec 3.4."""
    obs = Availability("MJXC4AH/A", "R595", "wibble", "Available Wed 23 Sep")
    assert obs.is_hit is True
    assert obs.is_unexpected is True


def test_available_is_a_hit_and_not_unexpected():
    obs = Availability("MJXC4AH/A", "R595", "available", "Available Wed 23 Sep")
    assert obs.is_hit is True
    assert obs.is_unexpected is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.models'`

- [ ] **Step 4: Implement the models**

`src/applewatch/models.py`:

```python
"""Domain types shared across the package.

Every type here is frozen and free of I/O so the matching and diffing logic
can be tested without touching the network or the filesystem.
"""

from dataclasses import dataclass

# Values of `pickupDisplay` that mean "you cannot collect this today".
# Deliberately a denylist: the positive value has never been observed live,
# so anything outside this set is treated as stock. See spec 3.4.
NEGATIVE_PICKUP_STATES = frozenset({"unavailable", "ineligible"})

# The one positive value we believe exists. Used only to decide whether to
# annotate a message as unexpected, never to decide whether it is a hit.
EXPECTED_POSITIVE_STATE = "available"

PRIORITY_TIERS = {"urgent": 5, "high": 4, "default": 3, "low": 2}

_MODEL_LABELS = {
    "iphone18pro": "iPhone 18 Pro",
    "iphone18promax": "iPhone 18 Pro Max",
}


@dataclass(frozen=True)
class Store:
    number: str   # "R706", stable across renames
    name: str     # "Al Maryah Island"
    city: str     # "Abu Dhabi", must match Apple's string exactly
    emirate: str  # "Abu Dhabi", maintained by us; Apple sends an empty state


@dataclass(frozen=True)
class Sku:
    part_number: str  # "MJXC4AH/A"
    model: str        # "iphone18promax"
    capacity: str     # "512gb"
    color: str        # "burgundy"
    screen: str       # "6_9inch", provenance only, not filterable

    @property
    def label(self) -> str:
        model = _MODEL_LABELS.get(self.model, self.model)
        return f"{model} {self.capacity.upper()} {self.color.title()}"

    @property
    def buy_url(self) -> str:
        # "6_9inch" -> "6.9-inch"
        inches = self.screen.replace("inch", "").replace("_", ".")
        slug = f"{inches}-inch-display-{self.capacity}-{self.color}"
        return (
            "https://www.apple.com/ae/shop/buy-iphone/iphone-18-pro/" + slug
        )


@dataclass(frozen=True)
class Availability:
    """One (sku, store) observation from Apple."""

    part_number: str
    store_number: str
    pickup_display: str
    quote: str  # pickupSearchQuote, passed through verbatim

    @property
    def is_hit(self) -> bool:
        return self.pickup_display not in NEGATIVE_PICKUP_STATES

    @property
    def is_unexpected(self) -> bool:
        return self.is_hit and self.pickup_display != EXPECTED_POSITIVE_STATE


@dataclass(frozen=True)
class Watch:
    name: str
    priority: str = "urgent"
    model: str | None = None
    capacity: tuple[str, ...] = ()
    color: tuple[str, ...] = ()
    stores: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    emirates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Event:
    kind: str  # "in_stock" | "reminder" | "gone"
    sku: Sku
    store: Store
    quote: str
    pickup_display: str
    priority: int
    watches: tuple[str, ...]
    minutes: int = 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: 6 passed

- [ ] **Step 6: Write the failing test for the store table**

`tests/test_stores.py`:

```python
from applewatch.stores import load_stores


def test_loads_all_five_uae_stores():
    assert len(load_stores()) == 5


def test_al_jimi_is_al_ain_city_but_abu_dhabi_emirate():
    """The trap this project exists to avoid. Apple returns city 'Al Ain',
    so a city filter for Abu Dhabi must not pick it up, but an emirate
    filter must. See spec 3.2."""
    al_jimi = load_stores()["R785"]
    assert al_jimi.city == "Al Ain"
    assert al_jimi.emirate == "Abu Dhabi"


def test_abu_dhabi_city_has_exactly_two_stores():
    stores = load_stores().values()
    assert {s.number for s in stores if s.city == "Abu Dhabi"} == {"R706", "R595"}


def test_abu_dhabi_emirate_has_exactly_three_stores():
    stores = load_stores().values()
    assert {s.number for s in stores if s.emirate == "Abu Dhabi"} == {
        "R706",
        "R595",
        "R785",
    }
```

- [ ] **Step 7: Run it to confirm it fails**

Run: `pytest tests/test_stores.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.stores'`

- [ ] **Step 8: Create the store table and loader**

`stores.yml`:

```yaml
# Apple Store locations in the UAE.
#
# `emirate` is maintained here because Apple's pickup-message payload returns
# an empty `state` field for every UAE store. `city` must match Apple's string
# byte-for-byte because city filters compare against it literally.
#
# Verified against the live endpoint on 2026-09-21.
stores:
  - number: R706
    name: Al Maryah Island
    city: Abu Dhabi
    emirate: Abu Dhabi
  - number: R595
    name: Yas Mall
    city: Abu Dhabi
    emirate: Abu Dhabi
  - number: R596
    name: Mall of the Emirates
    city: Dubai
    emirate: Dubai
  - number: R597
    name: Dubai Mall
    city: Dubai
    emirate: Dubai
  - number: R785
    name: Al Jimi Mall
    city: Al Ain
    emirate: Abu Dhabi
```

`src/applewatch/stores.py`:

```python
"""Loads the hand-maintained UAE store table."""

from pathlib import Path

import yaml

from .models import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORES_PATH = REPO_ROOT / "stores.yml"


def load_stores(path: Path = DEFAULT_STORES_PATH) -> dict[str, Store]:
    """Return stores keyed by store number, e.g. {"R706": Store(...)}."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    stores = {}
    for entry in raw["stores"]:
        store = Store(
            number=entry["number"],
            name=entry["name"],
            city=entry["city"],
            emirate=entry["emirate"],
        )
        stores[store.number] = store
    return stores
```

- [ ] **Step 9: Run the full suite**

Run: `pytest -v`
Expected: 10 passed

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .gitignore src/ tests/ stores.yml
git commit -m "feat: add domain models and UAE store table

Store table carries an emirate column because Apple's payload returns an
empty state field for every UAE store. Al Jimi Mall is city Al Ain but
emirate Abu Dhabi, which the tests pin down explicitly."
```

---

### Task 2: SKU catalog generation and resolution

**Files:**
- Create: `scripts/refresh_catalog.py`, `catalog.json` (generated), `src/applewatch/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Sku` from Task 1.
- Produces: `load_catalog() -> dict[str, Sku]` keyed by part number, and `select_skus(catalog, model, capacity, color) -> list[Sku]` where empty filter tuples mean "any".

- [ ] **Step 1: Write the catalog refresh script**

`scripts/refresh_catalog.py`:

```python
"""Regenerate catalog.json from Apple UAE's product page.

Part numbers change when Apple revises the lineup, so the catalog is derived
data rather than something hand-edited.

Extraction is anchored on whole flat JSON objects rather than on a proximity
window between fields. A windowed regex was tried during the spike and failed
in both directions: too narrow silently dropped MJXG4AH/A, and too wide paired
a part number with the next SKU's dimensions. See spec 3.3.
"""

import itertools
import json
import re
import sys
from datetime import date
from pathlib import Path

import requests

URL = "https://www.apple.com/ae/shop/buy-iphone/iphone-18-pro"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
OUTPUT = Path(__file__).resolve().parents[1] / "catalog.json"

# A flat object (no nested braces) carrying both the part number and its
# dimensions. The [^{}] classes are what make the pairing unambiguous.
SKU_OBJECT = re.compile(
    r'\{[^{}]*?"partNumber":"(?P<part_number>[A-Z0-9]+AH/A)"[^{}]*?'
    r'"familyType":"(?P<model>\w+)"[^{}]*?'
    r'"dimensionCapacity":"(?P<capacity>\w+)",'
    r'"dimensionScreensize":"(?P<screen>\w+)",'
    r'"dimensionColor":"(?P<color>\w+)"[^{}]*?\}'
)

COLORS = ["black", "burgundy", "glacier", "silver"]
CAPACITIES = ["256gb", "512gb", "1tb", "2tb"]
SCREENS = ["6_3inch", "6_9inch"]


def extract(html: str) -> dict[str, dict]:
    skus: dict[str, dict] = {}
    for match in SKU_OBJECT.finditer(html):
        entry = match.groupdict()
        part = entry["part_number"]
        previous = skus.get(part)
        if previous is not None and previous != entry:
            raise SystemExit(
                f"conflicting dimensions for {part}: {previous} vs {entry}"
            )
        skus[part] = entry
    return skus


def assert_complete(skus: dict[str, dict]) -> None:
    """Fail loudly on a gap.

    A catalog missing a SKU produces a watch that silently matches nothing,
    which is indistinguishable from 'no stock'. That is the exact failure
    this project exists to prevent, so a gap is fatal.
    """
    seen = {(s["screen"], s["capacity"], s["color"]) for s in skus.values()}
    expected = set(itertools.product(SCREENS, CAPACITIES, COLORS))
    missing = sorted(expected - seen)
    if missing:
        raise SystemExit(f"catalog incomplete, missing {len(missing)}: {missing}")


def main() -> int:
    response = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    skus = extract(response.text)
    assert_complete(skus)
    payload = {
        "generated": date.today().isoformat(),
        "source": URL,
        "skus": sorted(skus.values(), key=lambda s: s["part_number"]),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(skus)} SKUs to {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Generate the catalog and verify it**

Run: `python scripts/refresh_catalog.py`
Expected: `wrote 32 SKUs to .../catalog.json`

Then confirm the target SKU landed correctly:

Run: `python -c "import json;d=json.load(open('catalog.json'));print(len(d['skus']));print([s for s in d['skus'] if s['part_number']=='MJXC4AH/A'])"`
Expected: `32` followed by the burgundy 512GB Pro Max entry.

If the script exits with `catalog incomplete`, Apple has changed its page markup. Do not weaken the assertion to get past it. Fix the regex.

- [ ] **Step 3: Write the failing tests**

`tests/test_catalog.py`:

```python
from applewatch.catalog import load_catalog, select_skus

TARGET = "MJXC4AH/A"


def test_catalog_is_a_complete_cross_product():
    """Guards the committed data file, not just the generator."""
    catalog = load_catalog()
    assert len(catalog) == 32
    assert len({(s.screen, s.capacity, s.color) for s in catalog.values()}) == 32


def test_target_sku_is_burgundy_512_pro_max():
    sku = load_catalog()[TARGET]
    assert (sku.model, sku.capacity, sku.color) == (
        "iphone18promax",
        "512gb",
        "burgundy",
    )


def test_one_tb_burgundy_pro_max_is_present():
    """MJXG4AH/A was silently dropped by the first extraction attempt."""
    assert load_catalog()["MJXG4AH/A"].capacity == "1tb"


def test_empty_filters_select_everything():
    catalog = load_catalog()
    assert len(select_skus(catalog, None, (), ())) == 32


def test_full_filter_selects_exactly_one():
    catalog = load_catalog()
    selected = select_skus(catalog, "iphone18promax", ("512gb",), ("burgundy",))
    assert [s.part_number for s in selected] == [TARGET]


def test_list_values_within_a_field_are_ored():
    catalog = load_catalog()
    selected = select_skus(
        catalog, "iphone18promax", ("512gb",), ("burgundy", "black")
    )
    assert len(selected) == 2


def test_model_narrows_to_one_family():
    catalog = load_catalog()
    assert len(select_skus(catalog, "iphone18promax", (), ())) == 16
```

- [ ] **Step 4: Run them to confirm they fail**

Run: `pytest tests/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.catalog'`

- [ ] **Step 5: Implement the catalog module**

`src/applewatch/catalog.py`:

```python
"""Loads the generated SKU catalog and resolves watch filters to SKUs."""

import json
from pathlib import Path

from .models import Sku

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = REPO_ROOT / "catalog.json"


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Sku]:
    """Return SKUs keyed by part number."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    catalog = {}
    for entry in raw["skus"]:
        sku = Sku(
            part_number=entry["part_number"],
            model=entry["model"],
            capacity=entry["capacity"],
            color=entry["color"],
            screen=entry["screen"],
        )
        catalog[sku.part_number] = sku
    return catalog


def select_skus(
    catalog: dict[str, Sku],
    model: str | None,
    capacity: tuple[str, ...],
    color: tuple[str, ...],
) -> list[Sku]:
    """Filter the catalog. An empty tuple or None means 'no constraint'.

    Fields AND together; values within a field OR together. See spec 5.3.
    """
    selected = []
    for sku in catalog.values():
        if model is not None and sku.model != model:
            continue
        if capacity and sku.capacity not in capacity:
            continue
        if color and sku.color not in color:
            continue
        selected.append(sku)
    return sorted(selected, key=lambda s: s.part_number)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_catalog.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add scripts/refresh_catalog.py catalog.json src/applewatch/catalog.py tests/test_catalog.py
git commit -m "feat: generate and resolve the SKU catalog

Extraction anchors on flat JSON objects rather than a proximity window,
after the windowed form dropped MJXG4AH/A when narrow and mispaired
dimensions when wide. Generation asserts a complete 4x4x2 cross-product
so a gap fails loudly instead of yielding a watch that matches nothing."
```

---

### Task 3: Watch configuration loading and validation

**Files:**
- Create: `src/applewatch/config.py`, `watches.yml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Watch` from Task 1, `load_catalog` from Task 2, `load_stores` from Task 1.
- Produces: `Config(ntfy_topic_env, poll_location, reminder_minutes, watches)`, `load_config(path, catalog, stores) -> Config`, and `ConfigError`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import pytest

from applewatch.catalog import load_catalog
from applewatch.config import ConfigError, load_config
from applewatch.stores import load_stores

CATALOG = load_catalog()
STORES = load_stores()


def write(tmp_path, body: str):
    path = tmp_path / "watches.yml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
ntfy_topic_env: NTFY_TOPIC
poll_location: Abu Dhabi
reminder_minutes: 30
watches:
  - name: "Burgundy 512 Pro Max, Abu Dhabi city"
    model: iphone18promax
    capacity: [512gb]
    color: [burgundy]
    cities: [Abu Dhabi]
    priority: urgent
"""


def test_loads_the_default_watch(tmp_path):
    config = load_config(write(tmp_path, BASE), CATALOG, STORES)
    assert config.reminder_minutes == 30
    assert config.poll_location == "Abu Dhabi"
    assert len(config.watches) == 1
    assert config.watches[0].cities == ("Abu Dhabi",)


def test_two_location_selectors_is_an_error(tmp_path):
    body = BASE + "    emirates: [Abu Dhabi]\n"
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "cities" in str(exc.value) and "emirates" in str(exc.value)


def test_misspelled_colour_is_an_error(tmp_path):
    """A typo must fail loudly, not silently match nothing."""
    body = BASE.replace("[burgundy]", "[burgandy]")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "burgandy" in str(exc.value)


def test_unknown_store_is_an_error(tmp_path):
    body = BASE.replace("cities: [Abu Dhabi]", "stores: [R999]")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "R999" in str(exc.value)


def test_unknown_priority_is_an_error(tmp_path):
    body = BASE.replace("priority: urgent", "priority: screaming")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "screaming" in str(exc.value)


def test_unknown_key_is_an_error(tmp_path):
    """`screen` is deliberately not a field. Accepting it silently would let
    a user write a rule they think is narrower than it is."""
    body = BASE + "    screen: 6_9inch\n"
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "screen" in str(exc.value)


def test_no_location_selector_is_allowed(tmp_path):
    body = BASE.replace("    cities: [Abu Dhabi]\n", "")
    config = load_config(write(tmp_path, body), CATALOG, STORES)
    assert config.watches[0].cities == ()
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.config'`

- [ ] **Step 3: Implement the config loader**

`src/applewatch/config.py`:

```python
"""Loads and validates watches.yml.

Validation is deliberately strict. Every silent mismatch in this project looks
identical to 'no stock', so a typo must stop the run rather than quietly
watching nothing.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import PRIORITY_TIERS, Sku, Store, Watch

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "watches.yml"

LOCATION_SELECTORS = ("stores", "cities", "emirates")
ALLOWED_KEYS = {"name", "priority", "model", "capacity", "color", *LOCATION_SELECTORS}


class ConfigError(Exception):
    """Raised for any malformed or unrecognisable configuration."""


@dataclass(frozen=True)
class Config:
    ntfy_topic_env: str
    poll_location: str
    reminder_minutes: int
    watches: tuple[Watch, ...]


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _validate_values(name, values, allowed, field, errors):
    unknown = sorted(set(values) - allowed)
    if unknown:
        errors.append(
            f"watch {name!r}: unknown {field} {unknown}, expected one of "
            f"{sorted(allowed)}"
        )


def _build_watch(raw: dict, catalog: dict[str, Sku], stores: dict[str, Store], errors):
    name = raw.get("name")
    if not name:
        errors.append("every watch needs a name")
        return None

    unknown_keys = sorted(set(raw) - ALLOWED_KEYS)
    if unknown_keys:
        errors.append(
            f"watch {name!r}: unknown keys {unknown_keys}. Note that `screen` is "
            f"not a field: model already implies screen size."
        )

    used = [key for key in LOCATION_SELECTORS if raw.get(key)]
    if len(used) > 1:
        errors.append(
            f"watch {name!r}: {used} are mutually exclusive, pick one. "
            f"cities [Abu Dhabi] is R706 and R595; emirates [Abu Dhabi] also "
            f"includes R785 Al Jimi Mall."
        )

    priority = raw.get("priority", "urgent")
    if priority not in PRIORITY_TIERS:
        errors.append(
            f"watch {name!r}: unknown priority {priority!r}, expected one of "
            f"{sorted(PRIORITY_TIERS)}"
        )

    capacity = _as_tuple(raw.get("capacity"))
    color = _as_tuple(raw.get("color"))
    store_refs = _as_tuple(raw.get("stores"))
    cities = _as_tuple(raw.get("cities"))
    emirates = _as_tuple(raw.get("emirates"))

    model = raw.get("model")
    if model is not None:
        _validate_values(
            name, (model,), {s.model for s in catalog.values()}, "model", errors
        )
    _validate_values(
        name, capacity, {s.capacity for s in catalog.values()}, "capacity", errors
    )
    _validate_values(name, color, {s.color for s in catalog.values()}, "color", errors)
    _validate_values(
        name,
        store_refs,
        {s.number for s in stores.values()} | {s.name for s in stores.values()},
        "store",
        errors,
    )
    _validate_values(name, cities, {s.city for s in stores.values()}, "city", errors)
    _validate_values(
        name, emirates, {s.emirate for s in stores.values()}, "emirate", errors
    )

    return Watch(
        name=name,
        priority=priority,
        model=model,
        capacity=capacity,
        color=color,
        stores=store_refs,
        cities=cities,
        emirates=emirates,
    )


def load_config(
    path: Path,
    catalog: dict[str, Sku],
    stores: dict[str, Store],
) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errors: list[str] = []

    entries = raw.get("watches") or []
    if not entries:
        errors.append("watches.yml defines no watches")

    watches = [_build_watch(entry, catalog, stores, errors) for entry in entries]

    if errors:
        raise ConfigError("\n".join(errors))

    return Config(
        ntfy_topic_env=raw.get("ntfy_topic_env", "NTFY_TOPIC"),
        poll_location=raw.get("poll_location", "Abu Dhabi"),
        reminder_minutes=int(raw.get("reminder_minutes", 30)),
        watches=tuple(w for w in watches if w is not None),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Create the shipped watches.yml**

`watches.yml`:

```yaml
# What to watch. See README.md for the full field reference.
#
# Location selectors are mutually exclusive: a watch uses exactly one of
# `stores`, `cities` or `emirates`.
#
#   cities:   [Abu Dhabi]  ->  R706 Al Maryah Island, R595 Yas Mall
#   emirates: [Abu Dhabi]  ->  the above plus R785 Al Jimi Mall (city Al Ain)
#   stores:   [R706]       ->  exactly what you list
#
# Validate any change before committing it:  python -m applewatch --dry-run

ntfy_topic_env: NTFY_TOPIC
poll_location: Abu Dhabi
reminder_minutes: 30

watches:
  - name: "Burgundy 512GB Pro Max, Abu Dhabi city"
    model: iphone18promax
    capacity: [512gb]
    color: [burgundy]
    cities: [Abu Dhabi]
    priority: urgent

  # To also cover Al Jimi Mall in Al Ain (133 km, Abu Dhabi emirate), replace
  # the `cities` line above with:
  #     emirates: [Abu Dhabi]
  # Replace it rather than adding a second watch: overlapping watches are
  # deduplicated by store, but a second watch is simply redundant.
```

- [ ] **Step 6: Verify the shipped config loads**

Run: `python -c "from applewatch.config import load_config, DEFAULT_CONFIG_PATH; from applewatch.catalog import load_catalog; from applewatch.stores import load_stores; c=load_config(DEFAULT_CONFIG_PATH, load_catalog(), load_stores()); print(c.watches[0])"`
Expected: prints the `Watch` with `cities=('Abu Dhabi',)` and `priority='urgent'`

- [ ] **Step 7: Commit**

```bash
git add src/applewatch/config.py watches.yml tests/test_config.py
git commit -m "feat: load and strictly validate watches.yml

Unknown colours, stores, priorities and keys are load-time errors. In a
project where every silent mismatch looks exactly like 'no stock', a typo
must stop the run rather than quietly watch nothing."
```

---

### Task 4: Apple client

**Files:**
- Create: `src/applewatch/apple.py`, `tests/fixtures/pickup_available_synthetic.json`
- Test: `tests/test_apple.py`
- Existing: `tests/fixtures/pickup_unavailable.json`, `tests/fixtures/pickup_ineligible.json` (already committed, captured live 2026-09-21)

**Interfaces:**
- Consumes: `Availability` from Task 1.
- Produces: `build_params(part_numbers, location) -> dict`, `parse_pickup_response(payload) -> list[Availability]`, `fetch_availability(part_numbers, location, session=None) -> list[Availability]`, and `AppleError`.

- [ ] **Step 1: Create the synthetic positive fixture**

No positive payload could be captured live, so derive one from the real structure rather than inventing a shape. Run this once:

```bash
python - <<'PY'
import json
from pathlib import Path

src = Path("tests/fixtures/pickup_unavailable.json")
dst = Path("tests/fixtures/pickup_available_synthetic.json")
payload = json.loads(src.read_text(encoding="utf-8"))

# Put burgundy 512 in stock at Yas Mall (R595) only. The quote string is the
# one the owner's 2026-09-21 screenshots showed for in-stock SKUs.
for store in payload["body"]["stores"]:
    if store["storeNumber"] != "R595":
        continue
    parts = store["partsAvailability"]["MJXC4AH/A"]
    parts["pickupDisplay"] = "available"
    parts["pickupSearchQuote"] = "Available Wed 23 Sep"

dst.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
print("wrote", dst)
PY
```

The `_synthetic` suffix is mandatory and must not be renamed. Anyone reading these fixtures needs to know at a glance which two are observed reality and which one is a guess.

- [ ] **Step 2: Write the failing tests**

`tests/test_apple.py`:

```python
import json
from pathlib import Path

import pytest

from applewatch.apple import (
    AppleError,
    build_params,
    parse_pickup_response,
)

FIXTURES = Path(__file__).parent / "fixtures"
TARGET = "MJXC4AH/A"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_build_params_indexes_each_part():
    params = build_params(["MJXC4AH/A", "MJX94AH/A"], "Abu Dhabi")
    assert params["parts.0"] == "MJXC4AH/A"
    assert params["parts.1"] == "MJX94AH/A"
    assert params["location"] == "Abu Dhabi"
    assert params["pl"] == "true"
    assert params["mts.0"] == "regular"


def test_parses_all_five_stores():
    observations = parse_pickup_response(fixture("pickup_unavailable"))
    assert {o.store_number for o in observations} == {
        "R706",
        "R595",
        "R596",
        "R597",
        "R785",
    }


def test_real_unavailable_payload_yields_no_hits():
    observations = parse_pickup_response(fixture("pickup_unavailable"))
    assert all(o.is_hit is False for o in observations)
    assert all(o.quote == "Currently unavailable" for o in observations)


def test_real_ineligible_payload_is_not_a_hit():
    observations = parse_pickup_response(fixture("pickup_ineligible"))
    dubai_mall = next(o for o in observations if o.store_number == "R597")
    assert dubai_mall.pickup_display == "ineligible"
    assert dubai_mall.is_hit is False


def test_synthetic_available_payload_yields_one_hit():
    observations = parse_pickup_response(fixture("pickup_available_synthetic"))
    hits = [o for o in observations if o.is_hit]
    assert len(hits) == 1
    assert hits[0].store_number == "R595"
    assert hits[0].part_number == TARGET
    assert hits[0].quote == "Available Wed 23 Sep"


def test_non_200_body_status_raises():
    payload = {"head": {"status": "500"}, "body": {}}
    with pytest.raises(AppleError):
        parse_pickup_response(payload)


def test_missing_stores_key_raises():
    with pytest.raises(AppleError):
        parse_pickup_response({"head": {"status": "200"}, "body": {}})
```

- [ ] **Step 3: Run them to confirm they fail**

Run: `pytest tests/test_apple.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.apple'`

- [ ] **Step 4: Implement the client**

`src/applewatch/apple.py`:

```python
"""Talks to Apple UAE's store-pickup endpoint.

One request returns every UAE store, so store filtering is a local concern.
Verified live on 2026-09-21; see spec 3.1 for the endpoints that do not work.
"""

import time
from collections.abc import Sequence

import requests

from .models import Availability

PICKUP_URL = "https://www.apple.com/ae/shop/retail/pickup-message"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 8)


class AppleError(Exception):
    """Raised when Apple's response cannot be trusted or parsed."""


def build_params(part_numbers: Sequence[str], location: str) -> dict[str, str]:
    params = {"pl": "true", "mts.0": "regular", "location": location}
    for index, part_number in enumerate(part_numbers):
        params[f"parts.{index}"] = part_number
    return params


def parse_pickup_response(payload: dict) -> list[Availability]:
    status = payload.get("head", {}).get("status")
    if status != "200":
        raise AppleError(f"Apple returned body status {status!r}")

    stores = payload.get("body", {}).get("stores")
    if not stores:
        raise AppleError("response contained no stores")

    observations = []
    for store in stores:
        store_number = store.get("storeNumber")
        for part_number, parts in (store.get("partsAvailability") or {}).items():
            observations.append(
                Availability(
                    part_number=part_number,
                    store_number=store_number,
                    pickup_display=parts.get("pickupDisplay", ""),
                    quote=parts.get("pickupSearchQuote", ""),
                )
            )
    return observations


def fetch_availability(
    part_numbers: Sequence[str],
    location: str,
    session: requests.Session | None = None,
) -> list[Availability]:
    """Fetch and parse, retrying transient failures.

    Raises AppleError after MAX_ATTEMPTS so the caller can alert rather than
    treat a broken checker as an absence of stock.
    """
    session = session or requests.Session()
    params = build_params(part_numbers, location)
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = session.get(
                PICKUP_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            return parse_pickup_response(response.json())
        except (requests.RequestException, ValueError, AppleError) as error:
            last_error = error
            if attempt < len(BACKOFF_SECONDS):
                time.sleep(BACKOFF_SECONDS[attempt])

    raise AppleError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_apple.py -v`
Expected: 7 passed

- [ ] **Step 6: Verify against the live endpoint**

Run: `python -c "from applewatch.apple import fetch_availability; [print(o) for o in fetch_availability(['MJXC4AH/A'],'Abu Dhabi')]"`
Expected: five `Availability` rows. As of 2026-09-21 every one reads `unavailable`. If any reads something else, that is real news and worth recording in the spec.

- [ ] **Step 7: Commit**

```bash
git add src/applewatch/apple.py tests/test_apple.py tests/fixtures/pickup_available_synthetic.json
git commit -m "feat: add Apple pickup-message client

Parses against two payloads captured live on 2026-09-21 plus one clearly
named synthetic positive, since no in-stock payload could be recorded
before the stock was claimed."
```

---

### Task 5: Rule matching

**Files:**
- Create: `src/applewatch/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `Watch`, `Sku`, `Store` from Task 1, `select_skus` from Task 2.
- Produces: `store_matches(watch, store) -> bool` and `collect_matches(watches, catalog, stores) -> dict[tuple[str, str], list[Watch]]` keyed by `(part_number, store_number)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_rules.py`:

```python
from applewatch.catalog import load_catalog
from applewatch.models import Watch
from applewatch.rules import collect_matches, store_matches
from applewatch.stores import load_stores

CATALOG = load_catalog()
STORES = load_stores()
TARGET = "MJXC4AH/A"


def test_city_filter_excludes_al_jimi():
    """cities [Abu Dhabi] must not reach Al Ain. See spec 5.3."""
    watch = Watch(name="w", cities=("Abu Dhabi",))
    assert store_matches(watch, STORES["R706"]) is True
    assert store_matches(watch, STORES["R595"]) is True
    assert store_matches(watch, STORES["R785"]) is False


def test_emirate_filter_includes_al_jimi():
    watch = Watch(name="w", emirates=("Abu Dhabi",))
    assert store_matches(watch, STORES["R785"]) is True
    assert store_matches(watch, STORES["R597"]) is False


def test_store_filter_accepts_number_or_name():
    watch = Watch(name="w", stores=("R706", "Yas Mall"))
    assert store_matches(watch, STORES["R706"]) is True
    assert store_matches(watch, STORES["R595"]) is True
    assert store_matches(watch, STORES["R596"]) is False


def test_no_location_selector_matches_every_store():
    watch = Watch(name="w")
    assert all(store_matches(watch, store) for store in STORES.values())


def test_default_watch_produces_two_keys():
    watch = Watch(
        name="default",
        model="iphone18promax",
        capacity=("512gb",),
        color=("burgundy",),
        cities=("Abu Dhabi",),
    )
    matches = collect_matches([watch], CATALOG, STORES)
    assert set(matches) == {(TARGET, "R706"), (TARGET, "R595")}


def test_emirate_variant_produces_three_keys():
    watch = Watch(
        name="emirate",
        model="iphone18promax",
        capacity=("512gb",),
        color=("burgundy",),
        emirates=("Abu Dhabi",),
    )
    matches = collect_matches([watch], CATALOG, STORES)
    assert set(matches) == {(TARGET, "R706"), (TARGET, "R595"), (TARGET, "R785")}


def test_overlapping_watches_collapse_onto_one_key():
    """Two watches, one key, both names recorded. Prevents duplicate alerts."""
    narrow = Watch(name="narrow", color=("burgundy",), capacity=("512gb",),
                   model="iphone18promax", stores=("R706",))
    wide = Watch(name="wide", color=("burgundy",), capacity=("512gb",),
                 model="iphone18promax", cities=("Abu Dhabi",))
    matches = collect_matches([narrow, wide], CATALOG, STORES)
    assert [w.name for w in matches[(TARGET, "R706")]] == ["narrow", "wide"]
    assert [w.name for w in matches[(TARGET, "R595")]] == ["wide"]
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `pytest tests/test_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.rules'`

- [ ] **Step 3: Implement the matcher**

`src/applewatch/rules.py`:

```python
"""Resolves watch rules to the exact (sku, store) pairs they cover.

Pure functions over plain data. This is where the city-versus-emirate
distinction is enforced, so it carries the heaviest test coverage.
"""

from collections.abc import Iterable

from .catalog import select_skus
from .models import Sku, Store, Watch


def store_matches(watch: Watch, store: Store) -> bool:
    """A watch uses exactly one location selector, enforced at config load.

    With none set, every store matches.
    """
    if watch.stores:
        return store.number in watch.stores or store.name in watch.stores
    if watch.cities:
        return store.city in watch.cities
    if watch.emirates:
        return store.emirate in watch.emirates
    return True


def collect_matches(
    watches: Iterable[Watch],
    catalog: dict[str, Sku],
    stores: dict[str, Store],
) -> dict[tuple[str, str], list[Watch]]:
    """Map (part_number, store_number) to the watches covering it.

    Keyed by pair rather than by watch so overlapping watches cannot raise
    duplicate alerts for the same phone in the same shop. See spec 6.
    """
    matches: dict[tuple[str, str], list[Watch]] = {}
    for watch in watches:
        skus = select_skus(catalog, watch.model, watch.capacity, watch.color)
        for sku in skus:
            for store in stores.values():
                if not store_matches(watch, store):
                    continue
                matches.setdefault((sku.part_number, store.number), []).append(watch)
    return matches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rules.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/applewatch/rules.py tests/test_rules.py
git commit -m "feat: match watch rules to (sku, store) pairs

Keyed by pair rather than by watch so overlapping watches collapse onto
one alert. Pins the city-versus-emirate reach difference in tests."
```

---

### Task 6: State diffing and event generation

**Files:**
- Create: `src/applewatch/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `Event`, `Availability`, `Sku`, `Store`, `PRIORITY_TIERS` from Task 1.
- Produces: `load_state(path) -> dict`, `save_state(path, state) -> None`, and `diff(matches, observations, previous, now, reminder_minutes, catalog, stores) -> tuple[list[Event], dict]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_state.py`:

```python
from datetime import datetime, timedelta, timezone

from applewatch.catalog import load_catalog
from applewatch.models import Availability, Watch
from applewatch.state import diff
from applewatch.stores import load_stores

CATALOG = load_catalog()
STORES = load_stores()
TARGET = "MJXC4AH/A"
KEY = (TARGET, "R595")
DUBAI = timezone(timedelta(hours=4))
NOW = datetime(2026, 9, 22, 8, 0, tzinfo=DUBAI)

WATCH = Watch(name="default", priority="urgent")
MATCHES = {KEY: [WATCH]}


def observed(display: str, quote: str = "") -> list[Availability]:
    return [Availability(TARGET, "R595", display, quote)]


def run(display, previous, now=NOW, reminder=30, quote=""):
    return diff(
        MATCHES, observed(display, quote), previous, now, reminder, CATALOG, STORES
    )


def test_first_sighting_emits_in_stock_at_watch_priority():
    events, state = run("available", {}, quote="Available Wed 23 Sep")
    assert [e.kind for e in events] == ["in_stock"]
    assert events[0].priority == 5
    assert events[0].quote == "Available Wed 23 Sep"
    assert events[0].sku.label == "iPhone 18 Pro Max 512GB Burgundy"
    assert events[0].store.name == "Yas Mall"
    assert state[f"{TARGET}|R595"]["status"] == "hit"


def test_unexpected_positive_state_still_alerts_at_full_priority():
    """A positive value we failed to anticipate is still stock on a shelf."""
    events, _ = run("wibble", {})
    assert [e.kind for e in events] == ["in_stock"]
    assert events[0].priority == 5
    assert events[0].pickup_display == "wibble"


def test_unavailable_from_empty_state_emits_nothing():
    events, state = run("unavailable", {})
    assert events == []
    assert state[f"{TARGET}|R595"]["status"] == "miss"


def test_sustained_hit_stays_quiet_before_the_reminder_window():
    previous = {
        f"{TARGET}|R595": {
            "status": "hit",
            "since": (NOW - timedelta(minutes=20)).isoformat(),
            "last_notified": (NOW - timedelta(minutes=20)).isoformat(),
        }
    }
    events, _ = run("available", previous)
    assert events == []


def test_sustained_hit_reminds_after_the_window():
    previous = {
        f"{TARGET}|R595": {
            "status": "hit",
            "since": (NOW - timedelta(minutes=47)).isoformat(),
            "last_notified": (NOW - timedelta(minutes=31)).isoformat(),
        }
    }
    events, state = run("available", previous)
    assert [e.kind for e in events] == ["reminder"]
    assert events[0].priority == 3
    assert events[0].minutes == 47
    assert state[f"{TARGET}|R595"]["last_notified"] == NOW.isoformat()


def test_hit_to_miss_emits_gone_with_duration():
    previous = {
        f"{TARGET}|R595": {
            "status": "hit",
            "since": (NOW - timedelta(minutes=52)).isoformat(),
            "last_notified": (NOW - timedelta(minutes=52)).isoformat(),
        }
    }
    events, state = run("unavailable", previous)
    assert [e.kind for e in events] == ["gone"]
    assert events[0].priority == 2
    assert events[0].minutes == 52
    assert state[f"{TARGET}|R595"]["status"] == "miss"


def test_unwatched_pairs_are_ignored():
    observations = [Availability(TARGET, "R597", "available", "")]
    events, state = diff(MATCHES, observations, {}, NOW, 30, CATALOG, STORES)
    assert events == []
    assert f"{TARGET}|R597" not in state


def test_highest_priority_among_overlapping_watches_wins():
    matches = {KEY: [Watch(name="quiet", priority="low"), Watch(name="loud", priority="urgent")]}
    events, _ = diff(matches, observed("available"), {}, NOW, 30, CATALOG, STORES)
    assert events[0].priority == 5
    assert events[0].watches == ("quiet", "loud")
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.state'`

- [ ] **Step 3: Implement state handling**

`src/applewatch/state.py`:

```python
"""Diffs the current observation against the previous run.

Each Actions container is fresh, so the previous run's knowledge lives in
state.json, committed back to the repository.
"""

import json
from datetime import datetime
from pathlib import Path

from .models import PRIORITY_TIERS, Availability, Event, Sku, Store, Watch

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = REPO_ROOT / "state.json"

REMINDER_PRIORITY = PRIORITY_TIERS["default"]  # 3
GONE_PRIORITY = PRIORITY_TIERS["low"]  # 2


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    Path(path).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")


def _minutes_between(start: str | None, now: datetime) -> int:
    if not start:
        return 0
    return int((now - datetime.fromisoformat(start)).total_seconds() // 60)


def diff(
    matches: dict[tuple[str, str], list[Watch]],
    observations: list[Availability],
    previous: dict,
    now: datetime,
    reminder_minutes: int,
    catalog: dict[str, Sku],
    stores: dict[str, Store],
) -> tuple[list[Event], dict]:
    """Return the events to send and the state to persist.

    Only pairs present in `matches` are considered; everything else in the
    payload is ignored, including pairs left over in the previous state.
    """
    events: list[Event] = []
    state: dict = {}

    by_pair = {(o.part_number, o.store_number): o for o in observations}

    for key, watches in matches.items():
        observation = by_pair.get(key)
        if observation is None:
            # Apple did not report this pair at all. Carry the previous entry
            # forward untouched; cli.py raises the rotated-part-number alarm.
            state_key = f"{key[0]}|{key[1]}"
            if state_key in previous:
                state[state_key] = previous[state_key]
            continue

        part_number, store_number = key
        state_key = f"{part_number}|{store_number}"
        before = previous.get(state_key, {})
        was_hit = before.get("status") == "hit"
        priority = max(PRIORITY_TIERS[w.priority] for w in watches)
        names = tuple(w.name for w in watches)

        if observation.is_hit and not was_hit:
            events.append(
                Event(
                    kind="in_stock",
                    sku=catalog[part_number],
                    store=stores[store_number],
                    quote=observation.quote,
                    pickup_display=observation.pickup_display,
                    priority=priority,
                    watches=names,
                )
            )
            state[state_key] = {
                "status": "hit",
                "quote": observation.quote,
                "since": now.isoformat(),
                "last_notified": now.isoformat(),
            }

        elif observation.is_hit and was_hit:
            elapsed_since_alert = _minutes_between(before.get("last_notified"), now)
            state[state_key] = dict(before)
            state[state_key]["quote"] = observation.quote
            if elapsed_since_alert >= reminder_minutes:
                events.append(
                    Event(
                        kind="reminder",
                        sku=catalog[part_number],
                        store=stores[store_number],
                        quote=observation.quote,
                        pickup_display=observation.pickup_display,
                        priority=REMINDER_PRIORITY,
                        watches=names,
                        minutes=_minutes_between(before.get("since"), now),
                    )
                )
                state[state_key]["last_notified"] = now.isoformat()

        elif not observation.is_hit and was_hit:
            events.append(
                Event(
                    kind="gone",
                    sku=catalog[part_number],
                    store=stores[store_number],
                    quote=observation.quote,
                    pickup_display=observation.pickup_display,
                    priority=GONE_PRIORITY,
                    watches=names,
                    minutes=_minutes_between(before.get("since"), now),
                )
            )
            state[state_key] = {
                "status": "miss",
                "quote": observation.quote,
                "since": now.isoformat(),
                "last_notified": None,
            }

        else:
            state[state_key] = {
                "status": "miss",
                "quote": observation.quote,
                "since": before.get("since", now.isoformat()),
                "last_notified": before.get("last_notified"),
            }

    return events, state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/applewatch/state.py tests/test_state.py
git commit -m "feat: diff observations against previous state

An unanticipated positive pickupDisplay alerts at full watch priority
rather than a demoted tier: unfamiliar stock is still stock."
```

---

### Task 7: ntfy notifications

**Files:**
- Create: `src/applewatch/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `Event` from Task 1.
- Produces: `render(event) -> tuple[str, str, dict]` returning `(title, body, headers)`, `send(topic, event, session=None) -> None`, and `send_text(topic, title, body, priority, session=None) -> None` for alerts that have no SKU or store behind them.

- [ ] **Step 1: Write the failing tests**

`tests/test_notify.py`:

```python
from applewatch.catalog import load_catalog
from applewatch.models import Event
from applewatch.notify import render
from applewatch.stores import load_stores

CATALOG = load_catalog()
STORES = load_stores()
SKU = CATALOG["MJXC4AH/A"]
YAS = STORES["R595"]


def event(kind, priority, display="available", quote="Available Wed 23 Sep", minutes=0):
    return Event(
        kind=kind,
        sku=SKU,
        store=YAS,
        quote=quote,
        pickup_display=display,
        priority=priority,
        watches=("default",),
        minutes=minutes,
    )


def test_in_stock_title_names_the_phone_and_the_store():
    title, body, headers = render(event("in_stock", 5))
    assert "iPhone 18 Pro Max 512GB Burgundy" in title
    assert "Yas Mall" in title
    assert "Available Wed 23 Sep" in body
    assert headers["Priority"] == "5"


def test_in_stock_links_straight_to_the_buy_page():
    _, _, headers = render(event("in_stock", 5))
    assert headers["Click"] == SKU.buy_url


def test_unexpected_state_is_flagged_in_the_body():
    _, body, _ = render(event("in_stock", 5, display="wibble"))
    assert "wibble" in body


def test_expected_state_is_not_flagged():
    _, body, _ = render(event("in_stock", 5, display="available"))
    assert "wibble" not in body and "unexpected" not in body.lower()


def test_reminder_reports_how_long_it_has_been_up():
    title, body, headers = render(event("reminder", 3, minutes=47))
    assert "47" in title or "47" in body
    assert headers["Priority"] == "3"


def test_gone_reports_the_window_length():
    title, body, headers = render(event("gone", 2, display="unavailable",
                                        quote="Currently unavailable", minutes=52))
    assert "52" in title or "52" in body
    assert headers["Priority"] == "2"


def test_send_text_posts_a_bare_message(monkeypatch):
    """Health alerts have no SKU or store behind them."""
    captured = {}

    class FakeSession:
        def post(self, url, data, headers, timeout):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return type("R", (), {"raise_for_status": lambda self: None})()

    from applewatch.notify import send_text

    send_text("topic-abc", "Checker broken", "simulated outage", 2,
              session=FakeSession())
    assert captured["url"].endswith("/topic-abc")
    assert captured["headers"]["Priority"] == "2"
    assert b"simulated outage" in captured["data"]
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'applewatch.notify'`

- [ ] **Step 3: Implement notifications**

`src/applewatch/notify.py`:

```python
"""Renders and sends ntfy notifications.

The topic name is the only secret, so it is read from the environment and
never logged.
"""

import requests

from .models import EXPECTED_POSITIVE_STATE, Event

NTFY_BASE = "https://ntfy.sh"
TAGS = {"in_stock": "rotating_light", "reminder": "hourglass", "gone": "wave"}


def render(event: Event) -> tuple[str, str, dict[str, str]]:
    phone = event.sku.label
    store = event.store.name

    if event.kind == "in_stock":
        title = f"IN STOCK: {phone} at {store}"
        body = event.quote or "Available for pickup"
        if event.pickup_display != EXPECTED_POSITIVE_STATE:
            body += f"\n(unexpected pickup state: {event.pickup_display})"
    elif event.kind == "reminder":
        title = f"Still in stock: {phone} at {store}"
        body = f"{event.quote}\nUp for {event.minutes} min"
    else:
        title = f"Gone: {phone} at {store}"
        body = f"Window lasted {event.minutes} min"

    body += f"\nWatch: {', '.join(event.watches)}"

    headers = {
        "Title": title,
        "Priority": str(event.priority),
        "Tags": TAGS.get(event.kind, "bell"),
        "Click": event.sku.buy_url,
    }
    return title, body, headers


def send(topic: str, event: Event, session: requests.Session | None = None) -> None:
    session = session or requests.Session()
    _, body, headers = render(event)
    response = session.post(
        f"{NTFY_BASE}/{topic}",
        data=body.encode("utf-8"),
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()


def send_text(
    topic: str,
    title: str,
    body: str,
    priority: int,
    session: requests.Session | None = None,
) -> None:
    """Send an alert with no SKU or store behind it, such as a health warning."""
    session = session or requests.Session()
    response = session.post(
        f"{NTFY_BASE}/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": str(priority),
            "Tags": "warning",
        },
        timeout=15,
    )
    response.raise_for_status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notify.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/applewatch/notify.py tests/test_notify.py
git commit -m "feat: render and send ntfy alerts

Messages carry the raw pickupSearchQuote so the owner sees Apple's own
wording, plus a Click header linking straight to the buy page."
```

---

### Task 8: CLI wiring and resilience

**Files:**
- Create: `src/applewatch/cli.py`, `src/applewatch/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 7.
- Produces: `main(argv=None) -> int` supporting `--dry-run`, `--force-notify`, `--config`, `--state`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import json
from pathlib import Path

import pytest

from applewatch import cli

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "send", lambda topic, event, session=None: calls.append(event))
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    return calls


@pytest.fixture
def health_alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "send_text",
        lambda topic, title, body, priority, session=None: calls.append(title),
    )
    return calls


def stub_apple(monkeypatch, fixture_name):
    from applewatch.apple import parse_pickup_response

    payload = json.loads((FIXTURES / f"{fixture_name}.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(
        cli, "fetch_availability", lambda parts, location: parse_pickup_response(payload)
    )


def test_no_stock_sends_nothing_and_exits_zero(monkeypatch, sent, tmp_path):
    stub_apple(monkeypatch, "pickup_unavailable")
    code = cli.main(["--state", str(tmp_path / "state.json")])
    assert code == 0
    assert sent == []


def test_stock_sends_one_alert(monkeypatch, sent, tmp_path):
    stub_apple(monkeypatch, "pickup_available_synthetic")
    code = cli.main(["--state", str(tmp_path / "state.json")])
    assert code == 0
    assert [e.kind for e in sent] == ["in_stock"]
    assert sent[0].store.number == "R595"


def test_dry_run_sends_nothing(monkeypatch, sent, tmp_path, capsys):
    stub_apple(monkeypatch, "pickup_available_synthetic")
    code = cli.main(["--dry-run", "--state", str(tmp_path / "state.json")])
    assert code == 0
    assert sent == []
    assert "IN STOCK" in capsys.readouterr().out


def test_dry_run_does_not_write_state(monkeypatch, sent, tmp_path):
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_available_synthetic")
    cli.main(["--dry-run", "--state", str(state_path)])
    assert not state_path.exists()


def test_second_run_is_quiet(monkeypatch, sent, tmp_path):
    """Dedup across runs is the whole point of persisting state."""
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_available_synthetic")
    cli.main(["--state", str(state_path)])
    cli.main(["--state", str(state_path)])
    assert [e.kind for e in sent] == ["in_stock"]


def break_apple(monkeypatch):
    from applewatch.apple import AppleError

    def boom(parts, location):
        raise AppleError("simulated outage")

    monkeypatch.setattr(cli, "fetch_availability", boom)


def test_apple_failure_exits_nonzero_and_alerts(monkeypatch, sent, health_alerts, tmp_path):
    """A broken checker must not look like an absence of stock."""
    break_apple(monkeypatch)
    code = cli.main(["--state", str(tmp_path / "state.json")])
    assert code != 0
    assert len(health_alerts) == 1
    assert sent == []


def test_repeat_failure_within_six_hours_stays_quiet(
    monkeypatch, sent, health_alerts, tmp_path
):
    """Apple being down for a day must not produce 288 notifications."""
    state_path = tmp_path / "state.json"
    break_apple(monkeypatch)
    cli.main(["--state", str(state_path)])
    cli.main(["--state", str(state_path)])
    cli.main(["--state", str(state_path)])
    assert len(health_alerts) == 1


def test_failure_does_not_destroy_existing_state(
    monkeypatch, sent, health_alerts, tmp_path
):
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_available_synthetic")
    cli.main(["--state", str(state_path)])
    before = json.loads(state_path.read_text(encoding="utf-8"))

    break_apple(monkeypatch)
    cli.main(["--state", str(state_path)])
    after = json.loads(state_path.read_text(encoding="utf-8"))

    assert after["MJXC4AH/A|R595"] == before["MJXC4AH/A|R595"]


def test_missing_topic_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    stub_apple(monkeypatch, "pickup_unavailable")
    assert cli.main(["--state", str(tmp_path / "state.json")]) != 0
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ImportError` or `AttributeError` on `applewatch.cli`

- [ ] **Step 3: Implement the CLI**

`src/applewatch/cli.py`:

```python
"""Entry point: fetch, match, diff, notify, persist."""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .apple import AppleError, fetch_availability
from .catalog import DEFAULT_CATALOG_PATH, load_catalog
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .notify import render, send, send_text
from .rules import collect_matches
from .state import DEFAULT_STATE_PATH, diff, load_state, save_state
from .stores import load_stores

DUBAI = ZoneInfo("Asia/Dubai")

# Apple being down for a day must not produce 288 notifications.
HEALTH_ALERT_INTERVAL_HOURS = 6


def _report_broken(state_path: Path, topic: str | None, now, message: str) -> None:
    """Alert that the checker itself is broken, at most once per interval.

    Silence from a broken checker is indistinguishable from silence from an
    empty shelf, which is the failure this whole project is built to avoid.
    Preserves existing state: only the _health entry is touched.
    """
    state = load_state(state_path)
    health = state.get("_health", {})
    last = health.get("last_error_notified")
    due = True
    if last:
        elapsed_hours = (now - datetime.fromisoformat(last)).total_seconds() / 3600
        due = elapsed_hours >= HEALTH_ALERT_INTERVAL_HOURS

    if due and topic:
        send_text(topic, "Stock checker is broken", message, 2)
        health["last_error_notified"] = now.isoformat()

    health["last_error"] = message
    state["_health"] = health
    save_state(state_path, state)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="applewatch")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent, touch nothing",
    )
    parser.add_argument(
        "--force-notify",
        action="store_true",
        help="send one test alert for the first watched pair and exit",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    stores = load_stores()
    try:
        config = load_config(Path(args.config), catalog, stores)
    except ConfigError as error:
        print(f"config error:\n{error}", file=sys.stderr)
        return 2

    topic = os.environ.get(config.ntfy_topic_env)
    if not topic and not args.dry_run:
        print(
            f"{config.ntfy_topic_env} is not set. Export it or use --dry-run.",
            file=sys.stderr,
        )
        return 2

    matches = collect_matches(config.watches, catalog, stores)
    if not matches:
        print("no watch matched any SKU or store, check watches.yml", file=sys.stderr)
        return 2

    part_numbers = sorted({part for part, _ in matches})
    print(f"watching {len(part_numbers)} SKU(s) across {len(matches)} store pair(s)")

    if args.force_notify:
        from .models import Event

        part_number, store_number = sorted(matches)[0]
        send(
            topic,
            Event(
                kind="in_stock",
                sku=catalog[part_number],
                store=stores[store_number],
                quote="Test alert, no real stock",
                pickup_display="available",
                priority=5,
                watches=("--force-notify",),
            ),
        )
        print("sent one test alert")
        return 0

    now = datetime.now(DUBAI)

    try:
        observations = fetch_availability(part_numbers, config.poll_location)
    except AppleError as error:
        print(f"apple request failed: {error}", file=sys.stderr)
        if not args.dry_run:
            _report_broken(Path(args.state), topic, now, str(error))
        return 1

    reported = {(o.part_number, o.store_number) for o in observations}
    missing = sorted(key for key in matches if key not in reported)
    if missing:
        print(
            f"WARNING: Apple did not report {len(missing)} watched pair(s): {missing}. "
            f"Part numbers may have rotated; run scripts/refresh_catalog.py.",
            file=sys.stderr,
        )

    previous = load_state(Path(args.state))
    events, state = diff(
        matches, observations, previous, now, config.reminder_minutes, catalog, stores
    )
    if "_health" in previous:
        state["_health"] = previous["_health"]

    for event in events:
        title, body, _ = render(event)
        if args.dry_run:
            print(f"[would send] {title}\n{body}\n")
        else:
            send(topic, event)
            print(f"sent: {title}")

    if not events:
        print("no changes")

    if not args.dry_run:
        state["_heartbeat"] = now.isoformat()
        save_state(Path(args.state), state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`src/applewatch/__main__.py`:

```python
import sys

from .cli import main

sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: 9 passed

- [ ] **Step 5: Run the whole suite**

Run: `pytest -v`
Expected: 62 passed (6 models, 4 stores, 7 catalog, 7 config, 7 apple, 7 rules, 8 state, 7 notify, 9 cli)

- [ ] **Step 6: Verify the dry run against the live endpoint**

Run: `python -m applewatch --dry-run`
Expected: `watching 1 SKU(s) across 2 store pair(s)` then `no changes`. Nothing written, nothing sent.

- [ ] **Step 7: Commit**

```bash
git add src/applewatch/cli.py src/applewatch/__main__.py tests/test_cli.py
git commit -m "feat: wire the checker end to end

Adds --dry-run for validating a config change before committing it, and
warns loudly when Apple stops reporting a watched pair, since a rotated
part number would otherwise read as a permanent absence of stock."
```

---

### Task 9: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/check.yml`, `state.json` (seeded empty)

**Interfaces:**
- Consumes: `python -m applewatch` from Task 8.
- Produces: a scheduled job that commits `state.json` when it changes.

- [ ] **Step 1: Seed an empty state file**

```bash
echo '{}' > state.json
```

Committing it up front means the first scheduled run does a plain update rather than creating a file, which keeps the commit step uniform.

- [ ] **Step 2: Write the workflow**

`.github/workflows/check.yml`:

```yaml
name: check-stock

on:
  schedule:
    # Every 5 minutes. GitHub drifts under load, so expect 5 to 15 minutes
    # in practice. This is the documented minimum interval.
    - cron: "*/5 * * * *"
  workflow_dispatch:

# Overlapping runs would race the state commit.
concurrency:
  group: check-stock
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install
        run: pip install -e .

      - name: Check availability
        env:
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: python -m applewatch

      - name: Commit state
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add state.json
          if git diff --cached --quiet; then
            echo "no state change"
            exit 0
          fi
          git commit -m "chore: update availability state [skip ci]"
          git push
```

- [ ] **Step 3: Verify the workflow parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/check.yml')); print('valid yaml')"`
Expected: `valid yaml`

- [ ] **Step 4: Commit and push**

```bash
git add .github/workflows/check.yml state.json
git commit -m "ci: poll every five minutes and commit state

State is committed on every run, which doubles as the heartbeat that keeps
GitHub from disabling the cron after 60 days of repository inactivity."
git push
```

- [ ] **Step 5: Set the secret and trigger a manual run**

These steps need the repository owner, since they involve a real device and a real secret.

```bash
gh secret set NTFY_TOPIC --body "<the topic chosen during README setup>"
gh workflow run check-stock
gh run watch
```

Expected: the run succeeds and logs `watching 1 SKU(s) across 2 store pair(s)` followed by `no changes`.

- [ ] **Step 6: Verify the alert path end to end**

Run: `gh workflow run check-stock` is not enough on its own, because nothing is in stock. Instead, from a local shell with the topic exported:

Run: `NTFY_TOPIC=<topic> python -m applewatch --force-notify`
Expected: one notification arrives on the phone within a few seconds, titled `IN STOCK: iPhone 18 Pro Max 512GB Burgundy at Al Maryah Island`, and tapping it opens Apple's buy page.

If nothing arrives, the fault is in the phone setup rather than the code. Work through the README's ntfy section, especially the Focus allowlist.

---

### Task 10: README

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything. This is documentation of the finished system.
- Produces: nothing consumed by code.

- [ ] **Step 1: Write the README**

`README.md` must contain these sections, in this order. Every value below is verified, not illustrative.

1. **What this does.** One paragraph: polls Apple UAE's store-pickup endpoint every five minutes from GitHub Actions and pushes an ntfy notification when a watched iPhone becomes collectable at a watched store. No UI, no automated purchasing.

2. **Quick start.** Fork, set the `NTFY_TOPIC` secret, enable Actions, edit `watches.yml`.

3. **Configuring watches.** The core section. It must include:

   - The full field table:

     | Field | Type | Omitted means | Matches against |
     |---|---|---|---|
     | `name` | string | required | label shown in notifications |
     | `model` | string | any model | `iphone18pro`, `iphone18promax` |
     | `capacity` | list | any capacity | `256gb`, `512gb`, `1tb`, `2tb` |
     | `color` | list | any colour | `burgundy`, `black`, `silver`, `glacier` |
     | `stores` | list | see below | store number or exact name |
     | `cities` | list | see below | `Abu Dhabi`, `Dubai`, `Al Ain` |
     | `emirates` | list | see below | `Abu Dhabi`, `Dubai` |
     | `priority` | enum | `urgent` | `urgent`, `high`, `default`, `low` |

   - A note that `screen` is deliberately absent because `model` already implies it.

   - The store table:

     | Number | Name | City | Emirate |
     |---|---|---|---|
     | R706 | Al Maryah Island | Abu Dhabi | Abu Dhabi |
     | R595 | Yas Mall | Abu Dhabi | Abu Dhabi |
     | R596 | Mall of the Emirates | Dubai | Dubai |
     | R597 | Dubai Mall | Dubai | Dubai |
     | R785 | Al Jimi Mall | Al Ain | Abu Dhabi |

   - **Matching rules**, stated plainly: fields AND together; values within a field OR together; an omitted field is a wildcard; `stores`, `cities` and `emirates` are mutually exclusive and using two is an error.

   - **The Al Jimi worked example**, spelled out: `cities: [Abu Dhabi]` gives R706 and R595, because Apple files Al Jimi Mall under the city Al Ain. `emirates: [Abu Dhabi]` gives all three. Choose deliberately, since Al Jimi is 133 km away, farther than Dubai Mall.

   - **Four recipes**, each a complete YAML block: the strict default; any colour at one store; a whole emirate; every 512GB nationwide.

4. **Validating a change.** `python -m applewatch --dry-run` prints the resolved SKUs, the matched store pairs and any alerts that would fire, without sending or writing anything. Run it before committing a config change.

5. **ntfy setup**, numbered:
   1. Install ntfy from the App Store.
   2. Choose an unguessable topic, for example `apple-ae-burgundy-k7f2q9x`. Topics on ntfy.sh are readable by anyone who knows the name, so the random suffix is the only thing protecting it.
   3. In the app, **+ → Subscribe to topic**, enter the name, leave the server as `ntfy.sh`.
   4. Allow notifications.
   5. In **Settings → Notifications → ntfy**, enable Time Sensitive Notifications.
   6. Add ntfy to the allowed apps for your sleep Focus. Without this a 4am restock alert is suppressed, which defeats the project.
   7. `gh secret set NTFY_TOPIC --body "<topic>"`.
   8. Verify with `curl -d "test" ntfy.sh/<topic>`.

6. **How alerts behave.** Urgent on the flip to in stock, a quieter reminder every 30 minutes while it lasts, a low-priority note when it goes. Tuned via `reminder_minutes`.

7. **Troubleshooting**, as a table: no alerts ever (check the Focus allowlist, run `--force-notify`); "unknown colour" at startup (typo, valid values are listed above); "Apple did not report N watched pair(s)" (part numbers rotated, run `python scripts/refresh_catalog.py`); workflow stopped running (GitHub disables cron after 60 days idle, re-enable in the Actions tab).

8. **How it works**, brief: one GET to `/ae/shop/retail/pickup-message` returns all five UAE stores; a store counts as in stock when `pickupDisplay` is anything other than `unavailable` or `ineligible`. Link to the spec for why that is a denylist.

9. **Scope.** Reads a public availability endpoint at roughly the rate a person refreshing the page would. Does not automate purchasing.

- [ ] **Step 2: Verify every command in the README actually runs**

Run each command block in a clean shell. In particular:

Run: `python -m applewatch --dry-run`
Expected: exits 0 and prints the resolved pair count.

Run: `python scripts/refresh_catalog.py`
Expected: `wrote 32 SKUs`.

Fix the README rather than the code if any command differs by so much as a flag.

- [ ] **Step 3: Commit and push**

```bash
git add README.md
git commit -m "docs: document watch configuration and ntfy setup

The configuration section spells out the city-versus-emirate distinction
explicitly, since Al Jimi Mall sits in the Abu Dhabi emirate but the city
of Al Ain and a city filter silently excludes it."
git push
```

---

## Verification

After Task 10, confirm the following before calling the project done:

- [ ] `pytest -v` passes with no skips.
- [ ] `python -m applewatch --dry-run` exits 0 against the live endpoint.
- [ ] `python -m applewatch --force-notify` puts a real notification on the phone, and tapping it opens the buy page.
- [ ] The scheduled workflow has completed at least one green run in the Actions tab.
- [ ] `state.json` on `main` shows a `_heartbeat` timestamp from that run.
- [ ] `git log` shows no commit containing the ntfy topic string.
