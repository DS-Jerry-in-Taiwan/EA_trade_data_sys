import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import inspect

import pytest

from service.domain.trades import DealMappingError, DealRecord, DealSummary
from service.trade_query import errors, models


def make_deal(**overrides):
    values = {
        "deal_id": 101,
        "order_id": 202,
        "position_id": 303,
        "symbol": "XAUUSDm",
        "deal_type": "BUY",
        "entry_type": "IN",
        "volume": 0.1,
        "price": 2500.5,
        "profit": 12.25,
        "commission": -0.5,
        "swap": 0.0,
        "occurred_at": datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
        "comment": "strategy entry",
    }
    values.update(overrides)
    return DealRecord(**values)


def test_deal_record_is_immutable_and_uses_occurred_at():
    deal = make_deal()

    assert deal.occurred_at == datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    assert not hasattr(deal, "time")
    with pytest.raises(FrozenInstanceError):
        deal.profit = 99.0


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("deal_id", 0),
        ("deal_id", True),
        ("order_id", -1),
        ("position_id", 1.5),
    ),
)
def test_deal_record_rejects_invalid_ids(field, value):
    with pytest.raises(DealMappingError, match=field):
        make_deal(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("symbol", ""),
        ("symbol", "  "),
        ("deal_type", None),
        ("entry_type", 1),
        ("comment", None),
    ),
)
def test_deal_record_rejects_invalid_strings(field, value):
    with pytest.raises(DealMappingError, match=field):
        make_deal(**{field: value})


@pytest.mark.parametrize("deal_type", ("BALANCE", "CREDIT"))
def test_deal_record_allows_empty_symbol_for_non_trade_deal(deal_type):
    deal = make_deal(deal_type=deal_type, symbol="")

    assert deal.deal_type == deal_type
    assert deal.symbol == ""


@pytest.mark.parametrize("deal_type", ("BUY", "SELL"))
def test_deal_record_requires_non_empty_symbol_for_trade_deal(deal_type):
    with pytest.raises(DealMappingError, match="symbol"):
        make_deal(deal_type=deal_type, symbol="")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("volume", float("nan")),
        ("price", float("inf")),
        ("profit", float("-inf")),
        ("commission", True),
        ("swap", "0"),
    ),
)
def test_deal_record_rejects_non_finite_numbers(field, value):
    with pytest.raises(DealMappingError, match=field):
        make_deal(**{field: value})


@pytest.mark.parametrize("field", ("volume", "price"))
def test_deal_record_rejects_negative_volume_and_price(field):
    with pytest.raises(DealMappingError, match=field):
        make_deal(**{field: -0.01})


def test_deal_record_allows_negative_financial_results():
    deal = make_deal(profit=-10.0, commission=-0.5, swap=-0.25)

    assert (deal.profit, deal.commission, deal.swap) == (-10.0, -0.5, -0.25)


@pytest.mark.parametrize(
    "occurred_at",
    (
        datetime(2026, 9, 28, 12),
        datetime(2026, 9, 28, 12, tzinfo=timezone(timedelta(hours=8))),
        "2026-09-28T12:00:00Z",
    ),
)
def test_deal_record_requires_utc_aware_datetime(occurred_at):
    with pytest.raises(DealMappingError, match="occurred_at"):
        make_deal(occurred_at=occurred_at)


def test_deal_summary_is_immutable_and_validated():
    summary = DealSummary(profit=12.0, commission=-0.5, swap=0.0, count=1)

    assert summary.count == 1
    with pytest.raises(FrozenInstanceError):
        summary.count = 2


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("profit", float("nan")),
        ("commission", float("inf")),
        ("swap", float("-inf")),
        ("count", -1),
        ("count", True),
    ),
)
def test_deal_summary_rejects_invalid_values(field, value):
    values = {"profit": 0.0, "commission": 0.0, "swap": 0.0, "count": 0}
    values[field] = value

    with pytest.raises(DealMappingError, match=field):
        DealSummary(**values)


def test_domain_models_do_not_load_mt5_runtime_modules():
    imported_modules = set()
    for module in (models, errors):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    forbidden_roots = {"MetaTrader5", "pymt5linux", "rpyc"}
    assert not {
        name for name in imported_modules if name.split(".", 1)[0] in forbidden_roots
    }


def test_legacy_trade_query_paths_reexport_canonical_domain_contracts():
    assert models.DealRecord is DealRecord
    assert models.DealSummary is DealSummary
    assert errors.DealMappingError is DealMappingError
