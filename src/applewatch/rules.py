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
