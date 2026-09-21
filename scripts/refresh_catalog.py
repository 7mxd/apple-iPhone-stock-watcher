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
