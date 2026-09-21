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
