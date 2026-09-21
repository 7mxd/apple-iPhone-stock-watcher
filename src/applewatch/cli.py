"""Entry point: fetch, match, diff, notify, persist."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .apple import AppleError, fetch_availability
from .catalog import DEFAULT_CATALOG_PATH, load_catalog
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .notify import render, send, send_text
from .rules import collect_matches
from .state import DEFAULT_STATE_PATH, diff, load_state, save_state
from .stores import load_stores

DUBAI = timezone(timedelta(hours=4))

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
