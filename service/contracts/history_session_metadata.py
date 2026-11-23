"""Validation and construction helpers for the history/session wire contract.

This module is deliberately side effect free.  It accepts only the versioned
public fields and raises ``MetadataContractError`` for unknown fields/states;
callers must treat that error as unavailable metadata.
"""

from __future__ import annotations

from collections.abc import Mapping


class MetadataContractError(ValueError):
    """The metadata payload is not safe to expose or consume."""


_ROOT = {"schema_version", "history", "session"}
_HISTORY_REQUIRED = {
    "state", "logical_symbol", "source_symbol", "timeframe", "bars", "minimum_bars",
    "source_exhausted", "session_generation", "session_state", "provenance_id",
    "calendar_revision", "effective_from", "effective_until", "gaps",
}
_HISTORY = _HISTORY_REQUIRED | {"session_fingerprint"}
_SESSION = {"state", "ready", "generation"}
_HISTORY_STATES = {"ready", "insufficient", "error"}
_SESSION_STATES = {"ready", "disconnected", "switch_detected"}
_TIMEFRAMES = {"M5", "M15", "H1", "D1"}
_HISTORY_SESSION_STATES = {"open", "closed", "unknown"}
_GAP_CLASSES = {"expected_session_break", "cache_backfill_gap", "unknown"}


def _object(value, required, allowed, name):
    if (not isinstance(value, Mapping)
            or not required <= set(value)
            or not set(value) <= allowed):
        raise MetadataContractError(f"{name} has unknown or missing fields")


def validate_history_session_metadata(payload: Mapping) -> dict:
    """Return a detached, validated payload or fail closed.

    Only an opaque session fingerprint is accepted; account login, credentials,
    and raw broker account objects remain outside this boundary. ``dict``
    copies prevent callers mutating the object after validation.
    """
    _object(payload, _ROOT, _ROOT, "metadata")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise MetadataContractError("unsupported metadata schema version")
    history = payload["history"]
    session = payload["session"]
    _object(history, _HISTORY_REQUIRED, _HISTORY, "history")
    _object(session, _SESSION, _SESSION, "session")
    if history["state"] not in _HISTORY_STATES:
        raise MetadataContractError("unknown history state")
    if session["state"] not in _SESSION_STATES:
        raise MetadataContractError("unknown session state")
    for key, maximum in (("logical_symbol", 64), ("source_symbol", 128),
                         ("provenance_id", 256), ("calendar_revision", 256)):
        if not isinstance(history[key], str) or not 0 < len(history[key]) <= maximum:
            raise MetadataContractError(f"invalid history {key}")
    if "session_fingerprint" in history:
        value = history["session_fingerprint"]
        if not isinstance(value, str) or not 0 < len(value) <= 256:
            raise MetadataContractError("invalid history session_fingerprint")
    if history["timeframe"] not in _TIMEFRAMES:
        raise MetadataContractError("unknown timeframe")
    for key in ("bars", "minimum_bars", "session_generation"):
        value = history[key]
        minimum = 1 if key in {"minimum_bars", "session_generation"} else 0
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise MetadataContractError(f"invalid history {key}")
    if not isinstance(history["source_exhausted"], bool):
        raise MetadataContractError("invalid source_exhausted")
    if history["session_state"] not in _HISTORY_SESSION_STATES:
        raise MetadataContractError("unknown history session state")
    if history["effective_from"] is not None and not isinstance(history["effective_from"], str):
        raise MetadataContractError("invalid effective_from")
    if history["effective_until"] is not None and not isinstance(history["effective_until"], str):
        raise MetadataContractError("invalid effective_until")
    if not isinstance(history["gaps"], list):
        raise MetadataContractError("invalid history gaps")
    for gap in history["gaps"]:
        if not isinstance(gap, Mapping) or set(gap) != {"start", "end", "session_close", "session_reopen", "classification"}:
            raise MetadataContractError("invalid history gap fields")
        if gap["classification"] not in _GAP_CLASSES:
            raise MetadataContractError("unknown history gap classification")
        for key in ("start", "end"):
            if not isinstance(gap[key], str):
                raise MetadataContractError(f"invalid gap {key}")
        for key in ("session_close", "session_reopen"):
            if gap[key] is not None and not isinstance(gap[key], str):
                raise MetadataContractError(f"invalid gap {key}")
    if not isinstance(session["ready"], bool):
        raise MetadataContractError("invalid session ready")
    generation = session["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise MetadataContractError("invalid session generation")
    if history["session_generation"] != generation:
        raise MetadataContractError("history and session generations differ")
    if session["state"] == "ready" and not session["ready"]:
        raise MetadataContractError("ready session must be marked ready")
    if session["state"] != "ready" and session["ready"]:
        raise MetadataContractError("non-ready session cannot be marked ready")
    return {"schema_version": 1, "history": dict(history), "session": dict(session)}


__all__ = ["MetadataContractError", "validate_history_session_metadata"]
