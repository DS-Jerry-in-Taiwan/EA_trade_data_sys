from datetime import datetime, timezone

import pytest

from service.domain.trades.errors import DealMappingError
from service.trade_query.mt5_deal_mapper import map_mt5_deal


class StrictDeal:
    declared_fields = frozenset(
        {
            "ticket",
            "order",
            "position_id",
            "time",
            "type",
            "entry",
            "symbol",
            "volume",
            "price",
            "profit",
            "commission",
            "swap",
            "comment",
        }
    )

    def __init__(self, **overrides):
        fields = {
            "ticket": 101,
            "order": 202,
            "position_id": 303,
            "time": 1_798_459_200,
            "type": 0,
            "entry": 0,
            "symbol": "XAUUSDm",
            "volume": 0.1,
            "price": 2500.5,
            "profit": 12.25,
            "commission": -0.5,
            "swap": -0.1,
            "comment": "strategy entry",
        }
        fields.update(overrides)
        object.__setattr__(self, "_fields", fields)
        object.__setattr__(self, "accessed", [])

    def __getattribute__(self, name):
        if name in {"_fields", "accessed", "declared_fields", "without"}:
            return object.__getattribute__(self, name)
        accessed = object.__getattribute__(self, "accessed")
        accessed.append(name)
        fields = object.__getattribute__(self, "_fields")
        if name not in fields:
            declared_fields = object.__getattribute__(self, "declared_fields")
            if name in declared_fields:
                raise AttributeError(name)
            raise AssertionError(f"undeclared MT5 field accessed: {name}")
        return fields[name]

    def without(self, field):
        del self._fields[field]
        return self


def test_maps_ticket_identity_numeric_fields_and_optional_values():
    raw = StrictDeal()

    deal = map_mt5_deal(raw)

    assert deal.deal_id == 101
    assert deal.order_id == 202
    assert deal.position_id == 303
    assert deal.symbol == "XAUUSDm"
    assert (deal.volume, deal.price) == (0.1, 2500.5)
    assert (deal.profit, deal.commission, deal.swap) == (12.25, -0.5, -0.1)
    assert deal.comment == "strategy entry"
    assert "deal" not in raw.accessed


@pytest.mark.parametrize(
    ("type_code", "entry_code", "deal_type", "entry_type"),
    (
        (0, 0, "BUY", "IN"),
        (1, 1, "SELL", "OUT"),
        (2, 2, "BALANCE", "INOUT"),
        (17, 3, "TAX", "OUT_BY"),
    ),
)
def test_maps_mt5_type_and_entry_enums(type_code, entry_code, deal_type, entry_type):
    deal = map_mt5_deal(StrictDeal(type=type_code, entry=entry_code))

    assert deal.deal_type == deal_type
    assert deal.entry_type == entry_type


@pytest.mark.parametrize("type_code", (0, 1))
def test_trade_deal_requires_non_empty_symbol(type_code):
    with pytest.raises(DealMappingError, match="symbol.*non-empty"):
        map_mt5_deal(StrictDeal(type=type_code, symbol=""))


@pytest.mark.parametrize(
    ("type_code", "expected_deal_type"), ((2, "BALANCE"), (3, "CREDIT"))
)
def test_non_trade_deal_allows_empty_string_symbol(type_code, expected_deal_type):
    deal = map_mt5_deal(StrictDeal(type=type_code, symbol=""))

    assert deal.deal_type == expected_deal_type
    assert deal.symbol == ""


def test_non_trade_deal_still_requires_symbol_to_be_string():
    with pytest.raises(DealMappingError, match="symbol.*string"):
        map_mt5_deal(StrictDeal(type=2, symbol=None))


def test_maps_unix_timestamp_to_utc():
    deal = map_mt5_deal(StrictDeal(time=0))

    assert deal.occurred_at == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert deal.occurred_at.tzinfo is timezone.utc


@pytest.mark.parametrize("field", ("profit", "commission", "swap", "comment"))
def test_uses_explicit_defaults_for_missing_optional_fields(field):
    deal = map_mt5_deal(StrictDeal().without(field))

    expected = "" if field == "comment" else 0.0
    assert getattr(deal, field) == expected


@pytest.mark.parametrize(
    "field",
    ("ticket", "order", "position_id", "time", "type", "entry", "symbol", "volume", "price"),
)
def test_missing_required_field_raises_sanitized_mapping_error(field):
    raw = StrictDeal().without(field)

    with pytest.raises(DealMappingError, match=field) as error:
        map_mt5_deal(raw)

    message = str(error.value)
    assert "StrictDeal" not in message
    assert "XAUUSDm" not in message


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ticket", "101"),
        ("order", 1.5),
        ("position_id", True),
        ("time", "2026-12-28"),
        ("type", "BUY"),
        ("entry", 99),
        ("symbol", None),
        ("volume", "0.1"),
        ("price", float("nan")),
        ("profit", object()),
        ("comment", 123),
    ),
)
def test_wrong_field_type_raises_sanitized_mapping_error(field, value):
    with pytest.raises(DealMappingError, match=field) as error:
        map_mt5_deal(StrictDeal(**{field: value}))

    message = str(error.value)
    assert repr(value) not in message
    assert "StrictDeal" not in message
