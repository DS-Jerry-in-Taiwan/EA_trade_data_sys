"""Build session-bound snapshots from the independent calendar providers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .calendar_providers import CalendarProviderError
from .calendar_snapshot import CalendarSnapshot, CalendarSnapshotError


class CalendarSnapshotBuilder:
    """Compose complete per-symbol evidence without exposing providers upstream."""

    def __init__(self, mt5_provider, broker_provider, *, symbols):
        self.mt5_provider = mt5_provider
        self.broker_provider = broker_provider
        self.symbols = tuple(symbols)

    @staticmethod
    def _identity(session: Mapping[str, Any]):
        fingerprint = session.get("fingerprint")
        if isinstance(fingerprint, Mapping):
            fingerprint = fingerprint.get("id")
        epoch = session.get("epoch") or session.get("boot_epoch")
        generation = session.get("generation")
        if not isinstance(fingerprint, str) or not fingerprint or not isinstance(epoch, str) or not epoch:
            raise CalendarSnapshotError("calendar session identity is incomplete")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise CalendarSnapshotError("calendar session generation is invalid")
        return fingerprint, generation, epoch

    def fetch(self, *, session: Mapping[str, Any]) -> CalendarSnapshot:
        fingerprint, generation, epoch = self._identity(session)
        if not self.symbols:
            raise CalendarSnapshotError("calendar symbols are required")
        evidence = {}
        captured = []
        valid_from = []
        valid_until = []
        revisions = []
        sources = []
        for symbol in self.symbols:
            try:
                mt5 = self.mt5_provider.fetch(symbol)
                broker = self.broker_provider.fetch(symbol)
            except Exception as exc:
                # Never expose adapter exception text (it can contain broker
                # URLs, local paths, or credentials) in status/wire metadata.
                raise CalendarProviderError(f"calendar provider failed for {symbol}") from exc
            if mt5.symbol != broker.symbol or mt5.source_symbol != broker.source_symbol:
                raise CalendarProviderError(f"calendar provider conflict for {symbol}")
            if tuple(mt5.utc_windows) != tuple(broker.utc_windows):
                raise CalendarProviderError(f"calendar windows conflict for {symbol}")
            if mt5.session_state == "unknown" or broker.session_state == "unknown":
                raise CalendarProviderError(f"calendar state unknown for {symbol}")
            evidence[symbol] = {
                "source_symbol": mt5.source_symbol,
                "utc_windows": list(mt5.utc_windows),
                "session_state": mt5.session_state,
            }
            captured.extend((mt5.captured_at, broker.captured_at))
            valid_from.extend((mt5.valid_from, broker.valid_from))
            valid_until.extend((mt5.valid_until, broker.valid_until))
            revisions.extend((mt5.revision, broker.revision))
            sources.extend((mt5.source, broker.source))
        now = datetime.now(timezone.utc)
        return CalendarSnapshot(
            schema_version="1",
            snapshot_id=f"calendar-{now.isoformat()}",
            source="+".join(sorted(set(sources))),
            revision="+".join(sorted(set(revisions))),
            captured_at=max(captured),
            valid_from=max(valid_from),
            valid_until=min(valid_until),
            session_fingerprint=fingerprint,
            session_generation=generation,
            session_epoch=epoch,
            symbols=evidence,
        )


__all__ = ["CalendarSnapshotBuilder"]
