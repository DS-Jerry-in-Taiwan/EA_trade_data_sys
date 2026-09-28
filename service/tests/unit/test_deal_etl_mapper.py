from datetime import datetime, timezone

import pytest

from service.etl.deal_mapper import decode_deal_payload
from service.etl.trade_etl import DEAL_UPSERT_SQL, upsert_deals
from service.trade_query.errors import DealMappingError
from service.trade_query.models import DealRecord


def canonical_deal(**overrides):
    values = {
        "deal_id": 101,
        "order_id": 55,
        "position_id": 77,
        "symbol": "XAUUSDm",
        "deal_type": "BUY",
        "entry_type": "IN",
        "volume": 0.1,
        "price": 2300.5,
        "profit": 12.25,
        "commission": -0.5,
        "swap": -0.1,
        "occurred_at": "2026-09-28T12:00:00Z",
        "comment": "canonical",
    }
    values.update(overrides)
    return values


def test_decodes_canonical_row():
    deal = decode_deal_payload(canonical_deal())

    assert deal.deal_id == 101
    assert deal.order_id == 55
    assert deal.occurred_at == datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def test_decodes_legacy_payload():
    payload = canonical_deal()
    payload.update(
        deal=payload.pop("deal_id"),
        order=payload.pop("order_id"),
        type=payload.pop("deal_type"),
        entry=payload.pop("entry_type"),
        time=payload.pop("occurred_at"),
    )

    assert decode_deal_payload(payload).deal_id == 101


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"ticket": 102, "deal": 101}, "deal_id"),
        ({"order": 56}, "order_id"),
    ],
)
def test_rejects_conflicting_id_aliases(overrides, field):
    with pytest.raises(DealMappingError, match=f"conflicting aliases for '{field}'"):
        decode_deal_payload(canonical_deal(**overrides))


@pytest.mark.parametrize("deal_id", [None, 0, -1, True, "not-an-id"])
def test_rejects_invalid_deal_id(deal_id):
    with pytest.raises(DealMappingError, match="deal_id"):
        decode_deal_payload(canonical_deal(deal_id=deal_id))


def test_rejects_payload_without_any_deal_id():
    payload = canonical_deal()
    del payload["deal_id"]

    with pytest.raises(DealMappingError, match="required deal field 'deal_id'"):
        decode_deal_payload(payload)


def test_upsert_uses_canonical_fields_and_deal_id_conflict_contract(monkeypatch):
    captured = {}
    deal = DealRecord(**{
        **canonical_deal(),
        "occurred_at": datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
    })

    def fake_execute_values(cursor, sql, rows, template):
        captured.update(sql=sql, rows=rows, template=template)

    monkeypatch.setattr("service.etl.trade_etl.psycopg2.extras.execute_values", fake_execute_values)
    upsert_deals(object(), [deal], login=9001)

    row = captured["rows"][0]
    assert row[1] == deal.deal_id
    assert row[12] == deal.order_id
    assert row[13] is deal.occurred_at
    assert "ON CONFLICT (deal_id) DO UPDATE" in captured["sql"]
    assert "ON CONFLICT (deal_id) DO UPDATE" in DEAL_UPSERT_SQL
