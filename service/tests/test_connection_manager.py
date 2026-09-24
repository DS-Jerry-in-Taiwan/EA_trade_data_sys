import importlib
import json
import sys
import types
from pathlib import Path

import pytest
import yaml


class FakeMT5:
    def __init__(self, initialize_result=True):
        self.initialize_result = initialize_result
        self.initialize_calls = []
        self.shutdown_calls = 0

    def initialize(self, **kwargs):
        self.initialize_calls.append(kwargs)
        return self.initialize_result

    def shutdown(self):
        self.shutdown_calls += 1


class FakeTransport:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class RealisticPymt5linuxMT5(FakeMT5):
    """Models pymt5linux: shutdown is remote-only; RPyC is private."""

    def __init__(self, initialize_result=True):
        super().__init__(initialize_result=initialize_result)
        self._MetaTrader5__conn = FakeTransport()

    @property
    def transport(self):
        return self._MetaTrader5__conn


class DynamicRemoteAttributeMT5(RealisticPymt5linuxMT5):
    def __init__(self):
        super().__init__()
        self.synthetic_close_calls = 0

    def __getattr__(self, name):
        if name == 'close':
            def synthetic_remote_close():
                self.synthetic_close_calls += 1
            return synthetic_remote_close
        raise AttributeError(name)


@pytest.fixture
def connection_manager(monkeypatch):
    pymt5linux = types.ModuleType('pymt5linux')
    pymt5linux.MetaTrader5 = object
    monkeypatch.setitem(sys.modules, 'pymt5linux', pymt5linux)
    sys.modules.pop('core.connection_manager', None)
    return importlib.import_module('core.connection_manager')


@pytest.fixture
def config_files(tmp_path):
    settings_path = tmp_path / 'settings.yaml'
    settings_path.write_text(
        yaml.safe_dump({
            'connection': {
                'default_host': 'mt5-server',
                'port': 8001,
            },
        })
    )
    accounts_path = tmp_path / 'accounts.json'
    accounts_path.write_text(json.dumps({
        'active_provider': 'test',
        'providers': {
            'test': {
                'login': 123,
                'password': 'test-password',
                'server': 'test-server',
            },
        },
    }))
    return settings_path, accounts_path


def make_connector(connection_manager, config_files):
    settings_path, accounts_path = config_files
    return connection_manager.MT5Connector(
        settings_path=str(settings_path),
        accounts_path=str(accounts_path),
    )


def test_connect_uses_only_configured_docker_dns_host(
    connection_manager, config_files, monkeypatch
):
    resolution_calls = []
    constructor_calls = []
    mt5 = FakeMT5()

    monkeypatch.setattr(
        connection_manager.socket,
        'getaddrinfo',
        lambda host, port, **kwargs: resolution_calls.append((host, port)) or [(object(),)],
    )

    def create_mt5(**kwargs):
        constructor_calls.append(kwargs)
        return mt5

    monkeypatch.setattr(connection_manager, 'MetaTrader5', create_mt5)

    result = make_connector(connection_manager, config_files).connect()

    assert result is mt5
    assert resolution_calls == [('mt5-server', 8001)]
    assert constructor_calls == [{'host': 'mt5-server', 'port': 8001}]
    assert mt5.shutdown_calls == 0


def test_resolution_failure_does_not_attempt_another_host(
    connection_manager, config_files, monkeypatch
):
    attempts = []

    def fail_resolution(host, port, **kwargs):
        attempts.append((host, port))
        raise OSError('not resolvable')

    monkeypatch.setattr(connection_manager.socket, 'getaddrinfo', fail_resolution)
    monkeypatch.setattr(
        connection_manager,
        'MetaTrader5',
        lambda **kwargs: pytest.fail('client must not be created after DNS failure'),
    )

    with pytest.raises(ConnectionError, match='Unable to resolve MT5 host mt5-server:8001'):
        make_connector(connection_manager, config_files).connect()

    assert attempts == [('mt5-server', 8001)]


def test_initialization_failure_closes_partial_client(
    connection_manager, config_files, monkeypatch
):
    mt5 = RealisticPymt5linuxMT5(initialize_result=False)
    monkeypatch.setattr(
        connection_manager.socket, 'getaddrinfo', lambda *args, **kwargs: [(object(),)]
    )
    monkeypatch.setattr(connection_manager, 'MetaTrader5', lambda **kwargs: mt5)

    with pytest.raises(ConnectionError, match='MT5 initialization failed for mt5-server:8001'):
        make_connector(connection_manager, config_files).connect()

    assert mt5.shutdown_calls == 1
    assert mt5.transport.close_calls == 1


def test_compatibility_close_is_idempotent_for_private_rpyc_transport(
    connection_manager,
):
    mt5 = RealisticPymt5linuxMT5()

    connection_manager.close_mt5_connection(mt5)
    connection_manager.close_mt5_connection(mt5)

    assert mt5.shutdown_calls == 1
    assert mt5.transport.close_calls == 1


def test_compatibility_close_ignores_synthetic_remote_close_attribute(
    connection_manager,
):
    mt5 = DynamicRemoteAttributeMT5()

    connection_manager.close_mt5_connection(mt5)

    assert mt5.synthetic_close_calls == 0
    assert mt5.transport.close_calls == 1


def test_proxy_connection_failure_is_reported_for_only_configured_host(
    connection_manager, config_files, monkeypatch
):
    attempts = []
    monkeypatch.setattr(
        connection_manager.socket, 'getaddrinfo', lambda *args, **kwargs: [(object(),)]
    )

    def fail_connection(**kwargs):
        attempts.append(kwargs)
        raise OSError('connection refused')

    monkeypatch.setattr(connection_manager, 'MetaTrader5', fail_connection)

    with pytest.raises(ConnectionError, match='Unable to connect to MT5 at mt5-server:8001'):
        make_connector(connection_manager, config_files).connect()

    assert attempts == [{'host': 'mt5-server', 'port': 8001}]


def test_production_settings_have_no_fixed_ip_or_fallback_hosts():
    settings_path = Path(__file__).parents[1] / 'config' / 'settings.yaml'
    raw_settings = settings_path.read_text()
    settings = yaml.safe_load(raw_settings)

    assert settings['connection']['default_host'] == 'mt5-server'
    assert settings['connection']['port'] == 8001
    assert 'fallback_hosts' not in settings['connection']
    assert '172.21.0.' not in raw_settings
