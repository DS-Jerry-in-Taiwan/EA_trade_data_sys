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
from service.infrastructure.mt5.session import AccountSessionTransition


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


class ObserveCalendarEvidenceService:
    """Read-only MT5 probe boundary used by the ``observe`` mode.

    Probe results are retained for diagnostics, but are never converted into
    an authoritative snapshot or an ``open``/``closed`` classification.  This
    lets operators validate the bridge while the broker evidence source is
    still being prepared.
    """

    authoritative = False

    def __init__(self, probe: Callable[[str], Mapping] | None = None,
                 generation_provider: Callable[[], int | None] | None = None):
        self.probe = probe
        self.generation_provider = generation_provider
        self.observations: list[dict] = []
        self._cycle_generation = None
        self._cycle_cache: dict[str, Mapping | None] = {}

    def begin_cycle(self, session_generation=None):
        """Start one worker cycle and clear the observation cache.

        A schedule probe is relatively expensive and must not be repeated for
        every gap in the same refresh.  The generation is retained only as a
        diagnostic guard; a new session always starts a fresh cache.
        """
        self._cycle_generation = session_generation
        self._cycle_cache = {}

    def check_generation(self, session_generation=None):
        """Stop a cycle when the terminal account/session changes."""
        if (session_generation is not None and self._cycle_generation is not None
                and session_generation != self._cycle_generation):
            raise AccountSessionTransition(
                "MT5 account session changed during calendar observation"
            )

    def classify_gap(self, symbol, start, end, timeframe):
        start, end = normalize_utc(start, "start"), normalize_utc(end, "end")
        record = {"symbol": symbol, "timeframe": timeframe, "status": "unavailable"}
        if self.generation_provider is not None:
            self.check_generation(self.generation_provider())
        if symbol in self._cycle_cache:
            result = self._cycle_cache[symbol]
            if isinstance(result, Mapping):
                record.update(status="observed", source_symbol=result.get("source_symbol"),
                              revision=result.get("revision"), utc_windows=result.get("utc_windows"))
            else:
                record["status"] = "error"
        elif self.probe is not None:
            try:
                if self.generation_provider is not None:
                    self.check_generation(self.generation_provider())
                result = self.probe(symbol)
                self._cycle_cache[symbol] = result if isinstance(result, Mapping) else None
                if isinstance(result, Mapping):
                    # Keep diagnostics bounded and avoid carrying arbitrary
                    # bridge objects across the worker boundary.
                    record["status"] = "observed"
                    record["source_symbol"] = result.get("source_symbol")
                    record["revision"] = result.get("revision")
                    record["utc_windows"] = result.get("utc_windows")
                else:
                    record["status"] = "invalid"
            except AccountSessionTransition:
                raise
            except Exception:
                record["status"] = "error"
                self._cycle_cache[symbol] = None
        self.observations.append(record)
        return CalendarObservation(
            state="unknown", effective_from=start, effective_until=end,
            source="mt5:observe", revision="observation-only",
            reason="calendar observe mode is non-authoritative",
        )


def compose_calendar_service(*, mode="enforce", coordinator=None,
                             session_provider=None, probe=None,
                             generation_provider=None):
    """Return the history boundary for a configured refresh coordinator.

    The composition root may supply a :class:`CalendarRefreshCoordinator`
    and a session identity callback once authoritative providers are enabled.
    In every other case the returned service is the explicit unknown adapter;
    this keeps production defaults fail-closed and avoids guessed schedules.
    """
    if mode == "observe":
        return ObserveCalendarEvidenceService(probe, generation_provider)
    if mode == "disabled":
        return None
    if coordinator is None or not callable(session_provider):
        return UnknownCalendar()
    return RefreshBackedCalendarEvidenceService(coordinator, session_provider)


__all__ = ["CalendarEvidenceService", "ObserveCalendarEvidenceService",
           "RefreshBackedCalendarEvidenceService", "compose_calendar_service"]
