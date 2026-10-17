"""Deterministic pure time configuration tests: no broker or active policy."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from service.config.models import Settings
from service.infrastructure.mt5.time_policy import TimePolicy, TimePolicyError, normalize_mt5_time

UTC = timezone.utc
NOW = datetime(2026, 10, 12, 1, tzinfo=UTC)


def mapping(**overrides):
    return dict(id="synthetic", version="1", server="synthetic-demo", resource="tick",
                semantic="utc_epoch", unit="milliseconds",
                valid_from="2026-01-01T00:00:00Z", valid_until="2027-01-01T00:00:00Z",
                evidence_source="synthetic fixture", **overrides)


def test_missing_settings_has_no_active_policies():
    assert Settings.from_mapping({}, environ={}).time_policies == ()


def test_config_roundtrip_frozen_and_preserves_precision():
    settings = Settings.from_mapping({"time_policies": [mapping()]}, environ={})
    policy = settings.time_policies[0]
    with pytest.raises(FrozenInstanceError):
        policy.version = "2"
    value = normalize_mt5_time(1791766800123, "tick", "synthetic-demo", policy, now=NOW)
    assert value.utc == datetime(2026, 10, 12, 1, 0, 0, 123000, tzinfo=UTC)
    assert value.raw_value == 1791766800123
    assert value.policy_id == "synthetic"
    assert Settings.from_mapping(settings.as_dict(), environ={}).time_policies == (policy,)


@pytest.mark.parametrize("value", [None, {}, "bad", [None], [{"unexpected": True}]])
def test_malformed_settings_rejected(value):
    with pytest.raises(TimePolicyError):
        Settings.from_mapping({"time_policies": value}, environ={})


def test_explicit_bounded_offset():
    raw = mapping()
    raw.update(semantic="broker_wall_clock", offset_seconds=10800)
    policy = TimePolicy.from_mapping(raw)
    result = normalize_mt5_time(1791777600123, "tick", "synthetic-demo", policy, now=NOW)
    assert result.utc == datetime(2026, 10, 12, 1, 0, 0, 123000, tzinfo=UTC)


@pytest.mark.parametrize("change", [
    {"offset_seconds": True}, {"semantic": "broker_wall_clock"},
    {"timezone": "Etc/UTC"}, {"valid_from": "2026-01-01"},
    {"version": True}, {"unit": "auto"}, {"unknown": "ignored?"},
])
def test_invalid_policy(change):
    raw = mapping()
    raw.update(change)
    with pytest.raises(TimePolicyError):
        TimePolicy.from_mapping(raw)
