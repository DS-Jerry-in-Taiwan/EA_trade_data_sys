"""Refresh coordination for persisted calendar evidence.

The coordinator is deliberately independent from History Worker.  Providers
return a validated :class:`CalendarSnapshot`; this module decides when a
snapshot may be used, persists it atomically, and limits failed refreshes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Mapping, Protocol

from .calendar import normalize_utc
from .calendar_snapshot import CalendarSnapshot, CalendarSnapshotError, CalendarSnapshotStore


class CalendarSnapshotProvider(Protocol):
    def fetch(self, *, session: Mapping[str, Any]) -> CalendarSnapshot:
        ...


@dataclass(frozen=True)
class CalendarRefreshStatus:
    """Operational state without exposing credentials or account identifiers."""

    state: str
    reason: str
    attempted_at: datetime | None = None
    next_retry_at: datetime | None = None
    symbols: tuple[str, ...] = ()


class CalendarRefreshCoordinator:
    """Single-flight, bounded-backoff owner of calendar snapshot refreshes.

    ``snapshot()`` is the only read path needed by callers.  It returns a
    snapshot only when it is current and bound to the supplied session.  A
    failed refresh never makes an expired snapshot valid and is retried only
    after the bounded backoff interval.
    """

    def __init__(
        self,
        store: CalendarSnapshotStore,
        provider: CalendarSnapshotProvider | Callable[..., CalendarSnapshot],
        *,
        clock: Callable[[], datetime] | None = None,
        base_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 900.0,
    ) -> None:
        if base_backoff_seconds <= 0 or max_backoff_seconds < base_backoff_seconds:
            raise ValueError("invalid refresh backoff bounds")
        self.store = store
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.base_backoff_seconds = float(base_backoff_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        self._lock = Lock()
        self._failure_count = 0
        self._status = CalendarRefreshStatus("unknown", "snapshot unavailable")

    def _now(self) -> datetime:
        return normalize_utc(self.clock(), "now")

    @staticmethod
    def _session_fingerprint(session: Mapping[str, Any]) -> str | None:
        value = session.get("fingerprint")
        if isinstance(value, Mapping):
            value = value.get("id")
        return value if isinstance(value, str) and value else None

    def status(self) -> CalendarRefreshStatus:
        return self._status

    def snapshot(self, *, session: Mapping[str, Any]) -> CalendarSnapshot | None:
        """Return only a valid snapshot; trigger one bounded refresh if needed."""
        now = self._now()
        current = self.store.load_valid(now=now, session=session)
        if current is not None:
            self._status = CalendarRefreshStatus("valid", "snapshot is current", symbols=tuple(current.symbols))
            return current
        result = self.refresh(session=session)
        if result.state != "valid":
            return None
        return self.store.load_valid(now=self._now(), session=session)

    def refresh(self, *, session: Mapping[str, Any], force: bool = False) -> CalendarRefreshStatus:
        now = self._now()
        with self._lock:
            if not force and self._status.next_retry_at is not None and now < self._status.next_retry_at:
                self._status = CalendarRefreshStatus(
                    "backoff", "refresh backoff active", self._status.attempted_at,
                    self._status.next_retry_at, self._status.symbols,
                )
                return self._status
            attempted = now
            try:
                fetch = getattr(self.provider, "fetch", None)
                if not callable(fetch):
                    fetch = self.provider
                snapshot = fetch(session=session)
                if not isinstance(snapshot, CalendarSnapshot):
                    raise CalendarSnapshotError("provider did not return CalendarSnapshot")
                if not snapshot.is_valid(now=now, session=session):
                    raise CalendarSnapshotError("provider returned stale or session-mismatched snapshot")
                self.store.save(snapshot)
            except Exception as exc:
                self._failure_count += 1
                delay = min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** (self._failure_count - 1)))
                next_retry = now + timedelta(seconds=delay)
                self._status = CalendarRefreshStatus("error", str(exc), attempted, next_retry)
                return self._status
            self._failure_count = 0
            self._status = CalendarRefreshStatus("valid", "snapshot refreshed", attempted, None, tuple(snapshot.symbols))
            return self._status


__all__ = ["CalendarSnapshotProvider", "CalendarRefreshStatus", "CalendarRefreshCoordinator"]
