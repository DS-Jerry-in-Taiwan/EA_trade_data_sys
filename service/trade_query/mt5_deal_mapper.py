"""Strict mapping from an MT5 ``TradeDeal`` to a canonical deal record."""

from datetime import datetime, timezone
import math
from typing import Any

from service.trade_query.errors import DealMappingError
from service.trade_query.models import DealRecord


_DEAL_TYPES = {
    0: "BUY",
    1: "SELL",
    2: "BALANCE",
    3: "CREDIT",
    4: "CHARGE",
    5: "CORRECTION",
    6: "BONUS",
    7: "COMMISSION",
    8: "COMMISSION_DAILY",
    9: "COMMISSION_MONTHLY",
    10: "COMMISSION_AGENT_DAILY",
    11: "COMMISSION_AGENT_MONTHLY",
    12: "INTEREST",
    13: "BUY_CANCELED",
    14: "SELL_CANCELED",
    15: "DIVIDEND",
    16: "DIVIDEND_FRANKED",
    17: "TAX",
}

_ENTRY_TYPES = {
    0: "IN",
    1: "OUT",
    2: "INOUT",
    3: "OUT_BY",
}


def _required(raw: object, field: str) -> Any:
    try:
        return getattr(raw, field)
    except AttributeError as exc:
        raise DealMappingError(f"required MT5 deal field '{field}' is missing") from exc


def _optional(raw: object, field: str, default: Any) -> Any:
    try:
        return getattr(raw, field)
    except AttributeError:
        return default


def _integer(field: str, value: Any, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise DealMappingError(f"MT5 deal field '{field}' must be a {qualifier} integer")
    return value


def _number(field: str, value: Any, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DealMappingError(f"MT5 deal field '{field}' must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise DealMappingError(f"MT5 deal field '{field}' must be a finite number")
    if non_negative and result < 0:
        raise DealMappingError(f"MT5 deal field '{field}' must be non-negative")
    return result


def _string(field: str, value: Any, *, non_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DealMappingError(f"MT5 deal field '{field}' must be a string")
    if non_empty and not value.strip():
        raise DealMappingError(f"MT5 deal field '{field}' must be non-empty")
    return value


def _enum(field: str, value: Any, values: dict[int, str]) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DealMappingError(f"MT5 deal field '{field}' must be an integer enum")
    try:
        return values[value]
    except KeyError as exc:
        raise DealMappingError(f"MT5 deal field '{field}' has an unsupported enum") from exc


def _occurred_at(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DealMappingError("MT5 deal field 'time' must be a finite timestamp")
    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise DealMappingError("MT5 deal field 'time' must be a finite timestamp")
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise DealMappingError("MT5 deal field 'time' is outside the supported range") from exc


def map_mt5_deal(raw: object) -> DealRecord:
    """Map one MT5 deal without probing aliases or leaking source data."""

    deal_type_code = _required(raw, "type")
    deal_type = _enum("type", deal_type_code, _DEAL_TYPES)
    symbol = _string(
        "symbol", _required(raw, "symbol"), non_empty=deal_type_code in (0, 1)
    )

    return DealRecord(
        deal_id=_integer("ticket", _required(raw, "ticket"), positive=True),
        order_id=_integer("order", _required(raw, "order")),
        position_id=_integer("position_id", _required(raw, "position_id")),
        symbol=symbol,
        deal_type=deal_type,
        entry_type=_enum("entry", _required(raw, "entry"), _ENTRY_TYPES),
        volume=_number("volume", _required(raw, "volume"), non_negative=True),
        price=_number("price", _required(raw, "price"), non_negative=True),
        profit=_number("profit", _optional(raw, "profit", 0.0)),
        commission=_number("commission", _optional(raw, "commission", 0.0)),
        swap=_number("swap", _optional(raw, "swap", 0.0)),
        occurred_at=_occurred_at(_required(raw, "time")),
        comment=_string("comment", _optional(raw, "comment", "")),
    )


__all__ = ["map_mt5_deal"]
