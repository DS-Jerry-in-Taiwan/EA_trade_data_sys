from datetime import datetime, timezone

from service.trade_query.models import DealRecord, DealSummary
from service.trade_query.presenters import present_deal_summary_v1, present_deal_v1


def test_present_deal_v1_preserves_contract_and_uses_canonical_ids():
    deal = DealRecord(
        deal_id=101,
        order_id=202,
        position_id=303,
        symbol="XAUUSDm",
        deal_type="BUY",
        entry_type="OUT",
        volume=0.1,
        price=2500.5,
        profit=12.345,
        commission=-0.555,
        swap=-0.105,
        occurred_at=datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
        comment="closed",
    )

    assert present_deal_v1(deal) == {
        "deal": 101,
        "ticket": 101,
        "deal_id": 101,
        "order": 202,
        "order_id": 202,
        "position_id": 303,
        "symbol": "XAUUSDm",
        "type": "BUY",
        "entry": "OUT",
        "volume": 0.1,
        "price": 2500.5,
        "profit": 12.35,
        "commission": -0.56,
        "swap": -0.1,
        "time": "2026-09-28T12:00:00+00:00",
        "comment": "closed",
    }


def test_present_deal_v1_identity_aliases_are_equal():
    deal = DealRecord(
        deal_id=101,
        order_id=202,
        position_id=303,
        symbol="XAUUSDm",
        deal_type="BUY",
        entry_type="IN",
        volume=0.1,
        price=2500.5,
        profit=0.0,
        commission=0.0,
        swap=0.0,
        occurred_at=datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
    )

    presented = present_deal_v1(deal)

    assert presented["deal"] == presented["ticket"] == presented["deal_id"] == 101
    assert presented["order"] == presented["order_id"] == 202


def test_present_deal_summary_v1_preserves_summary_contract():
    summary = DealSummary(profit=1.235, commission=-0.505, swap=-0.105, count=2)

    assert present_deal_summary_v1(summary) == {
        "profit": 1.24,
        "commission": -0.51,
        "swap": -0.1,
        "count": 2,
    }
