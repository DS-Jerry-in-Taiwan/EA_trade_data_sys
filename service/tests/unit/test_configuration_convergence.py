"""Synthetic configuration certification; no MT5 calls or credential files."""

import importlib

import pytest
import yaml

from service.config import load_settings, resolve_config_path


@pytest.mark.parametrize("selector", ["TRADE_DATA_CONFIG", "MT5_SETTINGS_PATH"])
@pytest.mark.parametrize("mode", ["terminal", "managed"])
def test_all_composition_roots_pass_alternate_settings_to_connectors(
    monkeypatch, tmp_path, selector, mode
):
    path = tmp_path / "alternate.yaml"
    path.write_text(yaml.safe_dump({
        "connection": {"default_host": "synthetic-mt5", "port": 8123,
                       "timeout": 7, "mode": mode},
        "symbol_aliases": {"BTC": "BTCUSD.synthetic"},
        "tick_service": {"symbols": ["BTC"], "output_dir": str(tmp_path / "ticks"),
                         "socket_path": str(tmp_path / "ticks.sock")},
        "history_service": {"data_path": str(tmp_path / "history"),
                            "symbols": [{"name": "BTC", "timeframes": ["M5"]}]},
    }), encoding="utf-8")
    for name in ("TRADE_DATA_CONFIG", "MT5_SETTINGS_PATH", "MT5_CONNECTION_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(selector, str(path))
    monkeypatch.setenv("EXECUTION_IDEMPOTENCY_DB", str(tmp_path / "execution.sqlite3"))

    gateway_module = importlib.import_module("service.entrypoints.api_gateway")
    execution_module = importlib.import_module("service.entrypoints.execution_server")
    from service.history.worker import HistoryService
    from service.realtime.worker import TickService
    from service.trade_query.account_service import AccountService
    import core.connection_manager as connection_manager

    # Managed-mode behavior is exercised with a synthetic profile returned in
    # memory. No real account JSON is opened, and connect never reaches MT5.
    monkeypatch.setattr(connection_manager.MT5Connector, "_load_managed_accounts",
                        lambda _self: {"synthetic": True})
    adapters = []
    adapter_type = execution_module.MT5ExecutionAdapter

    def capture_adapter(client, symbols):
        adapter = adapter_type(client, symbols)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(execution_module, "MT5ExecutionAdapter", capture_adapter)
    gateway, gateway_client = gateway_module.build_gateway()
    execution, execution_client = execution_module.build_execution_server()
    tick, history, account = TickService(), HistoryService(), AccountService()
    clients = [gateway_client, execution_client, tick.mt5_client,
               history.mt5_client, account.mt5_client]
    assert gateway.context.tick_symbols == ["BTC"]
    assert gateway.context.symbol_names == ["BTC"]
    assert tick.symbols == ["BTC"]
    assert history.symbols[0]["name"] == "BTC"
    assert adapters[0].resolver_symbols == ("BTC",)
    received = []

    def factory(*, settings):
        connector = connection_manager.MT5Connector(settings=settings)
        received.append(connector)
        connector.connect = lambda: None
        return connector

    try:
        for client in clients:
            client._connector_factory = factory
            assert client.ensure_connected() is False
            assert client._symbol_aliases == {"BTC": "BTCUSD.synthetic"}
        assert len(received) == len(clients)
        for connector in received:
            assert connector.connection.default_host == "synthetic-mt5"
            assert connector.connection.port == 8123
            assert connector.timeout == 7
            assert connector.mode == mode
            assert (connector.accounts is None) == (mode == "terminal")
    finally:
        history.close()
        for client in clients:
            client.shutdown()


def test_empty_canonical_selector_accepts_legacy_override(tmp_path):
    path = tmp_path / "legacy.yaml"
    path.write_text("connection: {port: 8123}\n", encoding="utf-8")
    settings = load_settings(environ={"TRADE_DATA_CONFIG": "", "MT5_SETTINGS_PATH": str(path)})
    assert settings.connection.port == 8123


def test_selector_conflict_is_rejected_before_file_access_without_values():
    with pytest.raises(ValueError, match="Conflicting configuration selectors") as error:
        resolve_config_path(environ={
            "TRADE_DATA_CONFIG": "/synthetic/private-one.yaml",
            "MT5_SETTINGS_PATH": "/synthetic/private-two.yaml",
        })
    assert "private-one" not in str(error.value)
    assert "private-two" not in str(error.value)
