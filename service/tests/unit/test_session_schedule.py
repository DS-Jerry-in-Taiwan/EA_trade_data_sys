from datetime import datetime, timezone

import pytest

from service.infrastructure.mt5.calendar_providers import CalendarProviderError
from service.infrastructure.mt5.session_schedule import MT5SessionScheduleReader


class FakeMT5:
    def __init__(self, *, mismatch=False):
        self.mismatch = mismatch

    def symbol_info_session_trade(self, symbol, day, index):
        if index:
            return None
        return {"from": 9 * 3600, "to": (10 * 3600 if not self.mismatch else 11 * 3600)}

    def symbol_info_session_quote(self, symbol, day, index):
        if index:
            return None
        return {"from": 9 * 3600, "to": 10 * 3600}


def test_reader_converts_terminal_sessions_to_utc():
    reader = MT5SessionScheduleReader(
        FakeMT5(), server_utc_offset_seconds=2 * 3600, revision="terminal-r1",
        clock=lambda: datetime(2026, 11, 18, 12, tzinfo=timezone.utc), days=1,
    )
    raw = reader.fetch("EURUSD.sim")
    assert raw["source_symbol"] == "EURUSD.sim"
    assert raw["utc_windows"] == [{"from": "2026-11-15T07:00:00Z", "until": "2026-11-15T08:00:00Z"}]
    assert raw["session_state"] == "closed"


def test_reader_uses_mt5_sunday_zero_enum_not_python_weekday():
    reader = MT5SessionScheduleReader(
        FakeMT5(), server_utc_offset_seconds=0, revision="r",
        clock=lambda: datetime(2026, 11, 18, tzinfo=timezone.utc), days=7,
    )
    windows = reader.fetch("EURUSD.sim")["utc_windows"]
    assert windows[0]["from"].startswith("2026-11-15T")  # Sunday=0
    assert windows[1]["from"].startswith("2026-11-16T")  # Monday=1


def test_reader_rejects_missing_timezone_offset():
    with pytest.raises(CalendarProviderError, match="timezone offset"):
        MT5SessionScheduleReader(FakeMT5(), server_utc_offset_seconds=None, revision="r").fetch("BTCUSD.sim")


def test_reader_accepts_independent_trade_and_quote_sessions():
    reader = MT5SessionScheduleReader(
        FakeMT5(mismatch=True), server_utc_offset_seconds=0, revision="r",
        clock=lambda: datetime(2026, 11, 18, tzinfo=timezone.utc), days=1,
    )
    raw = reader.fetch("BTCUSD.sim")
    # Quote sessions are queried for observability, but trade windows remain
    # the calendar contract.  Different quote hours are normal at brokers.
    assert raw["utc_windows"] == [{"from": "2026-11-15T09:00:00Z", "until": "2026-11-15T11:00:00Z"}]


def test_reader_anchors_week_in_server_local_date():
    # At 23:30 UTC on Saturday the +2h terminal is already Sunday.  The
    # first MT5 day must therefore be the current Sunday, not the preceding
    # UTC Sunday.
    reader = MT5SessionScheduleReader(
        FakeMT5(), server_utc_offset_seconds=2 * 3600, revision="r",
        clock=lambda: datetime(2026, 11, 14, 23, 30, tzinfo=timezone.utc), days=1,
    )
    windows = reader.fetch("EURUSD.sim")["utc_windows"]
    assert windows[0]["from"].startswith("2026-11-15T07:")


def test_reader_rejects_missing_bridge_method():
    with pytest.raises(CalendarProviderError, match="unavailable"):
        MT5SessionScheduleReader(object(), server_utc_offset_seconds=0, revision="r").fetch("BTCUSD.sim")
