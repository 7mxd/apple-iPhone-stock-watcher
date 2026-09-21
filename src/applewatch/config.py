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

    # 0 or negative makes the reminder fire on every run, and a non-numeric
    # value raises an uncaught ValueError deep inside diff(); both must be
    # caught here, at load time, alongside every other config typo.
    reminder_minutes_raw = raw.get("reminder_minutes", 30)
    reminder_minutes = 30
    try:
        reminder_minutes = int(reminder_minutes_raw)
        if reminder_minutes < 1:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(
            f"reminder_minutes: {reminder_minutes_raw!r} must be an integer "
            f">= 1"
        )

    if errors:
        raise ConfigError("\n".join(errors))

    return Config(
        ntfy_topic_env=raw.get("ntfy_topic_env", "NTFY_TOPIC"),
        poll_location=raw.get("poll_location", "Abu Dhabi"),
        reminder_minutes=reminder_minutes,
        watches=tuple(w for w in watches if w is not None),
    )
