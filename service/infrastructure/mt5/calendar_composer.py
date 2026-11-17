"""Composition of MT5 sessions and broker calendar exceptions."""
from __future__ import annotations
from datetime import datetime
from .calendar import BrokerSessionCalendar, CalendarObservation, UnknownCalendar, normalize_utc

class CalendarEvidenceComposer:
    """Combine sources and remain unknown when evidence is missing/conflicting."""
    def __init__(self, mt5_sessions: BrokerSessionCalendar | None = None, broker_calendar: BrokerSessionCalendar | None = None):
        self.mt5_sessions, self.broker_calendar = mt5_sessions or UnknownCalendar(), broker_calendar or UnknownCalendar()
    def classify_gap(self, symbol: str, start: datetime, end: datetime, timeframe: str) -> CalendarObservation:
        start, end = normalize_utc(start, "start"), normalize_utc(end, "end")
        mt5 = self.mt5_sessions.classify_gap(symbol, start, end, timeframe)
        broker = self.broker_calendar.classify_gap(symbol, start, end, timeframe)
        source, revision = f"{mt5.source}+{broker.source}", f"{mt5.revision}+{broker.revision}"
        if mt5.state == "unknown" or broker.state == "unknown":
            return CalendarObservation(state="unknown", effective_from=start, effective_until=end, source=source, revision=revision, reason="calendar evidence incomplete")
        if mt5.state != broker.state:
            return CalendarObservation(state="unknown", effective_from=start, effective_until=end, source=source, revision=revision, reason="calendar evidence conflict")
        return CalendarObservation(state=mt5.state, effective_from=start, effective_until=end, source=source, revision=revision, reason=f"{mt5.reason}; {broker.reason}")

__all__ = ["CalendarEvidenceComposer"]
