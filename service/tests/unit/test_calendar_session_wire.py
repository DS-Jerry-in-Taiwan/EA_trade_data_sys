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
    calendar_from_mapping,
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


def test_calendar_from_mapping_preserves_all_configured_rules(wire):
    calendar = calendar_from_mapping({"enabled": True, "complete": True, **wire["calendar"]})
    assert isinstance(calendar, BrokerSessionCalendarAdapter)
    config = wire["calendar"]
    observation = calendar.classify_gap(
        "XAUUSDm",
        datetime.fromisoformat("2026-11-23T12:10:00+00:00"),
        datetime.fromisoformat("2026-11-23T12:50:00+00:00"),
        "M5",
    )
    assert observation.reason == "daily_break"
    assert observation.source == config["source"]
    assert observation.revision == config["revision"]

    holiday = calendar.classify_gap(
        "GBPUSDm",
        datetime.fromisoformat("2026-11-26T09:00:00+00:00"),
        datetime.fromisoformat("2026-11-26T12:00:00+00:00"),
        "H1",
    )
    assert holiday.reason == "holiday"

    early_close = calendar.classify_gap(
        "XAUUSDm",
        datetime.fromisoformat("2026-11-27T19:00:00+00:00"),
        datetime.fromisoformat("2026-11-27T23:00:00+00:00"),
        "M15",
    )
    assert early_close.reason == "early_close"


@pytest.mark.parametrize("field,value", [
    ("daily_breaks", [["not-a-time", "13:00:00"]]),
    ("holidays", ["not-a-date"]),
    ("early_closes", {"2026-11-27": "not-a-time"}),
])
def test_calendar_from_mapping_rejects_malformed_rules(wire, field, value):
    raw = {"enabled": True, "complete": True, **wire["calendar"], field: value}
    assert isinstance(calendar_from_mapping(raw), UnknownCalendar)
