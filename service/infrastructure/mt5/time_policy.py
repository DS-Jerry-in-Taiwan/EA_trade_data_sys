"""Explicit broker timestamp normalization; no clock inference or MT5 calls.

Policy validity applies to both the resulting UTC instant and evaluation time.
This helper does not judge quote freshness or modify system/grant timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt_timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class TimePolicyError(ValueError):
    """Untrusted or inapplicable time input; callers must fail closed."""


def _aware(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TimePolicyError("invalid policy datetime") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TimePolicyError("policy datetime must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TimePolicy:
    id: str
    version: str
    server: str
    resource: str
    semantic: str
    unit: str
    valid_from: datetime
    valid_until: datetime
    evidence_source: str
    timezone: str | None = None
    offset_seconds: int | None = None

    def __post_init__(self) -> None:
        for name in ("id", "version", "server", "resource", "semantic", "unit", "evidence_source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise TimePolicyError(f"{name} must be a non-empty exact string")
        if self.resource not in {"tick", "bar", "order", "deal", "position"}:
            raise TimePolicyError("unsupported time resource")
        if not isinstance(self.semantic, str) or self.semantic not in {"utc_epoch", "broker_wall_clock", "unknown"}:
            raise TimePolicyError("unsupported timestamp semantic")
        if not isinstance(self.unit, str) or self.unit not in {"seconds", "milliseconds"}:
            raise TimePolicyError("explicit seconds/milliseconds unit required")
        object.__setattr__(self, "valid_from", _aware(self.valid_from))
        object.__setattr__(self, "valid_until", _aware(self.valid_until))
        if self.valid_from >= self.valid_until:
            raise TimePolicyError("policy validity interval must be increasing")
        if self.timezone is not None:
            if not isinstance(self.timezone, str) or not self.timezone or self.timezone != self.timezone.strip():
                raise TimePolicyError("invalid IANA timezone")
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise TimePolicyError("unknown IANA timezone") from exc
        if self.offset_seconds is not None and (
            type(self.offset_seconds) is not int or abs(self.offset_seconds) >= 86400
        ):
            raise TimePolicyError("offset_seconds must be an integer below one day")
        count = int(self.timezone is not None) + int(self.offset_seconds is not None)
        if self.semantic == "broker_wall_clock" and count != 1:
            raise TimePolicyError("broker wall clock requires exactly one timezone or offset")
        if self.semantic != "broker_wall_clock" and count:
            raise TimePolicyError("timezone/offset only applies to broker wall clock")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TimePolicy":
        if not isinstance(raw, Mapping) or set(raw) - {f.name for f in fields(cls)}:
            raise TimePolicyError("invalid or unknown time policy fields")
        try:
            return cls(**dict(raw))
        except TypeError as exc:
            raise TimePolicyError("incomplete time policy") from exc


@dataclass(frozen=True)
class NormalizedTime:
    utc: datetime
    raw_value: int | float | Decimal
    raw_unit: str
    policy_id: str
    policy_version: str
    semantic: str
    server: str
    resource: str


def normalize_mt5_time(
    raw: int | float | Decimal,
    resource: str,
    server: str,
    policy: TimePolicy,
    *,
    now: datetime | None = None,
) -> NormalizedTime:
    """Normalize once using an explicit policy, rejecting ambiguous local times.

    Numeric strings and normalized wrappers are intentionally not accepted.
    Sub-microsecond inputs are rejected rather than silently rounded.
    """
    if not isinstance(policy, TimePolicy) or policy.server != server or policy.resource != resource:
        raise TimePolicyError("missing or mismatched time policy")
    if policy.semantic == "unknown":
        raise TimePolicyError("timestamp semantic is unknown")
    observed = _aware(datetime.now(UTC) if now is None else now)
    if not policy.valid_from <= observed < policy.valid_until:
        raise TimePolicyError("time policy is not currently valid")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
        raise TimePolicyError("raw timestamp must be a finite number")
    try:
        number = Decimal(str(raw))
        micros = number * (1000 if policy.unit == "milliseconds" else 1000000)
        if not number.is_finite() or micros != micros.to_integral_value():
            raise TimePolicyError("timestamp must be finite with microsecond precision")
        encoded = EPOCH + timedelta(microseconds=int(micros))
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise TimePolicyError("timestamp outside supported datetime range") from exc
    result = encoded
    if policy.semantic == "broker_wall_clock":
        wall = encoded.replace(tzinfo=None)
        if policy.offset_seconds is not None:
            try:
                result = encoded - timedelta(seconds=policy.offset_seconds)
            except OverflowError as exc:
                raise TimePolicyError("normalized timestamp outside supported range") from exc
        else:
            zone = ZoneInfo(policy.timezone)
            candidates = {
                candidate.astimezone(UTC)
                for fold in (0, 1)
                if (candidate := wall.replace(tzinfo=zone, fold=fold))
                .astimezone(UTC).astimezone(zone).replace(tzinfo=None) == wall
            }
            if len(candidates) != 1:
                raise TimePolicyError("ambiguous or nonexistent broker wall time")
            result = candidates.pop()
    if not policy.valid_from <= result < policy.valid_until:
        raise TimePolicyError("timestamp outside policy validity interval")
    return NormalizedTime(result, raw, policy.unit, policy.id, policy.version,
                          policy.semantic, server, resource)
