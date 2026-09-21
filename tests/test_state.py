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
    assert state[f"{TARGET}|R595"]["last_notified"] == NOW.isoformat()


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


def test_omitted_pair_carries_previous_state_forward():
    """Apple omits a watched pair (rotated part number): carry previous state untouched."""
    state_key = f"{TARGET}|R595"
    previous = {
        state_key: {
            "status": "hit",
            "since": (NOW - timedelta(minutes=10)).isoformat(),
            "last_notified": (NOW - timedelta(minutes=10)).isoformat(),
        }
    }
    events, state = diff(MATCHES, [], previous, NOW, 30, CATALOG, STORES)
    assert events == []
    assert state_key in state
    assert state[state_key] == previous[state_key]


def test_omitted_pair_with_no_previous_state_is_simply_skipped():
    """Apple omits a watched pair with no prior state: skip it."""
    state_key = f"{TARGET}|R595"
    events, state = diff(MATCHES, [], {}, NOW, 30, CATALOG, STORES)
    assert events == []
    assert state_key not in state
