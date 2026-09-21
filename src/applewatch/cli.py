"""Entry point: fetch, match, diff, notify, persist."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .apple import AppleError, fetch_availability
from .catalog import DEFAULT_CATALOG_PATH, load_catalog
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .notify import NotifyError, render, send, send_text
from .rules import collect_matches
from .state import DEFAULT_STATE_PATH, diff, load_state, save_state
from .stores import load_stores

DUBAI = timezone(timedelta(hours=4))

# Apple being down for a day must not produce 288 notifications.
HEALTH_ALERT_INTERVAL_HOURS = 6


def _ntfy_failure_detail(error: Exception) -> str:
    """Best available diagnostic for a failed ntfy call.

    notify.py sanitizes real ntfy failures into NotifyError before they
    reach any call site here, and NotifyError's own message already names
    the underlying requests exception type (HTTPError vs ConnectionError,
    etc.), so it is safe to print verbatim. Anything else reaching one of
    these except blocks is an unanticipated bug in the call site itself,
    not a sanitized ntfy failure, so it still collapses to a bare type name
    rather than risk printing an unsanitized message that could contain the
    ntfy topic.
    """
    if isinstance(error, NotifyError):
        return str(error)
    return type(error).__name__


def _report_broken(state_path: Path, topic: str | None, now, message: str) -> None:
    """Alert that the checker itself is broken, at most once per interval.

    Silence from a broken checker is indistinguishable from silence from an
    empty shelf, which is the failure this whole project is built to avoid.
    Preserves existing state: only the _health entry is touched.

    The rate-limit timestamp is written *before* the send is attempted, and
    the send itself can never raise past this function. Otherwise a slow or
    failing ntfy POST would both (a) defeat the 6-hour rate limit, since the
    timestamp would never be recorded, and (b) leak the ntfy topic: requests
    embeds the full "https://ntfy.sh/<topic>" URL in its exception text, and
    this repo's Actions logs are public.
    """
    try:
        state = load_state(state_path)
    except Exception:
        # This function exists to report brokenness, including a corrupt
        # state.json; it must not itself crash on the exact corruption it
        # is trying to alert about. Starting fresh here still yields a
        # valid state.json with the health entry, which is strictly better
        # than crashing back out to an unhandled traceback.
        state = {}
    health = state.get("_health", {})
    last = health.get("last_error_notified")
    due = True
    if last:
        elapsed_hours = (now - datetime.fromisoformat(last)).total_seconds() / 3600
        due = elapsed_hours >= HEALTH_ALERT_INTERVAL_HOURS

    if due and topic:
        health["last_error_notified"] = now.isoformat()
        try:
            send_text(topic, "Stock checker is broken", message, 2)
        except Exception as error:
            # Broad on purpose: guarantees the ntfy topic can never reach
            # stdout/stderr from this call site, at the cost of collapsing
            # any programming error here to a bare type name. notify.py
            # already sanitizes real ntfy failures into NotifyError before
            # they get here; this is belt-and-braces, not the primary fix.
            print(
                f"ntfy POST failed ({_ntfy_failure_detail(error)}); health "
                f"alert not delivered",
                file=sys.stderr,
            )

    health["last_error"] = message
    state["_health"] = health
    save_state(state_path, state)


def _heartbeat_value(previous_heartbeat: str | None, now: datetime) -> str:
    """Once-daily marker, not once-per-run.

    diff() rebuilds the state dict from scratch on every call, so without
    this the previous _heartbeat would simply be dropped and rewritten on
    every run, turning every five-minute poll into a state.json commit and
    making the workflow's "no state change" branch dead code.
    """
    if previous_heartbeat:
        try:
            previous_date = datetime.fromisoformat(previous_heartbeat).date()
        except ValueError:
            previous_date = None
        if previous_date == now.date():
            return previous_heartbeat
    return now.isoformat()


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

    if args.dry_run and args.force_notify:
        print(
            "--dry-run and --force-notify are mutually exclusive: dry-run "
            "promises nothing is sent, force-notify exists only to send "
            "something real.",
            file=sys.stderr,
        )
        return 2

    # Known before the topic is resolved, so a failure that happens before
    # that point can still be reported (with the send skipped, since there
    # is nothing to send to) rather than escaping unreported.
    topic: str | None = None

    try:
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
                f"{config.ntfy_topic_env} is not set. Export it or use "
                f"--dry-run.",
                file=sys.stderr,
            )
            return 2

        matches = collect_matches(config.watches, catalog, stores)
        if not matches:
            print(
                "no watch matched any SKU or store, check watches.yml",
                file=sys.stderr,
            )
            return 2

        part_numbers = sorted({part for part, _ in matches})
        print(
            f"watching {len(part_numbers)} SKU(s) across {len(matches)} "
            f"store pair(s)"
        )

        if args.force_notify:
            from .models import Event

            part_number, store_number = sorted(matches)[0]
            try:
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
            except Exception as error:
                # Broad on purpose: guarantees the ntfy topic can never
                # reach stdout/stderr from this call site, at the cost of
                # collapsing any programming error here to a bare type
                # name. notify.py already sanitizes real ntfy failures
                # into NotifyError before they get here; this is
                # belt-and-braces, not the primary fix.
                print(
                    f"ntfy POST failed ({_ntfy_failure_detail(error)}); "
                    f"test alert not delivered",
                    file=sys.stderr,
                )
                return 1
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
            # Any watched pair Apple does not report -- one of several, or
            # every single one -- is what a rotated part number or a broken
            # response shape looks like, not what "in stock nowhere" looks
            # like, so even a partial miss is treated as a broken checker
            # rather than a quiet, permanent absence of stock. A checker
            # that silently drops one target out of several is exactly the
            # failure mode this project exists to prevent.
            message = (
                f"Apple did not report {len(missing)} of {len(matches)} "
                f"watched pair(s): {missing}; part numbers may have "
                f"rotated. Run scripts/refresh_catalog.py."
            )
            print(f"apple request failed: {message}", file=sys.stderr)
            if not args.dry_run:
                _report_broken(Path(args.state), topic, now, message)
            return 1

        # A fetch that reached this point recovered (or was never broken),
        # so any stale _health entry from an earlier failure is
        # deliberately dropped rather than carried forward: an outage a few
        # hours after a recovery must be able to alert again immediately,
        # not stay rate-limited by an unrelated failure that has already
        # been resolved.
        previous = load_state(Path(args.state))
        events, state = diff(
            matches,
            observations,
            previous,
            now,
            config.reminder_minutes,
            catalog,
            stores,
        )

        for event in events:
            title, body, _ = render(event)
            if args.dry_run:
                print(f"[would send] {title}\n{body}\n")
            else:
                try:
                    send(topic, event)
                except Exception as error:
                    # Broad on purpose: guarantees the ntfy topic can never
                    # reach stdout/stderr from this call site, at the cost
                    # of collapsing any programming error here to a bare
                    # type name. notify.py already sanitizes real ntfy
                    # failures into NotifyError before they get here; this
                    # is belt-and-braces, not the primary fix.
                    print(
                        f"ntfy POST failed ({_ntfy_failure_detail(error)}); "
                        f"alert not delivered",
                        file=sys.stderr,
                    )
                    return 1
                print(f"sent: {title}")

        if not events:
            print("no changes")

        if not args.dry_run:
            state["_heartbeat"] = _heartbeat_value(previous.get("_heartbeat"), now)
            save_state(Path(args.state), state)

        return 0
    except Exception as error:
        # Every exception above this point that is not one of the specific,
        # already-handled cases (ConfigError, AppleError, a missing-pairs
        # response, or a failed ntfy send, each of which returns on its
        # own) reaches here. Without this, an AttributeError from a
        # malformed API response, a JSONDecodeError from a corrupt
        # state.json, or any other unanticipated bug would escape main()
        # as a bare traceback: no ntfy, just a red run the owner may not be
        # watching. Only the exception's type name is ever passed onward,
        # never str(error): _report_broken writes it into the public
        # state.json as last_error, and an arbitrary exception message
        # could contain anything, including this project's only secret.
        # Exception, never BaseException, so Ctrl-C and SystemExit (e.g.
        # from argparse or sys.exit elsewhere) still propagate normally.
        if not args.dry_run:
            _report_broken(
                Path(args.state), topic, datetime.now(DUBAI), type(error).__name__
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
