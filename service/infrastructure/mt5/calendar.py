"""Broker session calendar seam used when qualifying history gaps.

The MT5 terminal does not expose a portable calendar contract.  This module
keeps that concern behind a small, read-only adapter boundary.  Calendar
answers are deliberately evidence carrying: callers can only treat a gap as
closed when the answer includes a source, revision, and UTC effective window.
Unknown answers are never converted into an open or closed assertion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Iterable, Literal, Mapping, Protocol


CalendarState = Literal["open", "closed", "unknown"]


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware UTC datetime")
    result = value.astimezone(timezone.utc)
    return result


@dataclass(frozen=True)
class CalendarObservation:
    """A calendar result for an effective UTC interval.

    ``reason`` is intentionally descriptive (for example ``weekend``,
    ``daily_break``, ``holiday`` or ``early_close``); consumers must branch on
    ``state`` and retain the evidence fields when publishing metadata.
    """

    state: CalendarState
    effective_from: datetime
    effective_until: datetime
    source: str
    revision: str
    reason: str

    def __post_init__(self) -> None:
        if self.state not in {"open", "closed", "unknown"}:
            raise ValueError("unknown calendar state")
        start = _utc(self.effective_from, "effective_from")
        end = _utc(self.effective_until, "effective_until")
        if end <= start:
            raise ValueError("calendar effective window must be positive")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("calendar source is required")
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("calendar revision is required")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("calendar reason is required")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_until", end)

    @property
    def effective_start(self) -> datetime:
        """Alias used by adapters that call the lower bound ``start``."""

        return self.effective_from

    @property
    def effective_end(self) -> datetime:
        return self.effective_until

    @property
    def effective_utc_window(self) -> tuple[datetime, datetime]:
        return self.effective_from, self.effective_until

    def covers(self, start: datetime, end: datetime) -> bool:
        start, end = _utc(start, "start"), _utc(end, "end")
        return self.effective_from <= start and end <= self.effective_until


class BrokerSessionCalendar(Protocol):
    """Read-only seam implemented by a broker calendar provider."""

    def classify_gap(
        self, symbol: str, start: datetime, end: datetime, timeframe: str
    ) -> CalendarObservation:
        ...


class UnknownCalendar:
    """Fail-closed adapter for deployments without calendar evidence."""

    def classify_gap(self, symbol, start, end, timeframe):
        start, end = _utc(start, "start"), _utc(end, "end")
        return CalendarObservation(
            state="unknown",
            effective_from=start,
            effective_until=end,
            source="unavailable",
            revision="unknown",
            reason="calendar evidence unavailable",
        )


class BrokerSessionCalendarAdapter:
    """Small rule based adapter useful as a broker supplied implementation.

    Rules are expressed in UTC and are intentionally explicit.  ``daily_breaks``
    contains ``(start, end)`` clock times, ``holidays`` contains UTC dates, and
    ``early_closes`` maps a UTC date to the close clock time.  A gap wholly in
    one of these windows is ``closed``; other gaps are ``open`` under this
    authoritative schedule.  Deployments without a complete schedule should
    inject :class:`UnknownCalendar` instead.
    """

    def __init__(
        self,
        *,
        source: str,
        revision: str,
        daily_breaks: Iterable[tuple[clock_time, clock_time]] = (),
        holidays: Iterable[date] = (),
        early_closes: Mapping[date, clock_time] | None = None,
    ):
        self.source = source
        self.revision = revision
        self.daily_breaks = tuple(daily_breaks)
        self.holidays = frozenset(holidays)
        self.early_closes = dict(early_closes or {})

    def _window(self, day: date, start: clock_time, end: clock_time):
        lower = datetime.combine(day, start, tzinfo=timezone.utc)
        upper_day = day if end > start else day + timedelta(days=1)
        upper = datetime.combine(upper_day, end, tzinfo=timezone.utc)
        return lower, upper

    def classify_gap(self, symbol, start, end, timeframe):
        start, end = _utc(start, "start"), _utc(end, "end")
        if end <= start:
            raise ValueError("calendar gap end must be after start")
        days = {start.date() + timedelta(days=i) for i in range((end.date() - start.date()).days + 1)}
        candidates = []
        for day in days:
            if day.weekday() >= 5:
                candidates.append((datetime.combine(day, clock_time.min, tzinfo=timezone.utc),
                                   datetime.combine(day + timedelta(days=1), clock_time.min, tzinfo=timezone.utc),
                                   "weekend"))
            if day in self.holidays:
                candidates.append((datetime.combine(day, clock_time.min, tzinfo=timezone.utc),
                                   datetime.combine(day + timedelta(days=1), clock_time.min, tzinfo=timezone.utc),
                                   "holiday"))
            for break_start, break_end in self.daily_breaks:
                lower, upper = self._window(day, break_start, break_end)
                candidates.append((lower, upper, "daily_break"))
            if day in self.early_closes:
                lower = datetime.combine(day, self.early_closes[day], tzinfo=timezone.utc)
                upper = datetime.combine(day + timedelta(days=1), clock_time.min, tzinfo=timezone.utc)
                candidates.append((lower, upper, "early_close"))
        for lower, upper, reason in candidates:
            if lower <= start and end <= upper:
                return CalendarObservation(
                    state="closed", effective_from=lower, effective_until=upper,
                    source=self.source, revision=self.revision, reason=reason,
                )
        return CalendarObservation(
            state="open", effective_from=start, effective_until=end,
            source=self.source, revision=self.revision, reason="scheduled_open",
        )


# Short alias retained for callers that do not need the session qualifier.
BrokerCalendarAdapter = BrokerSessionCalendarAdapter


def calendar_from_mapping(raw: Mapping | None) -> BrokerSessionCalendar | UnknownCalendar:
    """Build a calendar adapter from non-secret typed deployment settings."""
    if not isinstance(raw, Mapping) or not raw.get("enabled", False) or not raw.get("complete", False):
        return UnknownCalendar()
    source = raw.get("source")
    revision = raw.get("revision")
    if not isinstance(source, str) or not source.strip() or not isinstance(revision, str) or not revision.strip():
        return UnknownCalendar()
    daily_breaks = raw.get("daily_breaks", ())
    holidays = raw.get("holidays", ())
    if not isinstance(daily_breaks, (list, tuple)) or not isinstance(holidays, (list, tuple)):
        return UnknownCalendar()
    return BrokerSessionCalendarAdapter(
        source=source.strip(), revision=revision.strip(),
        daily_breaks=(), holidays=(), early_closes={},
    )


__all__ = [
    "BrokerSessionCalendar",
    "CalendarObservation",
    "CalendarState",
    "BrokerSessionCalendarAdapter",
    "BrokerCalendarAdapter",
    "UnknownCalendar",
    "calendar_from_mapping",
]
