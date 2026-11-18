"""Calendar evidence provider contracts and source adapters.

Providers are deliberately small.  They only acquire and normalize source
evidence; snapshot persistence and gap classification remain separate.  A
provider failure is represented by a diagnostic result and never produces an
``open`` schedule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


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
            raise CalendarProviderError(f"mt5_session_provider_error: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise CalendarProviderError("mt5 session response must be an object")
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
                raise CalendarProviderError(f"broker_artifact_unavailable: {exc}") from exc
        if not isinstance(artifact, Mapping):
            raise CalendarProviderError("broker artifact must be an object")
        self.artifact = artifact
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
        try:
            return CalendarEvidence(source=self.source, symbol=logical, revision=self.artifact["revision"], captured_at=self.artifact["captured_at"], valid_from=self.artifact["valid_from"], valid_until=self.artifact["valid_until"], source_symbol=raw["source_symbol"], utc_windows=raw["utc_windows"], session_state=raw.get("session_state", "unknown"))
        except KeyError as exc:
            raise CalendarProviderError(f"broker artifact missing field: {exc.args[0]}") from exc


__all__ = ["BrokerCalendarArtifactProvider", "CalendarEvidence", "CalendarProvider", "CalendarProviderError", "MQL5SessionCalendarProvider", "ProviderFailure"]
