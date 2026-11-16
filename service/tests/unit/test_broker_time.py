from datetime import datetime, timezone

import pytest

from service.infrastructure.time import (
    BrokerTimeSettings, BrokerTimeWindow, normalize_mt5_epoch, normalize_mt5_wire, parse_schedule,
)


SCHEDULE = BrokerTimeSettings(
    timezone="FTMO/OANDA MetaTrader",
    source="official schedule fixture",
    schedule=(
        BrokerTimeWindow(datetime(2026, 3, 29), datetime(2026, 11, 2), 10800),
        BrokerTimeWindow(datetime(2026, 11, 2), datetime(2027, 3, 28), 7200),
    ),
)


def test_utc_policy_preserves_epoch_for_legacy_unit_inputs():
    value = normalize_mt5_epoch(1700000000, BrokerTimeSettings("UTC"))
    assert value == value.replace(tzinfo=timezone.utc)
    assert normalize_mt5_wire(1700000000, BrokerTimeSettings("UTC")) == "2023-11-14T22:13:20Z"


def test_gmt3_window_converts_broker_wall_clock_to_utc():
    # 2026-06-01 12:00 broker wall clock is 09:00 UTC in GMT+3.
    value = datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp()
    assert normalize_mt5_wire(value, SCHEDULE) == "2026-06-01T09:00:00Z"


def test_gmt2_window_converts_after_platform_switch():
    value = datetime(2026, 11, 16, 12, tzinfo=timezone.utc).timestamp()
    assert normalize_mt5_wire(value, SCHEDULE) == "2026-11-16T10:00:00Z"


@pytest.mark.parametrize("value", [
    datetime(2026, 3, 28, 12, tzinfo=timezone.utc).timestamp(),
    datetime(2027, 3, 28, 12, tzinfo=timezone.utc).timestamp(),
])
def test_unknown_or_stale_schedule_fails_closed(value):
    with pytest.raises(ValueError, match="outside.*schedule"):
        normalize_mt5_epoch(value, SCHEDULE)


def test_overlapping_schedule_is_rejected():
    with pytest.raises(ValueError, match="overlap"):
        parse_schedule([
            {"valid_from": "2026-01-01T00:00:00", "valid_until": "2026-06-01T00:00:00", "offset_seconds": 7200},
            {"valid_from": "2026-05-01T00:00:00", "valid_until": "2026-12-01T00:00:00", "offset_seconds": 10800},
        ])


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_invalid_mt5_timestamp_fails_closed(value):
    with pytest.raises(ValueError):
        normalize_mt5_epoch(value)
