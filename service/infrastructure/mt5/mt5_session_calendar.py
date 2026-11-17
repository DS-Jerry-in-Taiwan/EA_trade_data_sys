"""Read-only MT5 session calendar source."""
from __future__ import annotations
from datetime import datetime
from typing import Callable
from .calendar import CalendarObservation, UnknownCalendar, normalize_utc

class MT5SessionCalendarAdapter:
    """Adapt optional MQL5/RPyC session evidence to the calendar seam."""
    def __init__(self, mt5_client=None, symbol_resolver=None, *, source="mt5:mql5-session", revision="unknown", session_provider: Callable | None = None):
        self.mt5_client, self.symbol_resolver = mt5_client, symbol_resolver
        self.source, self.revision, self.session_provider = source, revision, session_provider
        self._unknown = UnknownCalendar()
    def _broker_symbol(self, symbol):
        resolver = self.symbol_resolver
        if resolver:
            for name in ("resolve", "resolve_symbol", "broker_symbol"):
                fn = getattr(resolver, name, None)
                if callable(fn):
                    try:
                        value = fn(symbol)
                        if isinstance(value, str) and value.strip(): return value
                    except Exception: pass
        return symbol
    def classify_gap(self, symbol: str, start: datetime, end: datetime, timeframe: str):
        start, end = normalize_utc(start, "start"), normalize_utc(end, "end")
        broker_symbol = self._broker_symbol(symbol)
        provider = self.session_provider
        if provider is not None:
            try:
                result = provider(broker_symbol, start, end, timeframe)
                if isinstance(result, CalendarObservation): return result
                if isinstance(result, dict): return CalendarObservation(**result)
            except Exception: pass
        mt5 = getattr(self.mt5_client, "mt5", None)
        classifier = getattr(mt5, "classify_gap", None)
        if callable(classifier):
            try:
                result = classifier(broker_symbol, start, end, timeframe)
                if isinstance(result, CalendarObservation): return result
                if isinstance(result, dict): return CalendarObservation(**result)
            except Exception: pass
        return self._unknown.classify_gap(symbol, start, end, timeframe)

__all__ = ["MT5SessionCalendarAdapter"]
