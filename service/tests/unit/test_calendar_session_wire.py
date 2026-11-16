"""Synthetic broker-calendar and history/session metadata wire fixtures."""

import copy
import json
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from service.contracts.history_session_metadata import (
    MetadataContractError,
    validate_history_session_metadata,
)
from service.infrastructure.mt5.calendar import (
    BrokerSessionCalendarAdapter,
    UnknownCalendar,
)


@pytest.fixture
def wire():
    path = Path(__file__).parents[2] / "contracts" / "fixtures" / "calendar_session_wire.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _calendar(wire):
    config = wire["calendar"]
    return BrokerSessionCalendarAdapter(
        source=config["source"],
        revision=config["revision"],
        daily_breaks=[
            (time.fromisoformat(start), time.fromisoformat(end))
            for start, end in config["daily_breaks"]
        ],
        holidays=[date.fromisoformat(day) for day in config["holidays"]],
        early_closes={
            date.fromisoformat(day): time.fromisoformat(close)
            for day, close in config["early_closes"].items()
        },
    )


@pytest.mark.parametrize(
    "name",
    ["daily_break", "weekend", "holiday", "early_close", "open"],
)
def test_calendar_observation_wire_cases(wire, name):
    case = wire["observations"][name]
    observation = _calendar(wire).classify_gap(
        case["symbol"],
        datetime.fromisoformat(case["start"].replace("Z", "+00:00")),
        datetime.fromisoformat(case["end"].replace("Z", "+00:00")),
        case["timeframe"],
    )
    assert observation.state == case["state"]
    assert observation.reason == case["reason"]
    assert observation.source == wire["calendar"]["source"]
    assert observation.revision == wire["calendar"]["revision"]
    assert observation.effective_from.isoformat().replace("+00:00", "Z") == case["effective_from"]
    assert observation.effective_until.isoformat().replace("+00:00", "Z") == case["effective_until"]
    assert observation.covers(
        datetime.fromisoformat(case["start"].replace("Z", "+00:00")),
        datetime.fromisoformat(case["end"].replace("Z", "+00:00")),
    )


def test_unknown_calendar_wire_is_explicit_and_fail_closed(wire):
    case = wire["observations"]["unknown"]
    start = datetime.fromisoformat(case["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(case["end"].replace("Z", "+00:00"))
    observation = UnknownCalendar().classify_gap(case["symbol"], start, end, case["timeframe"])
    assert observation.state == "unknown"
    assert observation.reason == case["reason"]
    assert observation.source == "unavailable"
    assert observation.revision == "unknown"
    assert observation.covers(start, end)

    metadata = validate_history_session_metadata(wire["metadata"]["unknown"])
    assert metadata["history"]["session_state"] == "unknown"
    assert metadata["history"]["gaps"][0]["classification"] == "unknown"
    assert metadata["session"]["ready"] is False


@pytest.mark.parametrize("name", ["closed", "open", "unknown"])
def test_session_metadata_wire_states_validate(wire, name):
    metadata = validate_history_session_metadata(wire["metadata"][name])
    assert metadata["history"]["session_state"] == name
    assert metadata["session"]["ready"] is (name == "open")
    assert metadata["history"]["gaps"][0]["classification"] in {
        "expected_session_break", "cache_backfill_gap", "unknown",
    }


def test_gap_classification_unknown_values_fail_closed(wire):
    payload = copy.deepcopy(wire["metadata"]["open"])
    payload["history"]["gaps"][0]["classification"] = wire["rejected"]["unknown_gap_classification"]
    with pytest.raises(MetadataContractError):
        validate_history_session_metadata(payload)


def test_unknown_session_state_value_fails_closed(wire):
    payload = copy.deepcopy(wire["metadata"]["unknown"])
    payload["history"]["session_state"] = wire["rejected"]["unknown_calendar_state"]
    with pytest.raises(MetadataContractError):
        validate_history_session_metadata(payload)
