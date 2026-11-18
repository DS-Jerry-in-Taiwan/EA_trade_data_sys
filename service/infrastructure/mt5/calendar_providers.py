"""Calendar evidence provider contracts and source adapters.

Providers are deliberately small.  They only acquire and normalize source
evidence; snapshot persistence and gap classification remain separate.  A
provider failure is represented by a diagnostic result and never produces an
``open`` schedule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .calendar_snapshot import CalendarSnapshot


class CalendarProviderError(ValueError):
    """Source evidence is absent, malformed, or internally inconsistent."""


def _utc(value: datetime | str, name: str) -> datetime:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise CalendarProviderError(f"{name} is not an ISO-8601 timestamp") from exc
    if not isinstance(value, datetime):
        raise CalendarProviderError(f"{name} must be a datetime or ISO-8601 string")
    if value.tzinfo is None:
        raise CalendarProviderError(f"{name} must include a timezone")
    return value.astimezone(timezone.utc)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalendarProviderError(f"{name} is required")
    return value.strip()


@dataclass(frozen=True)
class CalendarEvidence:
    """Normalized evidence from one source for one logical symbol."""

    source: str
    revision: str
    captured_at: datetime
    valid_from: datetime
    valid_until: datetime
    symbol: str
    source_symbol: str
    utc_windows: tuple[dict[str, str], ...]
    session_state: str = "unknown"
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("source", "revision", "symbol", "source_symbol"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        times = {}
        for name in ("captured_at", "valid_from", "valid_until"):
            times[name] = _utc(getattr(self, name), name)
            object.__setattr__(self, name, times[name])
        if times["valid_until"] <= times["valid_from"]:
            raise CalendarProviderError("valid_until must be after valid_from")
        if not isinstance(self.utc_windows, (list, tuple)) or not self.utc_windows:
            raise CalendarProviderError("utc_windows must be non-empty")
        normalized: list[dict[str, str]] = []
        for index, window in enumerate(self.utc_windows):
            if not isinstance(window, Mapping) or set(window) != {"from", "until"}:
                raise CalendarProviderError(f"utc_windows[{index}] has invalid fields")
            start, end = _utc(window["from"], f"utc_windows[{index}].from"), _utc(window["until"], f"utc_windows[{index}].until")
            if end <= start:
                raise CalendarProviderError(f"utc_windows[{index}] must be positive")
            normalized.append({"from": start.isoformat().replace("+00:00", "Z"), "until": end.isoformat().replace("+00:00", "Z")})
        object.__setattr__(self, "utc_windows", tuple(normalized))
        state = self.session_state.strip().lower() if isinstance(self.session_state, str) else "unknown"
        if state not in {"open", "closed", "unknown"}:
            raise CalendarProviderError("session_state must be open, closed, or unknown")
        object.__setattr__(self, "session_state", state)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "revision": self.revision,
            "captured_at": self.captured_at.isoformat().replace("+00:00", "Z"),
            "valid_from": self.valid_from.isoformat().replace("+00:00", "Z"),
            "valid_until": self.valid_until.isoformat().replace("+00:00", "Z"),
            "symbol": self.symbol, "source_symbol": self.source_symbol,
            "utc_windows": list(self.utc_windows), "session_state": self.session_state,
            "diagnostics": list(self.diagnostics),
        }


class CalendarProvider(Protocol):
    def fetch(self, symbol: str) -> CalendarEvidence: ...


@dataclass(frozen=True)
class ProviderFailure:
    source: str
    code: str
    message: str
    symbol: str

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "code": self.code, "message": self.message, "symbol": self.symbol}


class MQL5SessionCalendarProvider:
    """Adapter for an injected MQL5 bridge session provider.

    The bridge callable must return a mapping containing ``source_symbol``,
    ``revision``, ``captured_at``, ``valid_from``, ``valid_until`` and
    ``utc_windows``.  The adapter never invents a schedule when the bridge is
    unavailable.
    """

    source = "mt5:mql5-session"

    def __init__(self, bridge: Callable[[str], Mapping[str, Any]] | None, *, symbol_mapping: Mapping[str, str] | None = None):
        self.bridge = bridge
        self.symbol_mapping = dict(symbol_mapping or {})

    def fetch(self, symbol: str) -> CalendarEvidence:
        logical = _required_text(symbol, "symbol")
        if self.bridge is None:
            raise CalendarProviderError("mt5_session_provider_unavailable")
        try:
            raw = self.bridge(self.symbol_mapping.get(logical, logical))
        except Exception as exc:
            # Do not expose bridge exception text: RPyC/Wine errors may include
            # host paths, connection details, or credentials.  Keep the cause
            # chained for local logs while exposing only a stable diagnostic.
            raise CalendarProviderError("mt5_session_provider_error") from exc
        if not isinstance(raw, Mapping):
            raise CalendarProviderError("mt5 session response must be an object")
        allowed = {"source_symbol", "revision", "captured_at", "valid_from", "valid_until", "utc_windows", "session_state"}
        unknown = set(raw) - allowed
        if unknown:
            raise CalendarProviderError("mt5 session response has unknown fields")
        try:
            return CalendarEvidence(source=self.source, symbol=logical, session_state=raw.get("session_state", "unknown"), **{k: raw[k] for k in ("revision", "captured_at", "valid_from", "valid_until", "utc_windows", "source_symbol")})
        except KeyError as exc:
            raise CalendarProviderError(f"mt5 session missing field: {exc.args[0]}") from exc


class BrokerCalendarArtifactProvider:
    """Read-only adapter for a versioned broker calendar artifact."""

    source = "broker:official-artifact"

    def __init__(self, artifact: Mapping[str, Any] | str | Path):
        if isinstance(artifact, (str, Path)):
            try:
                with Path(artifact).open(encoding="utf-8") as stream:
                    artifact = json.load(stream)
            except (OSError, json.JSONDecodeError) as exc:
                # File paths and parser details are internal and must not cross
                # the service contract boundary.
                raise CalendarProviderError("broker_artifact_unavailable") from exc
        if not isinstance(artifact, Mapping):
            raise CalendarProviderError("broker artifact must be an object")
        expected = {"source", "revision", "captured_at", "valid_from", "valid_until", "symbols"}
        if set(artifact) != expected:
            missing = sorted(expected - set(artifact))
            extra = sorted(set(artifact) - expected)
            detail = f"missing={','.join(missing)}" if missing else f"unknown={','.join(extra)}"
            raise CalendarProviderError(f"broker artifact fields do not match contract ({detail})")
        self.artifact = artifact
        source = _required_text(artifact.get("source"), "source")
        if not source.startswith("broker:"):
            raise CalendarProviderError("source must identify a broker artifact")
        _required_text(artifact.get("revision"), "revision")
        _required_text(artifact.get("captured_at"), "captured_at")
        _required_text(artifact.get("valid_from"), "valid_from")
        _required_text(artifact.get("valid_until"), "valid_until")

    def fetch(self, symbol: str) -> CalendarEvidence:
        logical = _required_text(symbol, "symbol")
        entries = self.artifact.get("symbols")
        if not isinstance(entries, Mapping) or not isinstance(entries.get(logical), Mapping):
            raise CalendarProviderError(f"broker artifact has no coverage for {logical}")
        raw = entries[logical]
        allowed = {"source_symbol", "utc_windows", "session_state"}
        if set(raw) - allowed:
            raise CalendarProviderError(f"broker symbol evidence has unknown fields for {logical}")
        try:
            return CalendarEvidence(source=self.source, symbol=logical, revision=self.artifact["revision"], captured_at=self.artifact["captured_at"], valid_from=self.artifact["valid_from"], valid_until=self.artifact["valid_until"], source_symbol=raw["source_symbol"], utc_windows=raw["utc_windows"], session_state=raw.get("session_state", "unknown"))
        except KeyError as exc:
            raise CalendarProviderError(f"broker artifact missing field: {exc.args[0]}") from exc


class CompositeCalendarSnapshotProvider:
    """Build a session-bound snapshot from MT5 and broker evidence.

    Both sources must cover every configured symbol and agree on broker symbol,
    UTC windows and session state.  Partial coverage or disagreement raises a
    diagnostic error, so callers cannot accidentally persist a partial/open
    schedule.
    """

    def __init__(self, *, symbols: list[str] | tuple[str, ...], mt5: CalendarProvider, broker: CalendarProvider, clock: Callable[[], datetime] | None = None, valid_for_seconds: int = 86400):
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be a non-empty unique sequence")
        if valid_for_seconds <= 0:
            raise ValueError("valid_for_seconds must be positive")
        self.symbols, self.mt5, self.broker = tuple(symbols), mt5, broker
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.valid_for_seconds = valid_for_seconds

    @staticmethod
    def _session_identity(session: Mapping[str, Any]) -> tuple[str, int, str]:
        fingerprint = session.get("fingerprint")
        if isinstance(fingerprint, Mapping):
            fingerprint = fingerprint.get("id")
        generation, epoch = session.get("generation"), session.get("epoch")
        if not isinstance(fingerprint, str) or not fingerprint or not isinstance(generation, int) or isinstance(generation, bool) or generation < 1 or not isinstance(epoch, str) or not epoch:
            raise CalendarProviderError("session identity is incomplete")
        return fingerprint, generation, epoch

    def fetch(self, *, session: Mapping[str, Any]) -> CalendarSnapshot:
        fingerprint, generation, epoch = self._session_identity(session)
        captured = _utc(self.clock(), "captured_at")
        symbols: dict[str, dict[str, Any]] = {}
        revisions: list[str] = []
        sources: list[str] = []
        for logical in self.symbols:
            try:
                mt5, broker = self.mt5.fetch(logical), self.broker.fetch(logical)
            except CalendarProviderError as exc:
                # Provider diagnostics are intentionally collapsed.  The
                # logical symbol is safe and makes the failure actionable;
                # nested exception text is not part of the wire contract.
                raise CalendarProviderError(f"calendar evidence incomplete for {logical}") from exc
            if mt5.source_symbol != broker.source_symbol or mt5.utc_windows != broker.utc_windows or mt5.session_state != broker.session_state:
                raise CalendarProviderError(f"calendar evidence conflict for {logical}")
            symbols[logical] = {"source_symbol": mt5.source_symbol, "utc_windows": list(mt5.utc_windows), "session_state": mt5.session_state, "evidence_sources": [mt5.source, broker.source]}
            revisions.extend((mt5.revision, broker.revision))
            sources.extend((mt5.source, broker.source))
        valid_until = captured.replace(microsecond=0) + timedelta(seconds=self.valid_for_seconds)
        snapshot_id = hashlib.sha256((fingerprint + ":" + str(generation) + ":" + epoch + ":" + captured.isoformat()).encode()).hexdigest()[:24]
        return CalendarSnapshot(schema_version="calendar.snapshot.v1", snapshot_id=snapshot_id, source="+".join(sorted(set(sources))), revision="+".join(sorted(set(revisions))), captured_at=captured, valid_from=captured, valid_until=valid_until, session_fingerprint=fingerprint, session_generation=generation, session_epoch=epoch, symbols=symbols)


__all__ = ["BrokerCalendarArtifactProvider", "CalendarEvidence", "CalendarProvider", "CalendarProviderError", "CompositeCalendarSnapshotProvider", "MQL5SessionCalendarProvider", "ProviderFailure"]
