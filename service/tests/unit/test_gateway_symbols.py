"""Mock-only public symbol catalog and position alias contracts."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

try:
    import pymt5linux  # noqa: F401
except ModuleNotFoundError:
    stub = ModuleType("pymt5linux")
    stub.MetaTrader5 = object
    sys.modules["pymt5linux"] = stub

from service.config.models import Settings
from service.infrastructure.mt5.client import MT5Client
from service.trade_query.account_service import AccountService
from service.trade_query.contracts import TradeQueryError


def settings(aliases=None):
    return Settings.from_mapping({
        "symbol_aliases": aliases or {},
        "tick_service": {"symbols": ["XAUUSDm", "EURUSDm", "GBPUSDm", "BTC"]},
        "history_service": {"symbols": [{"name": name} for name in
                                         ["XAUUSDm", "EURUSDm", "GBPUSDm", "BTC"]]},
    }, environ={})


class CatalogMT5:
    def __init__(self, catalog):
        self.catalog = catalog
        self.info = SimpleNamespace(login=123, server="Broker-Demo", trade_mode=0)
        self.symbol_info_calls = []
        self.position_filters = []
        self.positions = []

    def account_info(self):
        return self.info

    def symbols_get(self):
        if self.catalog is None:
            return None
        return [SimpleNamespace(name=name) for name in self.catalog]

    def symbol_info(self, symbol):
        self.symbol_info_calls.append(symbol)
        return SimpleNamespace(digits=5, spread=7, description="Market", trade_mode=4)

    def positions_get(self, **filters):
        self.position_filters.append(filters)
        return [position for position in self.positions
                if not filters or position.symbol == filters["symbol"]]


class Connector:
    def __init__(self, module):
        self.module = module

    def connect(self):
        return self.module


def make_service(catalog, aliases=None):
    cfg = settings(aliases)
    module = CatalogMT5(catalog)
    client = MT5Client(lambda: Connector(module), symbol_aliases=cfg.symbol_aliases)
    return AccountService(settings=cfg, mt5_client=client), module, client


def market_client(account_service):
    flask = pytest.importorskip("flask")
    from service.gateway.routes.market_data import create_blueprint

    app = flask.Flask(__name__)
    app.register_blueprint(create_blueprint(
        None, 60, None, account_service,
        ["XAUUSDm", "EURUSDm", "GBPUSDm", "BTC"], "/unused/openapi.yaml",
    ))
    return app.test_client()


def positions_client(account_service, monkeypatch):
    flask = pytest.importorskip("flask")
    from service.gateway.routes.trade_query import create_blueprint

    monkeypatch.setenv("TEST_SYMBOL_QUERY_KEY", "test-key")
    app = flask.Flask(__name__)
    app.register_blueprint(create_blueprint(account_service, lambda: {
        "api_gateway": {"readonly_api_key_env": "TEST_SYMBOL_QUERY_KEY"},
    }))
    return app.test_client()


def test_public_symbol_catalog_keeps_all_logical_names_when_one_is_missing():
    service, module, _client = make_service(["XAUUSD.sim", "EUR_USD", "GBPUSD.sim"])

    response = market_client(service).get("/api/v1/symbols")

    assert response.status_code == 200
    assert response.json["count"] == 4
    assert [item["name"] for item in response.json["symbols"]] == [
        "XAUUSDm", "EURUSDm", "GBPUSDm", "BTC",
    ]
    assert response.json["symbols"][-1] == {
        "name": "BTC", "digits": None, "spread": None,
        "error": "Symbol is unavailable", "code": "symbol_not_found",
    }
    assert module.symbol_info_calls == ["XAUUSD.sim", "EUR_USD", "GBPUSD.sim"]


def test_public_catalog_marks_ambiguity_without_arbitrary_broker_call():
    service, module, _client = make_service(
        ["XAUUSD.sim", "EURUSD", "EUR_USD", "GBPUSD.sim", "BTCUSD.sim"]
    )

    response = market_client(service).get("/api/v1/symbols")

    assert response.status_code == 200
    assert response.json["symbols"][1]["code"] == "ambiguous_symbol"
    assert "EURUSD" not in module.symbol_info_calls
    assert "EUR_USD" not in module.symbol_info_calls
    assert response.json["symbols"][-1]["name"] == "BTC"
    assert "BTCUSD.sim" in module.symbol_info_calls


@pytest.mark.parametrize(("code", "message", "status"), [
    ("symbol_not_found", "Symbol is unavailable", 404),
    ("ambiguous_symbol", "Symbol mapping is ambiguous", 409),
    ("mt5_unavailable", "MT5 symbol catalog is unavailable", 503),
])
def test_symbol_route_propagates_sanitized_service_contract(code, message, status):
    class FailingService:
        def get_symbols(self, _names):
            raise TradeQueryError(code, message, status)

    response = market_client(FailingService()).get("/api/v1/symbols")

    assert response.status_code == status
    assert response.json == {"error": message, "code": code}


def test_unavailable_catalog_returns_503_without_rpc_diagnostics():
    service, module, _client = make_service(None)

    response = market_client(service).get("/api/v1/symbols")

    assert response.status_code == 503
    assert response.json == {
        "error": "MT5 symbol catalog is unavailable", "code": "mt5_unavailable",
    }
    assert module.symbol_info_calls == []


def test_connection_failure_is_translated_inside_account_service():
    service, _module, client = make_service([])

    def fail_connection():
        raise ConnectionError("private connection diagnostics")

    client.ensure_connected = fail_connection
    response = market_client(service).get("/api/v1/symbols")

    assert response.status_code == 503
    assert response.json == {"error": "MT5 is unavailable", "code": "mt5_unavailable"}
    assert "private" not in response.get_data(as_text=True)


def test_account_switch_during_resolver_initialization_discards_catalog_read():
    service, module, _client = make_service(
        ["XAUUSD.sim", "EUR_USD", "GBPUSD.sim", "BTCUSD.sim"]
    )
    original_catalog = module.symbols_get

    def switch_account_during_catalog():
        module.info = SimpleNamespace(login=456, server="Other-Demo", trade_mode=0)
        return original_catalog()

    module.symbols_get = switch_account_during_catalog
    response = market_client(service).get("/api/v1/symbols")

    assert response.status_code == 503
    assert response.json["code"] == "account_session_transition"
    assert module.symbol_info_calls == []


def test_unexpected_symbol_route_error_does_not_leak_details():
    class FailingService:
        def get_symbols(self, _names):
            raise RuntimeError("private transport diagnostics")

    response = market_client(FailingService()).get("/api/v1/symbols")

    assert response.status_code == 500
    assert response.json == {"error": "Internal server error", "code": "internal_error"}
    assert "private" not in response.get_data(as_text=True)


def test_positions_resolve_filter_and_normalize_known_result_symbol(monkeypatch):
    service, module, _client = make_service(
        ["XAUUSD.sim", "EUR_USD", "GBPUSD.sim", "BTCUSD.sim"]
    )
    module.positions = [SimpleNamespace(ticket=1, symbol="XAUUSD.sim", type=0)]

    response = positions_client(service, monkeypatch).get(
        "/api/v1/positions?symbol=XAUUSDm", headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    assert module.position_filters == [{"symbol": "XAUUSD.sim"}]
    assert response.json[0]["symbol"] == "XAUUSDm"


@pytest.mark.parametrize(("catalog", "symbol", "status", "code"), [
    (["XAUUSD.sim"], "BTC", 404, "symbol_not_found"),
    (["EURUSD", "EUR_USD"], "EURUSDm", 409, "ambiguous_symbol"),
])
def test_position_filter_failure_is_typed_and_does_not_query_mt5(
    catalog, symbol, status, code, monkeypatch,
):
    service, module, _client = make_service(catalog)

    response = positions_client(service, monkeypatch).get(
        f"/api/v1/positions?symbol={symbol}", headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == status
    assert response.json["code"] == code
    assert module.position_filters == []


def test_unfiltered_positions_preserve_unconfigured_broker_instruments():
    service, module, _client = make_service(["XAUUSD.sim", "USDJPYm"])
    module.positions = [SimpleNamespace(ticket=1, symbol="XAUUSD.sim", type=0),
                        SimpleNamespace(ticket=2, symbol="USDJPYm", type=1)]

    result = service.get_positions()

    assert module.position_filters == [{}]
    assert [position["symbol"] for position in result] == ["XAUUSDm", "USDJPYm"]


def test_position_alias_is_rebuilt_after_account_switch():
    service, module, client = make_service(["XAUUSD.sim", "EUR_USD", "GBPUSD.sim", "BTCUSD.sim"])
    service.get_positions()
    module.info = SimpleNamespace(login=456, server="Other-Demo", trade_mode=0)
    module.catalog = ["XAU_USD", "EURUSD.sim", "GBP_USD", "BTC_USD"]
    module.positions = [SimpleNamespace(ticket=1, symbol="XAU_USD", type=0)]

    assert service.get_positions("XAUUSDm")["code"] == "account_session_transition"
    assert client.session_status()["ready"] is True
    assert service.get_positions("XAUUSDm")[0]["symbol"] == "XAUUSDm"
    assert module.position_filters[-1] == {"symbol": "XAU_USD"}
