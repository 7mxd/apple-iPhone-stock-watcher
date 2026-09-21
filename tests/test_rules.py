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
