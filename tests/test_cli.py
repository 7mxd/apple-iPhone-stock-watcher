import json
from pathlib import Path

import pytest

from applewatch import cli

FIXTURES = Path(__file__).parent / "fixtures"


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
