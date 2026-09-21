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
