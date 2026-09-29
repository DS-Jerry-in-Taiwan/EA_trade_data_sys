"""Compatibility decoding for deal JSON at the ETL boundary."""

from collections.abc import Mapping
from datetime import datetime, timezone
import math
from typing import Any, Callable, TypeVar

from service.domain.trades.errors import DealMappingError
from service.domain.trades.models import DealRecord


T = TypeVar("T")
_MISSING = object()


def _aliased(
    payload: Mapping[str, Any],
    canonical_name: str,
    aliases: tuple[str, ...],
    decoder: Callable[[str, Any], T],
    *,
    default: object = _MISSING,
) -> T:
    """Decode aliases in priority order and reject contradictory values."""

    present = [(name, decoder(name, payload[name])) for name in aliases if name in payload]
    if not present:
        if default is _MISSING:
            raise DealMappingError(f"required deal field '{canonical_name}' is missing")
        return default  # type: ignore[return-value]

    selected = present[0][1]
    if any(value != selected for _, value in present[1:]):
        names = ", ".join(name for name, _ in present)
        raise DealMappingError(
            f"conflicting aliases for '{canonical_name}': {names}"
        )
    return selected


def _integer(field: str, value: Any, *, positive: bool = False) -> int:
    if isinstance(value, bool):
        result = -1
    elif isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value)
    else:
        result = -1
    minimum = 1 if positive else 0
    if result < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise DealMappingError(f"deal field '{field}' must be a {qualifier} integer")
    return result


def _number(field: str, value: Any, *, non_negative: bool = False) -> float:
    try:
        result = float(value) if not isinstance(value, bool) else math.nan
    except (TypeError, ValueError):
        result = math.nan
    if not math.isfinite(result) or (non_negative and result < 0):
        qualifier = " non-negative" if non_negative else ""
        raise DealMappingError(f"deal field '{field}' must be a finite{qualifier} number")
    return result


def _string(field: str, value: Any, *, non_empty: bool = False) -> str:
    if not isinstance(value, str) or (non_empty and not value.strip()):
        qualifier = "non-empty " if non_empty else ""
        raise DealMappingError(f"deal field '{field}' must be a {qualifier}string")
    return value


def _timestamp(field: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise DealMappingError(f"deal field '{field}' must be an ISO timestamp") from exc
    else:
        raise DealMappingError(f"deal field '{field}' must be an ISO timestamp")

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def decode_deal_payload(payload: Mapping[str, Any]) -> DealRecord:
    """Decode canonical or legacy API JSON into one canonical ``DealRecord``.

    Identifier precedence is ``deal_id`` -> ``ticket`` -> legacy ``deal`` and
    ``order_id`` -> ``order``. Multiple aliases are accepted only when they
    resolve to the same value.
    """

    if not isinstance(payload, Mapping):
        raise DealMappingError("deal payload must be a mapping")

    deal_type = _aliased(
        payload,
        "deal_type",
        ("deal_type", "type"),
        lambda field, value: _string(field, value, non_empty=True),
    )

    return DealRecord(
        deal_id=_aliased(
            payload,
            "deal_id",
            ("deal_id", "ticket", "deal"),
            lambda field, value: _integer(field, value, positive=True),
        ),
        order_id=_aliased(
            payload,
            "order_id",
            ("order_id", "order"),
            _integer,
            default=0,
        ),
        position_id=_integer("position_id", payload.get("position_id", 0)),
        symbol=_string(
            "symbol",
            payload.get("symbol"),
            non_empty=deal_type in {"BUY", "SELL"},
        ),
        deal_type=deal_type,
        entry_type=_aliased(
            payload,
            "entry_type",
            ("entry_type", "entry"),
            lambda field, value: _string(field, value, non_empty=True),
        ),
        volume=_number("volume", payload.get("volume", 0), non_negative=True),
        price=_number("price", payload.get("price", 0), non_negative=True),
        profit=_number("profit", payload.get("profit", 0)),
        commission=_number("commission", payload.get("commission", 0)),
        swap=_number("swap", payload.get("swap", 0)),
        occurred_at=_aliased(
            payload,
            "occurred_at",
            ("occurred_at", "time"),
            _timestamp,
        ),
        comment=_string("comment", payload.get("comment", "") or ""),
    )


def decode_deal_payloads(payloads: list[Mapping[str, Any]]) -> list[DealRecord]:
    """Decode a JSON deal collection, failing the whole batch on bad data."""

    return [decode_deal_payload(payload) for payload in payloads]


__all__ = ["decode_deal_payload", "decode_deal_payloads"]
