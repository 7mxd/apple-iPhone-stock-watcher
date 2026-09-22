import json
from pathlib import Path

import pytest

from applewatch import cli

FIXTURES = Path(__file__).parent / "fixtures"
TEST_CONFIG = FIXTURES / "watches_test.yml"


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    """Pin every CLI test to a fixed config instead of the repo's live one.

    Editing watches.yml is the documented way to use this project, so a
    suite that reads it fails whenever the owner legitimately changes what
    they watch. These tests cover CLI behaviour, not the current shopping
    list. The shipped watches.yml is validated separately, by
    test_config.py, without pinning any particular SKU or store count.
    """
    monkeypatch.setattr(cli, "DEFAULT_CONFIG_PATH", TEST_CONFIG)


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "send", lambda topic, event, session=None: calls.append(event))
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    return calls


@pytest.fixture
def health_alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "send_text",
        lambda topic, title, body, priority, session=None: calls.append(title),
    )
    return calls


def stub_apple(monkeypatch, fixture_name):
    from applewatch.apple import parse_pickup_response

    payload = json.loads((FIXTURES / f"{fixture_name}.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(
        cli, "fetch_availability", lambda parts, location: parse_pickup_response(payload)
    )


def test_no_stock_sends_nothing_and_exits_zero(monkeypatch, sent, tmp_path):
    stub_apple(monkeypatch, "pickup_unavailable")
    code = cli.main(["--state", str(tmp_path / "state.json")])
    assert code == 0
    assert sent == []


def test_stock_sends_one_alert(monkeypatch, sent, tmp_path):
    stub_apple(monkeypatch, "pickup_available_synthetic")
    code = cli.main(["--state", str(tmp_path / "state.json")])
    assert code == 0
    assert [e.kind for e in sent] == ["in_stock"]
    assert sent[0].store.number == "R595"


def test_dry_run_sends_nothing(monkeypatch, sent, tmp_path, capsys):
    stub_apple(monkeypatch, "pickup_available_synthetic")
    code = cli.main(["--dry-run", "--state", str(tmp_path / "state.json")])
    assert code == 0
    assert sent == []
    assert "IN STOCK" in capsys.readouterr().out


def test_dry_run_does_not_write_state(monkeypatch, sent, tmp_path):
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_available_synthetic")
    cli.main(["--dry-run", "--state", str(state_path)])
    assert not state_path.exists()


def test_second_run_is_quiet(monkeypatch, sent, tmp_path):
    """Dedup across runs is the whole point of persisting state."""
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_available_synthetic")
    cli.main(["--state", str(state_path)])
    cli.main(["--state", str(state_path)])
    assert [e.kind for e in sent] == ["in_stock"]


def break_apple(monkeypatch):
    from applewatch.apple import AppleError

    def boom(parts, location):
        raise AppleError("simulated outage")

    monkeypatch.setattr(cli, "fetch_availability", boom)


def test_apple_failure_exits_nonzero_and_alerts(monkeypatch, sent, health_alerts, tmp_path):
    """A broken checker must not look like an absence of stock."""
    break_apple(monkeypatch)
    code = cli.main(["--state", str(tmp_path / "state.json")])
    assert code != 0
    assert len(health_alerts) == 1
    assert sent == []


def test_repeat_failure_within_six_hours_stays_quiet(
    monkeypatch, sent, health_alerts, tmp_path
):
    """Apple being down for a day must not produce 288 notifications."""
    state_path = tmp_path / "state.json"
    break_apple(monkeypatch)
    cli.main(["--state", str(state_path)])
    cli.main(["--state", str(state_path)])
    cli.main(["--state", str(state_path)])
    assert len(health_alerts) == 1


def test_failure_does_not_destroy_existing_state(
    monkeypatch, sent, health_alerts, tmp_path
):
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_available_synthetic")
    cli.main(["--state", str(state_path)])
    before = json.loads(state_path.read_text(encoding="utf-8"))

    break_apple(monkeypatch)
    cli.main(["--state", str(state_path)])
    after = json.loads(state_path.read_text(encoding="utf-8"))

    assert after["MJXC4AH/A|R595"] == before["MJXC4AH/A|R595"]


def test_missing_topic_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    stub_apple(monkeypatch, "pickup_unavailable")
    assert cli.main(["--state", str(tmp_path / "state.json")]) != 0


# --- Fix round 1 -------------------------------------------------------
#
# The five tests below were added in response to a code review that found
# defects in the plan cli.py was transcribed from (see the task-8 fix
# report for details): a secret-leak path, a rate-limit that a failed send
# could defeat, a rotated-part-number scenario that stayed silent, a
# --dry-run that --force-notify could override, and an untested rate-limit
# release. This comment covers all six review findings; the sixth
# (health clearing on recovery) piggybacks on test_failure_does_not_destroy_
# existing_state's sibling below.


def test_ntfy_failure_does_not_leak_topic(monkeypatch, sent, tmp_path, capsys):
    """A broken ntfy POST must never put the topic on stdout/stderr.

    requests' raise_for_status() embeds the full request URL, including the
    topic, in the exception text. This repo's CI logs are public, so an
    unhandled exception here would hand the one secret in this project to
    anyone who reads a failed run.
    """
    import requests

    def boom_send(topic, event, session=None):
        raise requests.HTTPError(
            "403 Client Error: Forbidden for url: https://ntfy.sh/secret-topic-abc"
        )

    monkeypatch.setattr(cli, "send", boom_send)
    stub_apple(monkeypatch, "pickup_available_synthetic")

    cli.main(["--state", str(tmp_path / "state.json")])

    captured = capsys.readouterr()
    assert "secret-topic-abc" not in captured.out + captured.err


def test_health_alert_timestamp_persists_when_send_fails(
    monkeypatch, sent, tmp_path
):
    """A failing send_text must not defeat the 6-hour rate limit.

    If the timestamp were only recorded after a successful send, a POST
    that reaches ntfy but errors on the response would both deliver the
    alert and record nothing, letting the next cron tick re-alert.
    """

    def boom_send_text(topic, title, body, priority, session=None):
        raise RuntimeError("simulated ntfy outage")

    monkeypatch.setattr(cli, "send_text", boom_send_text)
    break_apple(monkeypatch)

    state_path = tmp_path / "state.json"
    cli.main(["--state", str(state_path)])

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["_health"]["last_error_notified"]


def test_total_missing_pairs_alerts_and_exits_nonzero(
    monkeypatch, sent, health_alerts, tmp_path
):
    """Every watched pair vanishing at once must not read as 'no stock'.

    That is what a rotated part number looks like, and staying quiet here
    is the permanent-silence failure this whole project exists to prevent.
    """
    from applewatch.models import Availability

    monkeypatch.setattr(
        cli,
        "fetch_availability",
        lambda parts, location: [Availability("MJX94AH/A", "R706", "unavailable", "")],
    )

    code = cli.main(["--state", str(tmp_path / "state.json")])

    assert code != 0
    assert len(health_alerts) == 1


def test_dry_run_with_force_notify_sends_nothing(monkeypatch, sent, tmp_path):
    """--dry-run must win over --force-notify, not the other way round.

    Without a topic set, letting --force-notify through would have POSTed
    to the public https://ntfy.sh/None.
    """
    code = cli.main(
        ["--dry-run", "--force-notify", "--state", str(tmp_path / "state.json")]
    )
    assert code != 0
    assert sent == []


def test_health_alert_fires_again_after_six_hours(
    monkeypatch, sent, health_alerts, tmp_path
):
    """The rate limit must release, not just suppress.

    A three-runs-in-a-row test alone can't catch a units bug (e.g. dividing
    by 60 instead of 3600): it would still pass, and its failure mode is
    silence forever. This proves the alert fires again once the interval
    has actually elapsed.
    """
    from datetime import datetime, timedelta, timezone

    state_path = tmp_path / "state.json"
    stale = datetime.now(timezone.utc) - timedelta(hours=7)
    state_path.write_text(
        json.dumps(
            {"_health": {"last_error_notified": stale.isoformat(), "last_error": "old"}}
        ),
        encoding="utf-8",
    )

    break_apple(monkeypatch)
    cli.main(["--state", str(state_path)])

    assert len(health_alerts) == 1


def test_successful_run_clears_health(monkeypatch, sent, health_alerts, tmp_path):
    """A recovered checker must not stay rate-limited by a resolved outage.

    Otherwise a fresh outage a couple of hours after a recovery stays
    suppressed for the rest of the 6-hour window because _health was never
    cleared.
    """
    state_path = tmp_path / "state.json"

    break_apple(monkeypatch)
    cli.main(["--state", str(state_path)])

    stub_apple(monkeypatch, "pickup_unavailable")
    cli.main(["--state", str(state_path)])

    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert "_health" not in after


# --- Fix round 2 -------------------------------------------------------
#
# A re-reviewer reproduced two of the fix-round-1 findings by probe rather
# than by reading the diff: a third, unwrapped ntfy call site at
# --force-notify, and a rate-limit test that only exercised the
# suppress-at-t=0 case. See the task-8 fix report for the full writeup.


def test_force_notify_failure_does_not_leak_topic(monkeypatch, sent, tmp_path, capsys):
    """The --force-notify send call is a third ntfy call site.

    It was the one left unwrapped by fix round 1: probing it directly
    (monkeypatching cli.send to raise a raw, URL-bearing HTTPError, exactly
    like a real ntfy failure would before notify.py sanitized it) proved
    the topic still reached stdout/stderr through this path.
    """
    import requests

    sentinel = "SUPER-SECRET-TOPIC-123"
    monkeypatch.setenv("NTFY_TOPIC", sentinel)

    def boom_send(topic, event, session=None):
        raise requests.HTTPError(
            f"403 Client Error: Forbidden for url: https://ntfy.sh/{sentinel}"
        )

    monkeypatch.setattr(cli, "send", boom_send)

    cli.main(["--force-notify", "--state", str(tmp_path / "state.json")])

    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err


def test_partial_missing_pair_alerts_and_exits_nonzero(
    monkeypatch, sent, health_alerts, tmp_path
):
    """A partial miss must not stay silent, only a total miss used to alert.

    Scenario from the review: the owner widens to two colours, Apple
    rotates only one part number, and the actual target goes permanently
    unwatched with a stderr-only warning and a green (exit 0) run. Any
    missing watched pair -- not just every one of them -- must route
    through the same rate-limited health alert.
    """
    from applewatch.models import Availability

    # The default watches.yml resolves to two pairs: (MJXC4AH/A, R706) and
    # (MJXC4AH/A, R595). Report only one of them.
    monkeypatch.setattr(
        cli,
        "fetch_availability",
        lambda parts, location: [Availability("MJXC4AH/A", "R706", "unavailable", "")],
    )

    code = cli.main(["--state", str(tmp_path / "state.json")])

    assert code != 0
    assert len(health_alerts) == 1


def test_corrupt_state_file_alerts_and_exits_nonzero(
    monkeypatch, sent, health_alerts, tmp_path
):
    """Any unexpected exception, not just ConfigError/AppleError, must
    route through the health alert rather than escape main() as a bare
    traceback the owner may not be watching CI for.

    A corrupt state.json (JSONDecodeError from load_state) is one of the
    concretely reachable cases: a broken commit, a manual edit gone wrong,
    or a race with a concurrent Actions run.
    """
    state_path = tmp_path / "state.json"
    state_path.write_text("{not valid json", encoding="utf-8")
    stub_apple(monkeypatch, "pickup_unavailable")

    code = cli.main(["--state", str(state_path)])

    assert code != 0
    assert len(health_alerts) == 1
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["_health"]["last_error"] == "JSONDecodeError"


def test_unexpected_exception_message_is_not_written_to_state(
    monkeypatch, sent, tmp_path
):
    """_report_broken must receive only the exception's type name, never
    str(error): an arbitrary exception message could contain anything, and
    it is written into the public state.json as last_error.
    """

    def boom_diff(*args, **kwargs):
        raise RuntimeError("super-secret-topic-abc-should-not-leak")

    monkeypatch.setattr(cli, "diff", boom_diff)
    stub_apple(monkeypatch, "pickup_unavailable")

    state_path = tmp_path / "state.json"
    code = cli.main(["--state", str(state_path)])

    assert code != 0
    after = state_path.read_text(encoding="utf-8")
    assert "super-secret-topic-abc-should-not-leak" not in after
    assert json.loads(after)["_health"]["last_error"] == "RuntimeError"


def test_malformed_health_timestamp_does_not_crash_handler(
    monkeypatch, sent, health_alerts, tmp_path, capsys
):
    """An unparseable last_error_notified must not crash _report_broken.

    _report_broken runs inside main()'s blanket exception handler in the
    failure case that matters most. If datetime.fromisoformat(last) were
    left unguarded, an unparseable stored value would raise from inside the
    handler that exists specifically to keep exception text out of the
    public state.json and Actions log: Python's chained-exception output
    would print the ORIGINAL error's message straight to stderr, with no
    health alert and no clean return code.
    """
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "_health": {
                    "last_error_notified": "not-a-timestamp",
                    "last_error": "old",
                }
            }
        ),
        encoding="utf-8",
    )
    break_apple(monkeypatch)

    code = cli.main(["--state", str(state_path)])

    assert code != 0
    assert len(health_alerts) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "During handling of the above exception" not in captured.err


def test_naive_health_timestamp_does_not_crash_handler(
    monkeypatch, sent, health_alerts, tmp_path, capsys
):
    """A naive stored timestamp compared against the aware `now` raises
    TypeError, not ValueError -- both must be caught."""
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "_health": {
                    "last_error_notified": "2026-09-21T08:00:00",
                    "last_error": "old",
                }
            }
        ),
        encoding="utf-8",
    )
    break_apple(monkeypatch)

    code = cli.main(["--state", str(state_path)])

    assert code != 0
    assert len(health_alerts) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err


def test_heartbeat_written_once_per_day(monkeypatch, sent, tmp_path):
    """The heartbeat must be a once-daily marker, not a once-per-run one.

    diff() rebuilds the state dict from scratch every call, so writing
    _heartbeat unconditionally means ~288 commits/day and makes the
    workflow's "no state change" branch dead code.
    """
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_unavailable")

    cli.main(["--state", str(state_path)])
    first = json.loads(state_path.read_text(encoding="utf-8"))["_heartbeat"]

    cli.main(["--state", str(state_path)])
    second = json.loads(state_path.read_text(encoding="utf-8"))["_heartbeat"]

    assert second == first


def test_heartbeat_updates_on_a_later_date(monkeypatch, sent, tmp_path):
    """A heartbeat from an earlier date must be refreshed, not carried
    forward forever."""
    state_path = tmp_path / "state.json"
    stub_apple(monkeypatch, "pickup_unavailable")

    stale_heartbeat = "2020-01-01T00:00:00+04:00"
    state_path.write_text(
        json.dumps({"_heartbeat": stale_heartbeat}), encoding="utf-8"
    )

    cli.main(["--state", str(state_path)])

    updated = json.loads(state_path.read_text(encoding="utf-8"))["_heartbeat"]
    assert updated != stale_heartbeat


def test_health_alert_suppressed_ten_minutes_after_previous(
    monkeypatch, sent, health_alerts, tmp_path
):
    """A short backdate must stay suppressed -- the discriminating case.

    An inflating units bug (dividing by 60 instead of 3600) makes `due`
    MORE often true, so neither a 7-hours-ago backdate (fires again, as
    expected either way) nor a near-zero-elapsed case proves the division
    is correct. Ten minutes is small enough that only the correct /3600
    computes an elapsed_hours under the 6-hour threshold; an accidental
    /60 would compute 10 (minutes-as-hours) and fire here too.

    Verified to actually catch the bug it targets: temporarily changing
    cli.py's `/ 3600` to `/ 60` makes this test fail (health_alerts grows
    to 1), confirming it is not a vacuously-passing test. See the fix
    report for the transcript.
    """
    from datetime import datetime, timedelta, timezone

    state_path = tmp_path / "state.json"
    ten_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    state_path.write_text(
        json.dumps(
            {
                "_health": {
                    "last_error_notified": ten_minutes_ago.isoformat(),
                    "last_error": "old",
                }
            }
        ),
        encoding="utf-8",
    )

    break_apple(monkeypatch)
    cli.main(["--state", str(state_path)])

    assert len(health_alerts) == 0
