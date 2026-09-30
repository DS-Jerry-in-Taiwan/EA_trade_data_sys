"""Account-session identity and fail-closed lifecycle state.

The MT5 terminal is allowed to change accounts through its desktop.  This
module records which account was actually observed by ``account_info()`` and
never stores or exposes the account login itself.  Consumers can use the
generation to reject work that spans a terminal-account transition.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


DISCONNECTED = "disconnected"
READY = "ready"
SWITCH_DETECTED = "switch_detected"
UNKNOWN = "unknown"

MT5_DISCONNECTED = "mt5_disconnected"
ACCOUNT_IDENTITY_UNKNOWN = "account_identity_unknown"
ACCOUNT_MODE_UNKNOWN = "account_mode_unknown"
ACCOUNT_SESSION_CHANGED = "account_session_changed"


def _login_hash(login: Any) -> str | None:
    if login is None or isinstance(login, bool):
        return None
    value = str(login).strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _trade_mode(raw: Any, demo_value: Any, real_value: Any) -> str:
    if raw == demo_value:
        return "DEMO"
    if raw == real_value:
        return "REAL"
    if isinstance(raw, str):
        normalized = raw.strip().upper()
        if normalized in {"DEMO", "REAL"}:
            return normalized
    return "UNKNOWN"


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


@dataclass(frozen=True)
class AccountFingerprint:
    """Non-secret identity of the account observed in the terminal."""

    login_hash: str
    server: str
    account_mode: str

    @property
    def token(self) -> str:
        material = f"{self.login_hash}\x00{self.server}\x00{self.account_mode}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.token,
            "login_hash": self.login_hash,
            "server": self.server,
            "account_mode": self.account_mode,
        }


@dataclass(frozen=True)
class AccountSessionStatus:
    state: str
    ready: bool
    generation: int
    fingerprint: dict[str, str] | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self.state,
            "ready": self.ready,
            "generation": self.generation,
            "fingerprint": self.fingerprint,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


class AccountSessionGuard:
    """Track the actual terminal account and fail closed during transitions.

    ``observe_account_info`` is intentionally the only way to establish a
    ready session.  A changed fingerprint enters ``switch_detected`` and must
    be explicitly acknowledged by ``reconcile`` by the later reconciliation
    ticket; it is never silently treated as the old session.
    """

    def __init__(self, *, demo_trade_mode: Any = 0, real_trade_mode: Any = 2):
        self.demo_trade_mode = demo_trade_mode
        self.real_trade_mode = real_trade_mode
        self._state = DISCONNECTED
        self._generation = 0
        self._active: AccountFingerprint | None = None
        self._observed: AccountFingerprint | None = None
        self._error: str | None = MT5_DISCONNECTED
        # The key contains only a hashed login plus server/mode facts. It
        # de-duplicates repeated observations while allowing disconnects to
        # start a new connection generation.
        self._last_observation_key: str | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def state(self) -> str:
        return self._state

    @property
    def ready(self) -> bool:
        return self._state == READY and self._active is not None

    @property
    def fingerprint(self) -> AccountFingerprint | None:
        return self._observed or self._active

    def _status(self) -> AccountSessionStatus:
        return AccountSessionStatus(
            state=self._state,
            ready=self.ready,
            generation=self._generation,
            fingerprint=self.fingerprint.as_dict() if self.fingerprint else None,
            error=self._error,
        )

    def _record_observation(self, key: str) -> None:
        if key != self._last_observation_key:
            self._generation += 1
            self._last_observation_key = key

    def status(self) -> dict[str, Any]:
        """Return stable, non-secret session facts for health/status output."""
        return self._status().as_dict()

    def observe_account_info(
        self,
        account_info: Any,
        *,
        demo_trade_mode: Any | None = None,
        real_trade_mode: Any | None = None,
    ) -> dict[str, Any]:
        """Observe one actual MT5 ``account_info`` result.

        Missing identity fields are unknown and therefore not ready.  Unknown
        trade modes retain a non-secret fingerprint but also remain blocked.
        """
        demo_value = self.demo_trade_mode if demo_trade_mode is None else demo_trade_mode
        real_value = self.real_trade_mode if real_trade_mode is None else real_trade_mode
        login = _field(account_info, "login") if account_info is not None else None
        server = _field(account_info, "server") if account_info is not None else None
        login_digest = _login_hash(login)
        server_name = str(server).strip() if server is not None else ""
        if login_digest is None or not server_name:
            self._record_observation(
                f"identity-unknown:{login_digest or ''}\x00{server_name}"
            )
            self._state = UNKNOWN
            self._observed = None
            self._error = ACCOUNT_IDENTITY_UNKNOWN
            return self.status()

        mode = _trade_mode(
            _field(account_info, "trade_mode"), demo_value, real_value
        )
        candidate = AccountFingerprint(login_digest, server_name, mode)
        previous = self._active
        self._record_observation(candidate.token)

        if previous is None:
            self._active = candidate if mode != "UNKNOWN" else None
            self._observed = candidate
            self._state = READY if mode != "UNKNOWN" else UNKNOWN
            self._error = None if self._state == READY else ACCOUNT_MODE_UNKNOWN
            return self.status()

        if mode == "UNKNOWN":
            self._observed = candidate
            self._state = UNKNOWN
            self._error = ACCOUNT_MODE_UNKNOWN
            return self.status()

        if candidate.token != previous.token:
            # A reconnect or GUI switch is a new generation. Keep the old
            # active fingerprint separate until reconciliation acknowledges the
            # newly observed account.
            self._observed = candidate
            self._state = SWITCH_DETECTED
            self._error = ACCOUNT_SESSION_CHANGED
            return self.status()

        self._active = candidate
        self._observed = candidate
        self._state = READY
        self._error = None
        return self.status()

    def observe(self, account_info: Any, **kwargs: Any) -> dict[str, Any]:
        """Short alias used by adapters that expose an observation callback."""
        return self.observe_account_info(account_info, **kwargs)

    def mark_disconnected(self, error: str = MT5_DISCONNECTED) -> dict[str, Any]:
        """Close readiness without discarding the last known identity."""
        self._state = DISCONNECTED
        self._last_observation_key = None
        self._error = error if error in {
            MT5_DISCONNECTED,
            ACCOUNT_IDENTITY_UNKNOWN,
            ACCOUNT_MODE_UNKNOWN,
        } else MT5_DISCONNECTED
        return self.status()

    def reconcile(self) -> dict[str, Any]:
        """Acknowledge the currently observed valid account after reconciliation."""
        candidate = self._observed
        if candidate is None or candidate.account_mode == "UNKNOWN":
            self._state = UNKNOWN
            self._error = ACCOUNT_MODE_UNKNOWN if candidate else ACCOUNT_IDENTITY_UNKNOWN
            return self.status()
        self._active = candidate
        self._state = READY
        self._error = None
        return self.status()


__all__ = [
    "ACCOUNT_IDENTITY_UNKNOWN",
    "ACCOUNT_MODE_UNKNOWN",
    "ACCOUNT_SESSION_CHANGED",
    "AccountFingerprint",
    "AccountSessionGuard",
    "AccountSessionStatus",
    "DISCONNECTED",
    "MT5_DISCONNECTED",
    "READY",
    "SWITCH_DETECTED",
    "UNKNOWN",
]
