"""Contracts for the History and read-only Trade Query package migration."""

import ast
from pathlib import Path

from service.history.query_service import HistoryQueryService
from service.history.repository import HistoryRepository
from service.history.worker import HistoryService
from service.trade_query.account_service import AccountService


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_history_entrypoint_delegates_to_modular_worker():
    source = (SERVICE_ROOT / "entrypoints" / "history_worker.py").read_text(
        encoding="utf-8"
    )
    assert "from service.history.worker import main" in source


def test_history_and_trade_query_use_only_modular_mt5_infrastructure():
    for relative in ("history/worker.py", "trade_query/account_service.py"):
        source = (SERVICE_ROOT / relative).read_text(encoding="utf-8")
        assert "service.infrastructure.mt5.client" in source


def test_trade_query_module_exposes_no_mutation_calls():
    path = SERVICE_ROOT / "trade_query" / "account_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = {
        "order_send",
        "order_check",
        "positions_close",
        "trade",
        "buy",
        "sell",
    }
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert forbidden.isdisjoint(called)


def test_history_defaults_preserve_cache_location_and_csv_contract():
    assert HistoryRepository().data_path == "/app/service/data/history"
    assert HistoryQueryService.COLUMNS == (
        "time",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
    )
    assert HistoryQueryService.SOURCE_COLUMN == "source_symbol"
