"""Durable, fail-closed calendar evidence snapshots.

The snapshot is the boundary between calendar providers and history.  Providers
may be unavailable while the process is running; history must still have a
deterministic answer and must never use an expired or differently-bound
schedule.  This module therefore contains only validation and atomic
persistence.  It does not fetch a broker or MT5 calendar.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .calendar import normalize_utc


class CalendarSnapshotError(ValueError):
    """The persisted evidence does not satisfy the snapshot contract."""


def _timestamp(value: datetime | str, name: str) -> datetime:
    try:
        return normalize_utc(value, name)
    except (TypeError, ValueError) as exc:
        raise CalendarSnapshotError(str(exc)) from exc


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalendarSnapshotError(f"{name} is required")
    return value.strip()


@dataclass(frozen=True)
class CalendarSnapshot:
    """A complete schedule snapshot bound to one MT5 account session."""

    schema_version: str
    snapshot_id: str
    source: str
    revision: str
    captured_at: datetime
    valid_from: datetime
    valid_until: datetime
    session_fingerprint: str
    session_generation: int
    session_epoch: str
    symbols: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version"))
        object.__setattr__(self, "snapshot_id", _text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "source", _text(self.source, "source"))
        object.__setattr__(self, "revision", _text(self.revision, "revision"))
        object.__setattr__(self, "session_fingerprint", _text(self.session_fingerprint, "session_fingerprint"))
        object.__setattr__(self, "session_epoch", _text(self.session_epoch, "session_epoch"))
        for field in ("captured_at", "valid_from", "valid_until"):
            object.__setattr__(self, field, _timestamp(getattr(self, field), field))
        if self.valid_until <= self.valid_from:
            raise CalendarSnapshotError("valid_until must be after valid_from")
        if self.captured_at > self.valid_until:
            raise CalendarSnapshotError("captured_at cannot be after valid_until")
        if isinstance(self.session_generation, bool) or not isinstance(self.session_generation, int) or self.session_generation < 1:
            raise CalendarSnapshotError("session_generation must be a positive integer")
        if not isinstance(self.symbols, Mapping) or not self.symbols:
            raise CalendarSnapshotError("symbols must be a non-empty mapping")
        normalized_symbols: dict[str, dict[str, Any]] = {}
        for logical, evidence in self.symbols.items():
            _text(logical, "symbol")
            if not isinstance(evidence, Mapping):
                raise CalendarSnapshotError(f"symbol evidence for {logical} must be an object")
            _text(evidence.get("source_symbol"), f"{logical}.source_symbol")
            windows = evidence.get("utc_windows")
            if not isinstance(windows, list) or not windows:
                raise CalendarSnapshotError(f"{logical}.utc_windows must be a non-empty list")
            normalized_windows = []
            for window in windows:
                if not isinstance(window, Mapping) or set(window) != {"from", "until"}:
                    raise CalendarSnapshotError(f"{logical}.utc_windows has invalid fields")
                start, end = _timestamp(window["from"], "window.from"), _timestamp(window["until"], "window.until")
                if end <= start:
                    raise CalendarSnapshotError(f"{logical}.utc_windows must be positive")
                normalized_windows.append({"from": start.isoformat().replace("+00:00", "Z"), "until": end.isoformat().replace("+00:00", "Z")})
            item = dict(evidence)
            item["utc_windows"] = normalized_windows
            normalized_symbols[logical] = item
        object.__setattr__(self, "symbols", normalized_symbols)

    def to_dict(self) -> dict[str, Any]:
        def iso(value: datetime) -> str:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        symbols = {}
        for logical, evidence in self.symbols.items():
            item = dict(evidence)
            item["utc_windows"] = [{"from": iso(_timestamp(w["from"], "window.from")), "until": iso(_timestamp(w["until"], "window.until"))} for w in evidence["utc_windows"]]
            symbols[logical] = item
        return {"schema_version": self.schema_version, "snapshot_id": self.snapshot_id,
                "source": self.source, "revision": self.revision, "captured_at": iso(self.captured_at),
                "valid_from": iso(self.valid_from), "valid_until": iso(self.valid_until),
                "session_fingerprint": self.session_fingerprint, "session_generation": self.session_generation,
                "session_epoch": self.session_epoch, "symbols": symbols}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CalendarSnapshot":
        if not isinstance(raw, Mapping):
            raise CalendarSnapshotError("snapshot must be an object")
        required = {"schema_version", "snapshot_id", "source", "revision", "captured_at", "valid_from", "valid_until", "session_fingerprint", "session_generation", "session_epoch", "symbols"}
        if set(raw) != required:
            raise CalendarSnapshotError("snapshot fields do not match contract")
        return cls(**dict(raw))

    def is_valid(self, *, now: datetime | None = None, session: Mapping[str, Any] | None = None) -> bool:
        instant = _timestamp(now or datetime.now(timezone.utc), "now")
        if not self.valid_from <= instant <= self.valid_until:
            return False
        if session is not None:
            fingerprint = (session.get("fingerprint") or {}).get("id") if isinstance(session.get("fingerprint"), Mapping) else session.get("fingerprint")
            if fingerprint != self.session_fingerprint or session.get("generation") != self.session_generation or session.get("epoch") != self.session_epoch:
                return False
        return True


class CalendarSnapshotStore:
    """Atomic JSON store.  Missing, invalid, stale or mismatched data is not usable."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: CalendarSnapshot) -> None:
        if not isinstance(snapshot, CalendarSnapshot):
            raise TypeError("snapshot must be a CalendarSnapshot")
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(snapshot.to_dict(), handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> CalendarSnapshot:
        try:
            with self.path.open(encoding="utf-8") as handle:
                return CalendarSnapshot.from_dict(json.load(handle))
        except FileNotFoundError as exc:
            raise CalendarSnapshotError("calendar snapshot is missing") from exc
        except json.JSONDecodeError as exc:
            raise CalendarSnapshotError("calendar snapshot is not valid JSON") from exc

    def load_valid(self, *, now: datetime | None = None, session: Mapping[str, Any] | None = None) -> CalendarSnapshot | None:
        try:
            snapshot = self.load()
        except CalendarSnapshotError:
            return None
        return snapshot if snapshot.is_valid(now=now, session=session) else None


CalendarEvidenceSnapshot = CalendarSnapshot

__all__ = ["CalendarSnapshot", "CalendarEvidenceSnapshot", "CalendarSnapshotError", "CalendarSnapshotStore"]
