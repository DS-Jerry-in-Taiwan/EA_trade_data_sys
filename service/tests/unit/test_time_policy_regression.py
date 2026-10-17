"""Independent fake-only regression tests for explicit MT5 timestamp policies."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from service.infrastructure.mt5.time_policy import (
    TimePolicy, TimePolicyError, normalize_mt5_time,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 12, 1, tzinfo=UTC)


def policy(**changes):
    values = dict(id="fake-policy", version="1", server="fake-demo",
                  resource="tick", semantic="utc_epoch", unit="seconds",
                  valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                  valid_until=datetime(2027, 1, 1, tzinfo=UTC),
                  evidence_source="fake test fixture")
    values.update(changes)
    return TimePolicy(**values)


def normalize(raw, selected=None, **kwargs):
    return normalize_mt5_time(raw, "tick", "fake-demo", selected or policy(),
                              now=kwargs.pop("now", NOW), **kwargs)


@pytest.mark.parametrize("unit,multiplier", [("seconds", 1), ("milliseconds", 1000)])
def test_units_preserve_instant_and_provenance(unit, multiplier):
    raw = Decimal(str(NOW.timestamp())) * multiplier + Decimal("0.123") * multiplier
    actual = normalize(raw, policy(unit=unit))
    assert actual.utc == NOW.replace(microsecond=123000)
    assert actual.raw_value == raw
    assert (actual.raw_unit, actual.policy_id, actual.policy_version) == (unit, "fake-policy", "1")
    assert (actual.semantic, actual.server, actual.resource) == ("utc_epoch", "fake-demo", "tick")


def test_positive_and_negative_offsets():
    for offset in (10800, -18000, 0):
        selected = policy(semantic="broker_wall_clock", offset_seconds=offset)
        assert normalize(NOW.timestamp() + offset, selected).utc == NOW


def test_iana_timezone_respects_summer_and_winter():
    selected = policy(semantic="broker_wall_clock", timezone="Europe/Berlin")
    for month, offset in ((1, 3600), (7, 7200)):
        expected = datetime(2026, month, 15, 12, tzinfo=UTC)
        assert normalize(expected.timestamp() + offset, selected).utc == expected


@pytest.mark.parametrize("wall", [datetime(2026, 3, 29, 2, 30, tzinfo=UTC),
                                  datetime(2026, 10, 25, 2, 30, tzinfo=UTC)])
def test_dst_gap_and_fold_rejected(wall):
    with pytest.raises(TimePolicyError):
        normalize(wall.timestamp(), policy(semantic="broker_wall_clock", timezone="Europe/Berlin"))


@pytest.mark.parametrize("raw", [True, False, "1791766800", None, [], {},
                                  float("nan"), float("inf"), float("-inf"),
                                  Decimal("NaN"), Decimal("Infinity"), -1, 10**40,
                                  Decimal("1791766800.0000001")])
def test_untrusted_numbers_rejected(raw):
    with pytest.raises(TimePolicyError):
        normalize(raw)


@pytest.mark.parametrize("scope", [{"server": "other"}, {"resource": "deal"}])
def test_scope_not_rebound(scope):
    with pytest.raises(TimePolicyError):
        normalize(NOW.timestamp(), policy(**scope))


def test_unknown_semantic_and_double_conversion_rejected():
    with pytest.raises(TimePolicyError):
        normalize(NOW.timestamp(), policy(semantic="unknown"))
    with pytest.raises(TimePolicyError):
        normalize(normalize(NOW.timestamp()))


def test_future_and_stale_values_are_not_clamped():
    for delta in (-86400, 10800):
        actual = normalize(NOW.timestamp() + delta)
        assert actual.utc.timestamp() == NOW.timestamp() + delta
        assert actual.utc != NOW


@pytest.mark.parametrize("boundary,accepted", [("start", True), ("end", False)])
def test_timestamp_and_evaluation_validity_are_half_open(boundary, accepted):
    selected = policy()
    instant = selected.valid_from if boundary == "start" else selected.valid_until
    if accepted:
        assert normalize(instant.timestamp(), selected).utc == instant
        assert normalize(NOW.timestamp(), selected, now=instant).utc == NOW
    else:
        with pytest.raises(TimePolicyError):
            normalize(instant.timestamp(), selected)
        with pytest.raises(TimePolicyError):
            normalize(NOW.timestamp(), selected, now=instant)


@pytest.mark.parametrize("changes", [
    {"id": ""}, {"version": 1}, {"version": " "}, {"server": " fake"},
    {"evidence_source": ""}, {"resource": "all"}, {"unit": "auto"},
    {"semantic": "infer"}, {"semantic": []}, {"unit": {}},
    {"valid_from": "2026-01-01"},
    {"valid_until": "2025-01-01T00:00:00Z"},
    {"timezone": "UTC"}, {"offset_seconds": 0},
    {"semantic": "broker_wall_clock"},
    {"semantic": "broker_wall_clock", "timezone": "UTC", "offset_seconds": 0},
    {"semantic": "broker_wall_clock", "timezone": "Invalid/Fake"},
    {"semantic": "broker_wall_clock", "offset_seconds": True},
    {"semantic": "broker_wall_clock", "offset_seconds": 86400},
])
def test_invalid_policy_rejected(changes):
    with pytest.raises(TimePolicyError):
        policy(**changes)


def test_mapping_strict_and_policy_immutable():
    selected = policy()
    values = dict(vars(selected))
    assert TimePolicy.from_mapping(values) == selected
    for malformed in ({**values, "auto_detect": True}, {"id": "partial"}, [], None):
        with pytest.raises(TimePolicyError):
            TimePolicy.from_mapping(malformed)
    with pytest.raises(FrozenInstanceError):
        selected.version = "2"
    assert normalize(NOW.timestamp(), replace(selected, version="2")).policy_version == "2"
