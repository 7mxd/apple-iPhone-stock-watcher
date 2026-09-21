import json
from pathlib import Path

import pytest

from applewatch.apple import (
    AppleError,
    build_params,
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
