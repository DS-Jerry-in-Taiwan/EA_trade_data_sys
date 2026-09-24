import threading
import sys
import types

import pytest

# Keep these unit tests independent of the container-only pymt5linux package.
connection_manager = types.ModuleType('core.connection_manager')
connection_manager.MT5Connector = object
sys.modules.setdefault('core.connection_manager', connection_manager)

from service.core.mt5_client import MT5Client


class FakeMT5:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


class FakeConnector:
    def __init__(self, connection):
        self.connection = connection
        self.connect_calls = 0
        self.close_calls = 0

    def connect(self):
        self.connect_calls += 1
        return self.connection

    def close(self):
        self.close_calls += 1


class FailingConnector(FakeConnector):
    def connect(self):
        self.connect_calls += 1
        raise OSError('connection lost')


class ConnectorFactory:
    def __init__(self, connections):
        self.connections = iter(connections)
        self.created = []

    def __call__(self):
        connector = FakeConnector(next(self.connections))
        self.created.append(connector)
        return connector


def test_concurrent_lazy_connect_creates_one_session():
    factory = ConnectorFactory([FakeMT5()])
    client = MT5Client(factory)
    barrier = threading.Barrier(8)

    def connect():
        barrier.wait()
        assert client.ensure_connected()

    threads = [threading.Thread(target=connect) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(factory.created) == 1
    assert factory.created[0].connect_calls == 1


def test_concurrent_recovery_creates_one_replacement_session():
    first, replacement = FakeMT5(), FakeMT5()
    factory = ConnectorFactory([first, replacement])
    client = MT5Client(factory)
    assert client.ensure_connected()
    client.reset()
    barrier = threading.Barrier(8)

    def recover():
        barrier.wait()
        assert client.ensure_connected()

    threads = [threading.Thread(target=recover) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(factory.created) == 2
    assert factory.created[1].connect_calls == 1
    assert client.mt5 is replacement


def test_reset_and_shutdown_close_once_and_are_idempotent():
    mt5 = FakeMT5()
    factory = ConnectorFactory([mt5])
    client = MT5Client(factory)
    assert client.ensure_connected()
    client.reset()
    client.shutdown()
    assert mt5.shutdown_calls == 1
    assert factory.created[0].close_calls == 1


def test_failed_call_closes_session_and_next_call_reconnects():
    first, second = FakeMT5(), FakeMT5()
    factory = ConnectorFactory([first, second])
    client = MT5Client(factory)
    with pytest.raises(RuntimeError, match='broken transport'):
        client.call(lambda _: (_ for _ in ()).throw(RuntimeError('broken transport')))
    assert first.shutdown_calls == 1
    assert client.call(lambda mt5: mt5) is second
    assert len(factory.created) == 2


def test_failed_connect_cleans_connector_and_can_retry():
    factory = ConnectorFactory([None, FakeMT5()])
    client = MT5Client(factory)
    assert client.ensure_connected() is False
    assert factory.created[0].close_calls == 1
    assert client.ensure_connected() is True


def test_connect_exception_closes_connector():
    connector = FailingConnector(None)
    client = MT5Client(lambda: connector)
    with pytest.raises(OSError, match='connection lost'):
        client.ensure_connected()
    assert connector.close_calls == 1


def test_resolver_configuration_is_refreshed_after_reconnect(monkeypatch):
    first, second = FakeMT5(), FakeMT5()
    factory = ConnectorFactory([first, second])
    client = MT5Client(factory)
    events = []

    class FakeResolver:
        def initialize(self, mt5, symbols):
            events.append(('initialize', mt5, tuple(symbols)))

        def refresh(self, mt5, symbols):
            events.append(('refresh', mt5, tuple(symbols)))

        def resolve(self, name):
            return name

    monkeypatch.setattr('service.core.symbol_resolver.SymbolResolver', FakeResolver)
    assert client.ensure_connected()
    client.init_resolver(['XAUUSDm', 'BTC'])
    client.reset()
    assert client.ensure_connected()
    assert events == [
        ('initialize', first, ('XAUUSDm', 'BTC')),
        ('refresh', second, ('XAUUSDm', 'BTC')),
    ]
