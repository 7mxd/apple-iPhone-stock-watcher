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
