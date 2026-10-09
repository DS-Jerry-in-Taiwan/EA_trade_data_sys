import pytest
import yaml

from service.config import load_runtime_settings, load_settings, resolve_config_path
from service.config.models import Settings


def test_legacy_connector_selector_uses_shared_loader(tmp_path):
    path = tmp_path / "custom.yaml"
    path.write_text("connection:\n  port: 8123\n", encoding="utf-8")
    assert load_settings(environ={"MT5_SETTINGS_PATH": str(path)}).connection.port == 8123


def test_equivalent_selectors_are_accepted(tmp_path):
    path = tmp_path / "settings.yaml"
    assert resolve_config_path(path, environ={
        "TRADE_DATA_CONFIG": str(path),
        "MT5_SETTINGS_PATH": str(tmp_path / "nested" / ".." / "settings.yaml"),
    }) == path


@pytest.mark.parametrize("explicit", [False, True])
def test_conflicting_selectors_fail_without_disclosing_paths(tmp_path, explicit):
    first, second = tmp_path / "private-a.yaml", tmp_path / "private-b.yaml"
    env = {"TRADE_DATA_CONFIG": str(first)}
    if not explicit:
        env["MT5_SETTINGS_PATH"] = str(second)
    with pytest.raises(ValueError, match="Conflicting configuration selectors") as error:
        resolve_config_path(second if explicit else None, environ=env)
    assert str(first) not in str(error.value)
    assert str(second) not in str(error.value)


def test_loader_returns_typed_sections_and_applies_runtime_overrides(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "connection": {
                    "mode": "managed",
                    "default_host": "mt5-test",
                    "port": 8002,
                    "timeout": 12,
                },
                "tick_service": {
                    "symbols": ["BTC"],
                    "socket_path": "/config/ticks.sock",
                },
                "history_service": {
                    "symbols": [{"name": "BTC", "timeframes": ["M5"]}],
                },
                "api_gateway": {
                    "host": "127.0.0.1",
                    "port": 8091,
                    "readonly_api_key_env": "TEST_READONLY_KEY",
                },
                "trade_query": {"default_days": 3, "max_days": 30},
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(
        path,
        environ={
            "TICK_SOCKET_PATH": "/env/ticks.sock",
            "API_GATEWAY_PORT": "8092",
        },
    )

    assert isinstance(settings, Settings)
    assert settings.connection.default_host == "mt5-test"
    assert settings.connection.mode == "managed"
    assert settings.connection.timeout == 12
    assert settings.tick_service.socket_path == "/env/ticks.sock"
    assert settings.history_service.symbols[0].name == "BTC"
    assert settings.api_gateway.port == 8092
    assert settings.api_gateway.readonly_api_key_env == "TEST_READONLY_KEY"
    assert settings.trade_query.max_days == 30

    # Only the environment-variable name is represented; the loader never
    # reads the value stored under that name.
    assert settings.as_dict()["api_gateway"]["readonly_api_key_env"] == "TEST_READONLY_KEY"


def test_connection_mode_defaults_to_terminal_and_can_be_overridden_by_environment(
    tmp_path,
):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"connection": {}}), encoding="utf-8")

    assert load_settings(path, environ={}).connection.mode == "terminal"
    assert load_settings(
        path, environ={"MT5_CONNECTION_MODE": "managed"}
    ).connection.mode == "managed"


def test_invalid_connection_mode_is_rejected(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"connection": {"mode": "profile"}}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="connection.mode"):
        load_settings(path)


def test_loader_preserves_strict_connection_timeout_contract(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"connection": {"timeout": 0}}), encoding="utf-8")

    with pytest.raises(ValueError, match="connection.timeout must be a positive number"):
        load_settings(path)


def test_runtime_loader_reads_only_supervisor_environment():
    settings = load_runtime_settings(
        environ={"TRADE_DATA_APP_ROOT": "/tmp/app", "SUPERVISOR_SHUTDOWN_TIMEOUT": "2.5"}
    )

    assert settings.app_root == "/tmp/app"
    assert settings.shutdown_timeout == 2.5


def test_runtime_loader_rejects_invalid_timeout():
    with pytest.raises(ValueError, match="SUPERVISOR_SHUTDOWN_TIMEOUT must be numeric"):
        load_runtime_settings(environ={"SUPERVISOR_SHUTDOWN_TIMEOUT": "invalid"})


def test_loader_preserves_explicit_broker_aliases_separately_from_logical_names(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({
        "symbol_aliases": {"XAUUSDm": "XAU_USD", "BTC": "BTCUSD.sim"},
        "tick_service": {"symbols": ["XAUUSDm", "BTC"]},
        "history_service": {"symbols": [{"name": "XAUUSDm", "timeframes": ["M5"]}]},
    }), encoding="utf-8")

    settings = load_settings(path, environ={})
    assert settings.symbol_aliases == {"XAUUSDm": "XAU_USD", "BTC": "BTCUSD.sim"}
    assert settings.tick_service.symbols == ("XAUUSDm", "BTC")
    assert settings.history_service.symbols[0].name == "XAUUSDm"
    assert settings.as_dict()["symbol_aliases"] == settings.symbol_aliases


@pytest.mark.parametrize("aliases", [[], None, {"BTC": ""}, {"BTC": 1}, {"BTC": " BTCUSD"}])
def test_loader_rejects_invalid_alias_mapping(tmp_path, aliases):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"symbol_aliases": aliases}), encoding="utf-8")

    with pytest.raises(ValueError, match="symbol_aliases"):
        load_settings(path, environ={})
