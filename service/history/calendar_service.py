"""Deep calendar boundary consumed by the history worker.

History owns gap verification and persistence of bars, not calendar provider
selection, refresh, retries, or snapshot storage.  This protocol is the only
calendar knowledge that crosses into the worker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from service.infrastructure.mt5.calendar import CalendarObservation


class CalendarEvidenceService(Protocol):
    """Classify one history gap using session-bound calendar evidence."""

    def classify_gap(
        self, symbol: str, start: datetime, end: datetime, timeframe: str
    ) -> CalendarObservation:
        ...


__all__ = ["CalendarEvidenceService"]
