"""Canonical, infrastructure-independent read models for historical deals."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math

from service.trade_query.errors import DealMappingError


def _require_id(name: str, value: int, *, positive: bool = False) -> None:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "a positive" if positive else "a non-negative"
        raise DealMappingError(f"{name} must be {qualifier} integer")


def _require_finite(name: str, value: float, *, non_negative: bool = False) -> None:
    try:
        finite = not isinstance(value, bool) and math.isfinite(value)
    except TypeError:
        finite = False
    if not finite:
        raise DealMappingError(f"{name} must be a finite number")
    if non_negative and value < 0:
        raise DealMappingError(f"{name} must be non-negative")


def _require_string(name: str, value: str, *, non_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise DealMappingError(f"{name} must be a string")
    if non_empty and not value.strip():
        raise DealMappingError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class DealRecord:
    """A validated deal detached from any MT5-specific representation."""

    deal_id: int
    order_id: int
    position_id: int
    symbol: str
    deal_type: str
    entry_type: str
    volume: float
    price: float
    profit: float
    commission: float
    swap: float
    occurred_at: datetime
    comment: str = ""

    def __post_init__(self) -> None:
        _require_id("deal_id", self.deal_id, positive=True)
        _require_id("order_id", self.order_id)
        _require_id("position_id", self.position_id)
        for name in ("deal_type", "entry_type"):
            _require_string(name, getattr(self, name), non_empty=True)
        _require_string("symbol", self.symbol, non_empty=self.deal_type in {"BUY", "SELL"})
        _require_string("comment", self.comment)
        for name in ("volume", "price"):
            _require_finite(name, getattr(self, name), non_negative=True)
        for name in ("profit", "commission", "swap"):
            _require_finite(name, getattr(self, name))
        if not isinstance(self.occurred_at, datetime):
            raise DealMappingError("occurred_at must be a datetime")
        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() != timedelta(0)
        ):
            raise DealMappingError("occurred_at must be UTC-aware")


@dataclass(frozen=True)
class DealSummary:
    """Aggregate totals for a collection of canonical deal records."""

    profit: float
    commission: float
    swap: float
    count: int

    def __post_init__(self) -> None:
        for name in ("profit", "commission", "swap"):
            _require_finite(name, getattr(self, name))
        _require_id("count", self.count)


__all__ = ["DealRecord", "DealSummary"]
