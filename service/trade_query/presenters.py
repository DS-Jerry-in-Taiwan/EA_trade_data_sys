"""Versioned API presenters for canonical trade-query models."""

from typing import Any, Dict

from service.trade_query.models import DealRecord, DealSummary


def _round_number(value: float, digits: int = 2) -> float:
    return round(value, digits)


def present_deal_v1(deal: DealRecord) -> Dict[str, Any]:
    """Present a canonical deal using the existing API v1 field contract."""

    return {
        "deal": deal.deal_id,
        "ticket": deal.deal_id,
        "deal_id": deal.deal_id,
        "order": deal.order_id,
        "order_id": deal.order_id,
        "position_id": deal.position_id,
        "symbol": deal.symbol,
        "type": deal.deal_type,
        "entry": deal.entry_type,
        "volume": deal.volume,
        "price": deal.price,
        "profit": _round_number(deal.profit),
        "commission": _round_number(deal.commission),
        "swap": _round_number(deal.swap),
        "time": deal.occurred_at.isoformat(),
        "comment": deal.comment,
    }


def present_deal_summary_v1(summary: DealSummary) -> Dict[str, Any]:
    """Present canonical deal totals using the API v1 summary contract."""

    return {
        "profit": _round_number(summary.profit),
        "commission": _round_number(summary.commission),
        "swap": _round_number(summary.swap),
        "count": summary.count,
    }


__all__ = ["present_deal_summary_v1", "present_deal_v1"]
