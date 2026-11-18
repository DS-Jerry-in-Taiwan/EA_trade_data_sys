"""Read-only MT5 trading-session schedule adapter.

The MetaTrader Python bridge exposes session windows as seconds from midnight
in terminal/server time.  This module is the narrow adapter that converts
those values into the UTC window contract used by the calendar providers.  It
never infers a timezone: callers must supply a verified server-to-UTC offset.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping

from .calendar_providers import CalendarProviderError


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CalendarProviderError("session clock must be timezone-aware")
    return value.astimezone(timezone.utc)


class MT5SessionScheduleReader:
    """Adapt ``symbol_info_session_trade/quote`` from an MT5 bridge.

    ``server_utc_offset_seconds`` is deliberately mandatory.  MT5 session
    values do not carry a timezone and silently treating terminal time as UTC
    would create unsafe calendar evidence around DST boundaries.
    """

    def __init__(
        self,
        mt5: Any,
        *,
        server_utc_offset_seconds: int | Callable[[], int] | None,
        revision: str,
        clock: Callable[[], datetime] | None = None,
        days: int = 7,
        max_sessions_per_day: int = 32,
    ) -> None:
        self.mt5 = mt5
        self.offset = server_utc_offset_seconds
        self.revision = revision
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.days = days
        self.max_sessions_per_day = max_sessions_per_day
        if not isinstance(revision, str) or not revision.strip():
            raise CalendarProviderError("mt5 session revision is required")
        if days < 1 or days > 7 or max_sessions_per_day < 1:
            raise ValueError("invalid session schedule bounds")

    def _offset(self) -> timedelta:
        value = self.offset() if callable(self.offset) else self.offset
        if not isinstance(value, int) or isinstance(value, bool) or abs(value) > 24 * 3600:
            raise CalendarProviderError("mt5 server timezone offset is unavailable")
        return timedelta(seconds=value)

    @staticmethod
    def _seconds(value: Any, field: str) -> int:
        if isinstance(value, time):
            return value.hour * 3600 + value.minute * 60 + value.second
        if isinstance(value, timedelta):
            value = value.total_seconds()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = int(value)
            if 0 <= value < 86400:
                return value
        raise CalendarProviderError(f"mt5 session {field} is invalid")

    def _read_kind(self, method_name: str, symbol: str, first_day: date, offset: timedelta) -> list[dict[str, str]]:
        method = getattr(self.mt5, method_name, None)
        if not callable(method):
            raise CalendarProviderError(f"mt5 {method_name} is unavailable")
        windows: list[dict[str, str]] = []
        for day in range(self.days):
            for index in range(self.max_sessions_per_day):
                try:
                    item = method(symbol, day, index)
                except Exception as exc:
                    raise CalendarProviderError(f"mt5 {method_name} failed") from exc
                if item is None:
                    break
                if isinstance(item, Mapping):
                    start, end = item.get("from"), item.get("to")
                else:
                    start, end = getattr(item, "from", None), getattr(item, "to", None)
                start_s, end_s = self._seconds(start, "from"), self._seconds(end, "to")
                start_dt = datetime.combine(first_day + timedelta(days=day), time.min) + timedelta(seconds=start_s)
                end_day = day + (1 if end_s <= start_s else 0)
                end_dt = datetime.combine(first_day + timedelta(days=end_day), time.min) + timedelta(seconds=end_s)
                start_utc = (start_dt - offset).replace(tzinfo=timezone.utc)
                end_utc = (end_dt - offset).replace(tzinfo=timezone.utc)
                windows.append({
                    "from": start_utc.isoformat().replace("+00:00", "Z"),
                    "until": end_utc.isoformat().replace("+00:00", "Z"),
                })
        return windows

    def fetch(self, symbol: str) -> dict[str, Any]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise CalendarProviderError("mt5 source symbol is required")
        offset = self._offset()
        captured = _utc(self.clock())
        first_day = captured.date() - timedelta(days=captured.weekday())
        trade = self._read_kind("symbol_info_session_trade", symbol, first_day, offset)
        quote = self._read_kind("symbol_info_session_quote", symbol, first_day, offset)
        if not trade or not quote:
            raise CalendarProviderError("mt5 session schedule is empty")
        if trade != quote:
            raise CalendarProviderError("mt5 trade and quote sessions conflict")
        valid_from = datetime.combine(first_day, time.min, tzinfo=timezone.utc)
        return {
            "source_symbol": symbol,
            "revision": self.revision,
            "captured_at": captured,
            "valid_from": valid_from,
            "valid_until": valid_from + timedelta(days=self.days),
            "utc_windows": trade,
            "session_state": "open" if any(w["from"] <= captured.isoformat().replace("+00:00", "Z") < w["until"] for w in trade) else "closed",
        }


__all__ = ["MT5SessionScheduleReader"]
