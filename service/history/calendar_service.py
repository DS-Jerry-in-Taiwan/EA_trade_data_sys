"""Deep calendar boundary consumed by the history worker.

History owns gap verification and persistence of bars, not calendar provider
selection, refresh, retries, or snapshot storage.  This protocol is the only
calendar knowledge that crosses into the worker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping, Protocol

from service.infrastructure.mt5.calendar import CalendarObservation
from service.infrastructure.mt5.calendar import UnknownCalendar, normalize_utc


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


def compose_calendar_service(*, coordinator=None, session_provider=None):
    """Return the history boundary for a configured refresh coordinator.

    The composition root may supply a :class:`CalendarRefreshCoordinator`
    and a session identity callback once authoritative providers are enabled.
    In every other case the returned service is the explicit unknown adapter;
    this keeps production defaults fail-closed and avoids guessed schedules.
    """
    if coordinator is None or not callable(session_provider):
        return UnknownCalendar()
    return RefreshBackedCalendarEvidenceService(coordinator, session_provider)


__all__ = ["CalendarEvidenceService", "RefreshBackedCalendarEvidenceService", "compose_calendar_service"]
