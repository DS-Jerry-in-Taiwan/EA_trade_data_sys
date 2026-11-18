"""Deep calendar boundary consumed by the history worker.

History owns gap verification and persistence of bars, not calendar provider
selection, refresh, retries, or snapshot storage.  This protocol is the only
calendar knowledge that crosses into the worker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping, Protocol

from service.infrastructure.mt5.calendar import CalendarObservation
from service.infrastructure.mt5.calendar import normalize_utc


class CalendarEvidenceService(Protocol):
    """Classify one history gap using session-bound calendar evidence."""

    def classify_gap(
        self, symbol: str, start: datetime, end: datetime, timeframe: str
    ) -> CalendarObservation:
        ...


class RefreshBackedCalendarEvidenceService:
    """History-facing adapter over the persisted refresh coordinator.

    The worker never sees providers or snapshot storage.  A session supplier
    is evaluated for every gap so an account switch cannot reuse an old
    snapshot.  Missing, stale, incomplete, or uncovered windows remain
    ``unknown`` and therefore fail closed in the worker.
    """

    def __init__(self, coordinator, session_provider: Callable[[], Mapping]):
        self.coordinator = coordinator
        self.session_provider = session_provider

    def classify_gap(self, symbol, start, end, timeframe):
        start, end = normalize_utc(start, "start"), normalize_utc(end, "end")
        session = self.session_provider()
        snapshot = self.coordinator.snapshot(session=session)
        if snapshot is None or symbol not in snapshot.symbols:
            return CalendarObservation(
                state="unknown", effective_from=start, effective_until=end,
                source="unavailable", revision="unknown",
                reason="calendar snapshot unavailable",
            )
        item = snapshot.symbols[symbol]
        windows = item.get("utc_windows", [])
        covered = False
        for window in windows:
            try:
                lower = normalize_utc(window["from"], "window.from")
                upper = normalize_utc(window["until"], "window.until")
            except (KeyError, TypeError, ValueError):
                continue
            if lower <= start and end <= upper:
                covered = True
                break
        state = item.get("session_state")
        if state not in {"open", "closed"} or not covered:
            state = "unknown"
            reason = "calendar interval is not covered by evidence"
        else:
            reason = "calendar snapshot evidence"
        return CalendarObservation(
            state=state, effective_from=start, effective_until=end,
            source=snapshot.source, revision=snapshot.revision, reason=reason,
        )


__all__ = ["CalendarEvidenceService", "RefreshBackedCalendarEvidenceService"]
