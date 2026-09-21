import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from applewatch.apple import (
    AppleError,
    build_params,
    fetch_availability,
    parse_pickup_response,
)

FIXTURES = Path(__file__).parent / "fixtures"
TARGET = "MJXC4AH/A"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_build_params_indexes_each_part():
    params = build_params(["MJXC4AH/A", "MJX94AH/A"], "Abu Dhabi")
    assert params["parts.0"] == "MJXC4AH/A"
    assert params["parts.1"] == "MJX94AH/A"
    assert params["location"] == "Abu Dhabi"
    assert params["pl"] == "true"
    assert params["mts.0"] == "regular"


def test_parses_all_five_stores():
    observations = parse_pickup_response(fixture("pickup_unavailable"))
    assert {o.store_number for o in observations} == {
        "R706",
        "R595",
        "R596",
        "R597",
        "R785",
    }


def test_real_unavailable_payload_yields_no_hits():
    observations = parse_pickup_response(fixture("pickup_unavailable"))
    assert all(o.is_hit is False for o in observations)
    assert all(o.quote == "Currently unavailable" for o in observations)


def test_real_ineligible_payload_is_not_a_hit():
    observations = parse_pickup_response(fixture("pickup_ineligible"))
    dubai_mall = next(o for o in observations if o.store_number == "R597")
    assert dubai_mall.pickup_display == "ineligible"
    assert dubai_mall.is_hit is False


def test_synthetic_available_payload_yields_one_hit():
    observations = parse_pickup_response(fixture("pickup_available_synthetic"))
    hits = [o for o in observations if o.is_hit]
    assert len(hits) == 1
    assert hits[0].store_number == "R595"
    assert hits[0].part_number == TARGET
    assert hits[0].quote == "Available Wed 23 Sep"


def test_non_200_body_status_raises():
    payload = {"head": {"status": "500"}, "body": {}}
    with pytest.raises(AppleError):
        parse_pickup_response(payload)


def test_missing_stores_key_raises():
    with pytest.raises(AppleError):
        parse_pickup_response({"head": {"status": "200"}, "body": {}})


def test_missing_pickup_display_key_raises():
    """An absent pickupDisplay key must never be scored as a hit.

    parts.get("pickupDisplay", "") would default to "", which is not in the
    denylist and so reads as in stock. If Apple renamed the field, every
    watched pair would fire an urgent false in-stock alert and lock state to
    "hit" forever. A missing key is a response-shape change and must raise,
    not silently default.
    """
    payload = {
        "head": {"status": "200"},
        "body": {
            "stores": [
                {
                    "storeNumber": "R595",
                    "partsAvailability": {
                        TARGET: {"pickupSearchQuote": "Currently unavailable"}
                    },
                }
            ]
        },
    }
    with pytest.raises(AppleError) as exc:
        parse_pickup_response(payload)
    assert TARGET in str(exc.value)
    assert "R595" in str(exc.value)


def test_unrecognised_non_empty_value_is_still_a_hit():
    """An unrecognised VALUE is still treated as stock, by design.

    Only an absent key is untrustworthy (see the test above); a present but
    unfamiliar value must keep counting as a hit so an unanticipated
    positive string from Apple is never silently dropped.
    """
    payload = {
        "head": {"status": "200"},
        "body": {
            "stores": [
                {
                    "storeNumber": "R595",
                    "partsAvailability": {
                        TARGET: {
                            "pickupDisplay": "wibble",
                            "pickupSearchQuote": "Some new state",
                        }
                    },
                }
            ]
        },
    }
    observations = parse_pickup_response(payload)
    assert len(observations) == 1
    assert observations[0].is_hit is True
    assert observations[0].pickup_display == "wibble"


def test_fetch_availability_retries_on_transient_failure(monkeypatch):
    """Transient failure on first attempt, success on second."""
    monkeypatch.setattr("applewatch.apple.time.sleep", lambda s: None)

    call_count = 0

    def get_mock(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise requests.RequestException("Transient network error")
        # Second call succeeds
        response = Mock()
        response.raise_for_status = Mock()
        response.json = Mock(return_value=fixture("pickup_unavailable"))
        return response

    session = Mock()
    session.get = get_mock

    result = fetch_availability(["MJXC4AH/A"], "Abu Dhabi", session=session)

    assert call_count == 2
    assert len(result) > 0  # Got valid response
    assert all(not o.is_hit for o in result)


def test_fetch_availability_raises_after_exhausting_retries(monkeypatch):
    """All attempts fail, should raise AppleError."""
    monkeypatch.setattr("applewatch.apple.time.sleep", lambda s: None)

    call_count = 0

    def get_mock(url, **kwargs):
        nonlocal call_count
        call_count += 1
        raise requests.RequestException("Network error")

    session = Mock()
    session.get = get_mock

    with pytest.raises(AppleError) as exc_info:
        fetch_availability(["MJXC4AH/A"], "Abu Dhabi", session=session)

    assert call_count == 3  # MAX_ATTEMPTS
    assert "failed after 3 attempts" in str(exc_info.value)


def test_fetch_availability_raises_on_bad_status_after_retries(monkeypatch):
    """Response with non-200 body status on every attempt raises AppleError."""
    monkeypatch.setattr("applewatch.apple.time.sleep", lambda s: None)

    call_count = 0

    def get_mock(url, **kwargs):
        nonlocal call_count
        call_count += 1
        response = Mock()
        response.raise_for_status = Mock()
        response.json = Mock(return_value={"head": {"status": "500"}, "body": {}})
        return response

    session = Mock()
    session.get = get_mock

    with pytest.raises(AppleError) as exc_info:
        fetch_availability(["MJXC4AH/A"], "Abu Dhabi", session=session)

    assert call_count == 3  # MAX_ATTEMPTS
    assert "failed after 3 attempts" in str(exc_info.value)
