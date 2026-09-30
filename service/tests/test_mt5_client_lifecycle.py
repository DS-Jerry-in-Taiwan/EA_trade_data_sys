import threading
import sys
import types

import pytest

# Keep these unit tests independent of the container-only pymt5linux package.
connection_manager = types.ModuleType('core.connection_manager')
connection_manager.MT5Connector = object


def close_mt5_connection(mt5):
    if mt5 is not None and not getattr(mt5, '_closed', False):
        mt5._closed = True
        mt5.shutdown()
        transport = getattr(mt5, '_MetaTrader5__conn', None)
        if transport is not None:
            transport.close()


connection_manager.close_mt5_connection = close_mt5_connection
sys.modules.setdefault('core.connection_manager', connection_manager)

from service.infrastructure.mt5.client import MT5Client


class FakeMT5:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


class FakeTransport:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class RealisticPymt5linuxMT5(FakeMT5):
    def __init__(self):
        super().__init__()
        self._MetaTrader5__conn = FakeTransport()

    @property
    def transport(self):
        return self._MetaTrader5__conn


class SessionMT5(RealisticPymt5linuxMT5):
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, info):
        super().__init__()
        self.info = info

    def account_info(self):
        return self.info


class Resolver:
    def __init__(self, unresolved=()):
        self.unresolved = list(unresolved)
        self.mapping = {}
        self.initialized_with = []

    def initialize(self, mt5, symbols):
        self.initialized_with.append((mt5, tuple(symbols)))
        self.mapping = {symbol: symbol for symbol in symbols if symbol not in self.unresolved}

    def refresh(self, mt5, symbols):
        self.initialize(mt5, symbols)

    def resolve(self, name):
        return self.mapping.get(name, name)


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


class ExpectedFailingConnector(FakeConnector):
    def connect(self):
        self.connect_calls += 1
        raise ConnectionError('MT5 unavailable')


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
    mt5 = RealisticPymt5linuxMT5()
    factory = ConnectorFactory([mt5])
    client = MT5Client(factory)
    assert client.ensure_connected()
    client.reset()
    client.shutdown()
    assert mt5.shutdown_calls == 1
    assert mt5.transport.close_calls == 1
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


def test_unexpected_connect_exception_still_raises_and_cleans_connector():
    connector = FailingConnector(None)
    client = MT5Client(lambda: connector)
    with pytest.raises(OSError, match='connection lost'):
        client.ensure_connected()
    assert connector.close_calls == 1


def test_expected_connection_error_returns_false_and_cleans_connector():
    connector = ExpectedFailingConnector(None)
    client = MT5Client(lambda: connector)

    assert client.ensure_connected() is False
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

    monkeypatch.setattr('service.infrastructure.mt5.symbol_resolver.SymbolResolver', FakeResolver)
    assert client.ensure_connected()
    client.init_resolver(['XAUUSDm', 'BTC'])
    client.reset()
    assert client.ensure_connected()
    assert events == [
        ('initialize', first, ('XAUUSDm', 'BTC')),
        ('refresh', second, ('XAUUSDm', 'BTC')),
    ]


def test_client_blocks_account_switch_until_session_reconciliation():
    from types import SimpleNamespace

    first = SessionMT5(SimpleNamespace(login=123, server='Demo', trade_mode=0))
    client = MT5Client(ConnectorFactory([first]))
    assert client.ensure_connected()
    first.info = SimpleNamespace(login=456, server='Demo', trade_mode=0)

    assert client.ensure_connected() is False
    status = client.session_status()
    assert status['state'] == 'switch_detected'
    assert status['ready'] is False
    assert status['generation'] == 2

    assert client.reconcile_session()['ready'] is True
    assert client.ensure_connected() is True


def test_client_marks_unknown_account_mode_not_ready():
    from types import SimpleNamespace

    mt5 = SessionMT5(SimpleNamespace(login=123, server='Demo', trade_mode=99))
    client = MT5Client(ConnectorFactory([mt5]))

    assert client.ensure_connected() is False
    status = client.session_status()
    assert status['state'] == 'unknown'
    assert status['ready'] is False
    assert status['error'] == 'account_mode_unknown'


def test_account_switch_invalidates_resolver_and_rebuilds_before_ready():
    from types import SimpleNamespace

    mt5 = SessionMT5(SimpleNamespace(login=123, server='Demo', trade_mode=0))
    old_resolver, new_resolver = Resolver(), Resolver()
    resolvers = iter([old_resolver, new_resolver])
    client = MT5Client(ConnectorFactory([mt5]), resolver_factory=lambda: next(resolvers))
    assert client.ensure_connected()
    client.init_resolver(['XAUUSDm'])
    assert client._resolver is old_resolver

    mt5.info = SimpleNamespace(login=456, server='OANDA-Demo-1', trade_mode=0)
    assert client.ensure_connected() is False
    assert client._resolver is None
    with pytest.raises(ConnectionError, match='transition requires reconciliation'):
        client.call(lambda current: current)

    status = client.reconcile_session()
    assert status['ready'] is True
    assert client._resolver is new_resolver
    assert new_resolver.initialized_with == [(mt5, ('XAUUSDm',))]
    assert client.call(lambda current: current) is mt5


def test_client_discards_read_that_spans_account_generation_change():
    from types import SimpleNamespace

    mt5 = SessionMT5(SimpleNamespace(login=123, server='Demo', trade_mode=0))
    client = MT5Client(ConnectorFactory([mt5]))
    assert client.ensure_connected()

    def read_and_switch(current):
        current.info = SimpleNamespace(login=456, server='OANDA-Demo-1', trade_mode=0)
        return "stale-read"

    with pytest.raises(ConnectionError, match='transition'):
        client.call(read_and_switch)
    assert client.session_status()['state'] == 'switch_detected'


def test_reconnect_to_new_account_keeps_session_for_reconciliation():
    from types import SimpleNamespace

    first = SessionMT5(SimpleNamespace(login=123, server='Demo', trade_mode=0))
    replacement = SessionMT5(
        SimpleNamespace(login=456, server='OANDA-Demo-1', trade_mode=0)
    )
    old_resolver, new_resolver = Resolver(), Resolver()
    resolvers = iter([old_resolver, new_resolver])
    client = MT5Client(
        ConnectorFactory([first, replacement]),
        resolver_factory=lambda: next(resolvers),
    )
    assert client.ensure_connected()
    client.init_resolver(['XAUUSDm'])
    client.reset()

    assert client.ensure_connected() is False
    assert client.mt5 is replacement
    assert client.session_status()['state'] == 'switch_detected'
    assert client.reconcile_session()['ready'] is True
    assert client._resolver is new_resolver


def test_reconciliation_missing_symbols_keeps_reads_not_ready():
    from types import SimpleNamespace

    mt5 = SessionMT5(SimpleNamespace(login=123, server='Demo', trade_mode=0))
    old_resolver, missing_resolver = Resolver(), Resolver(['BTC'])
    resolvers = iter([old_resolver, missing_resolver])
    client = MT5Client(ConnectorFactory([mt5]), resolver_factory=lambda: next(resolvers))
    assert client.ensure_connected()
    client.init_resolver(['BTC'])

    mt5.info = SimpleNamespace(login=456, server='OANDA-Demo-1', trade_mode=0)
    assert client.ensure_connected() is False
    status = client.reconcile_session()
    assert status['ready'] is False
    assert status['state'] == 'switch_detected'
    assert status['error'] == 'account_symbols_unavailable'
    assert client._resolver is None
    assert client.ensure_connected() is False
    assert client.session_status()['error'] == 'account_symbols_unavailable'


def test_reconciliation_failure_keeps_transition_blocked():
    from types import SimpleNamespace

    mt5 = SessionMT5(SimpleNamespace(login=123, server='Demo', trade_mode=0))
    old_resolver = Resolver()

    class FailingResolver(Resolver):
        def initialize(self, mt5, symbols):
            raise RuntimeError('symbols unavailable')

    resolvers = iter([old_resolver, FailingResolver()])
    client = MT5Client(ConnectorFactory([mt5]), resolver_factory=lambda: next(resolvers))
    assert client.ensure_connected()
    client.init_resolver(['BTC'])
    mt5.info = SimpleNamespace(login=456, server='OANDA-Demo-1', trade_mode=0)
    assert client.ensure_connected() is False

    status = client.reconcile_session()
    assert status['ready'] is False
    assert status['error'] == 'account_reconciliation_failed'
    with pytest.raises(ConnectionError, match='transition requires reconciliation'):
        client.call(lambda current: current)
