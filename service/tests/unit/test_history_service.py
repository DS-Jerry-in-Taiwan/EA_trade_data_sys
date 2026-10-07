import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import numpy as np
import pytest
import yaml

from service.history.worker import HistoryService
from service.infrastructure.mt5.client import MT5Client


def _bar(timestamp, close=100.0):
    return {
        'time': int(pd.Timestamp(timestamp).timestamp()),
        'open': close,
        'high': close + 1,
        'low': close - 1,
        'close': close,
        'tick_volume': 10,
    }


class FakeMT5:
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_D1 = 1440

    def __init__(self, rates):
        self.rates = rates
        self.requests = []

    def copy_rates_from_pos(self, symbol, timeframe, position, count):
        self.requests.append((symbol, timeframe, position, count))
        return self.rates[position:position + count]


class FakeMT5Client:
    def __init__(self, rates):
        self.mt5 = FakeMT5(rates)
        self.initialized_symbols = None

    def ensure_connected(self):
        return True

    def init_resolver(self, symbols):
        self.initialized_symbols = symbols

    def resolve(self, symbol):
        return f'broker-{symbol}'

    def is_resolved(self, symbol):
        return True

    def call(self, operation):
        return operation(self.mt5)


class AccountMT5(FakeMT5):
    """A terminal whose catalog and history change with its current account."""

    def __init__(self, aliases=('BTCUSDm', 'BTC_USD')):
        super().__init__([_bar('2026-09-08T10:00:00Z')])
        self.login = 1
        self.aliases = aliases
        self.read_accounts = []

    def account_info(self):
        return SimpleNamespace(login=self.login, server='test-demo', trade_mode=0)

    def symbols_get(self):
        return [SimpleNamespace(name=self.aliases[self.login - 1])]

    def copy_rates_from_pos(self, symbol, timeframe, position, count):
        self.requests.append((symbol, timeframe, position, count))
        self.read_accounts.append(self.login)
        close = 100 * self.login if symbol == self.aliases[self.login - 1] else 999
        bars = [
            _bar('2026-09-08T10:00:00Z', close=close),
            _bar('2026-09-08T10:15:00Z', close=close),
        ]
        return bars[position:position + count]

    def shutdown(self):
        pass


def _account_client(mt5, client_type=MT5Client):
    return client_type(connector_factory=lambda: SimpleNamespace(
        connect=lambda: mt5, close=lambda: None,
    ))


def _write_config(path, data_path, timeframes=None, **history_overrides):
    history = {
        'symbols': [
            {'name': 'BTC', 'timeframes': timeframes or ['M5', 'M15', 'H1', 'D1']}
        ],
        'data_path': str(data_path),
        'status_path': str(path.parent / 'history-status.json'),
    }
    history.update(history_overrides)
    path.write_text(yaml.safe_dump({'history_service': history}), encoding='utf-8')


def _temporary_csv_files(directory):
    return [path for path in directory.iterdir() if path.name.endswith('.tmp')]


def test_default_minimums_include_d1_and_fetch_margin(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    _write_config(config_path, tmp_path / 'history')

    service = HistoryService(config_path=config_path, mt5_client=FakeMT5Client([]))

    assert service.DEFAULT_MINIMUM_BARS == {
        'M5': 500,
        'M15': 200,
        'H1': 500,
        'D1': 200,
    }
    assert set(service.SUPPORTED_TIMEFRAMES) == {'M5', 'M15', 'H1', 'D1'}
    assert service._fetch_count('M5') == 521
    assert service._fetch_count('D1') == 221


def test_configured_minimums_control_every_refresh_request(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['D1'],
        minimum_bars={'D1': 205},
        fetch_margin_bars=5,
    )
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    client = FakeMT5Client(
        [_bar('2026-09-06T00:00:00Z'), _bar('2026-09-08T00:00:00Z')]
    )
    service = HistoryService(config_path=config_path, mt5_client=client)

    service.fetch_incremental(now=now)

    assert client.mt5.requests == [
        ('broker-BTC', 1440, 0, 211),
        ('broker-BTC', 1440, 2, 209),
    ]
    saved = pd.read_csv(tmp_path / 'history' / 'BTC_D1.csv')
    assert len(saved) == 1
    assert saved.iloc[0]['time'].startswith('2026-09-06')


def test_unresolved_symbol_fails_closed_without_querying_or_reusing_cache(tmp_path):
    class UnresolvedClient(FakeMT5Client):
        def is_resolved(self, symbol):
            return False

    config_path = tmp_path / 'settings.yaml'
    history_path = tmp_path / 'history'
    _write_config(
        config_path,
        history_path,
        timeframes=['M5'],
        minimum_bars={'M5': 1},
    )
    history_path.mkdir()
    (history_path / 'BTC_M5.csv').write_text(
        'time,open,high,low,close,tick_volume\n'
        '2026-09-08T10:00:00Z,1,2,0,1,1\n',
        encoding='utf-8',
    )
    client = UnresolvedClient([_bar('2026-09-08T10:00:00Z')])
    service = HistoryService(config_path=config_path, mt5_client=client)

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    assert client.mt5.requests == []
    assert service.get_sync_status()['BTC:M5']['state'] == 'error'
    assert service.get_sync_status()['BTC:M5']['detail'] == 'broker symbol unresolved'


def test_reconnect_reinitializes_resolver(tmp_path):
    class ReconnectingClient(FakeMT5Client):
        def __init__(self, rates):
            super().__init__(rates)
            self._rates = rates

        def ensure_connected(self):
            if self.mt5 is None:
                self.mt5 = FakeMT5(self._rates)
            return True

    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['M5'],
        minimum_bars={'M5': 1},
        retention_margin_bars=0,
    )
    client = ReconnectingClient([_bar('2026-09-08T10:00:00Z')])
    service = HistoryService(config_path=config_path, mt5_client=client)

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))
    client.initialized_symbols = None
    client.mt5 = None
    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    assert client.initialized_symbols == ['BTC']


@pytest.mark.parametrize(
    ('timeframe', 'closed_time', 'forming_time'),
    [
        ('M5', '2026-09-08T11:55:00Z', '2026-09-08T12:00:00Z'),
        ('M15', '2026-09-08T11:45:00Z', '2026-09-08T12:00:00Z'),
        ('H1', '2026-09-08T11:00:00Z', '2026-09-08T12:00:00Z'),
        ('D1', '2026-09-07T00:00:00Z', '2026-09-08T00:00:00Z'),
    ],
)
def test_keep_closed_bars_uses_bar_open_plus_duration(
    timeframe, closed_time, forming_time
):
    frame = pd.DataFrame([_bar(closed_time), _bar(forming_time)])

    result = HistoryService._keep_closed_bars(
        frame,
        timeframe,
        now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
    )

    assert len(result) == 1
    assert result.iloc[0]['time'] == pd.Timestamp(closed_time)


def test_merge_is_closed_sorted_and_deduplicated_with_new_value_winning():
    old = pd.DataFrame(
        [_bar('2026-09-08T10:05:00Z', close=101), _bar('2026-09-08T10:00:00Z')]
    )
    new = pd.DataFrame(
        [_bar('2026-09-08T10:10:00Z'), _bar('2026-09-08T10:05:00Z', close=999)]
    )

    result = HistoryService._merge_bars(
        old,
        new,
        'M5',
        now=datetime(2026, 9, 8, 11, tzinfo=timezone.utc),
    )

    assert result['time'].tolist() == [
        pd.Timestamp('2026-09-08T10:00:00Z'),
        pd.Timestamp('2026-09-08T10:05:00Z'),
        pd.Timestamp('2026-09-08T10:10:00Z'),
    ]
    assert result.loc[result['time'] == pd.Timestamp('2026-09-08T10:05:00Z'), 'close'].item() == 999


def test_atomic_write_replaces_target_from_same_directory(tmp_path, monkeypatch):
    target = tmp_path / 'BTC_M5.csv'
    target.write_text('old-data', encoding='utf-8')
    seen = {}
    real_replace = __import__('os').replace

    def recording_replace(source, destination):
        seen['source'] = Path(source)
        seen['destination'] = Path(destination)
        real_replace(source, destination)

    monkeypatch.setattr('service.history.worker.os.replace', recording_replace)
    frame = pd.DataFrame([_bar('2026-09-08T10:00:00Z')])

    HistoryService._atomic_write_csv(frame, str(target))

    assert seen['source'].parent == target.parent
    assert seen['destination'] == target
    assert pd.read_csv(target).shape[0] == 1
    assert not _temporary_csv_files(tmp_path)


def test_atomic_write_failure_preserves_target_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / 'BTC_M5.csv'
    target.write_text('old-data', encoding='utf-8')

    def failing_replace(source, destination):
        raise OSError('replace failed')

    monkeypatch.setattr('service.history.worker.os.replace', failing_replace)
    frame = pd.DataFrame([_bar('2026-09-08T10:00:00Z')])

    with pytest.raises(OSError, match='replace failed'):
        HistoryService._atomic_write_csv(frame, str(target))

    assert target.read_text(encoding='utf-8') == 'old-data'
    assert not _temporary_csv_files(tmp_path)


def test_bootstrap_collects_short_pages_until_target(tmp_path):
    class ShortPageMT5(FakeMT5):
        def copy_rates_from_pos(self, symbol, timeframe, position, count):
            self.requests.append((symbol, timeframe, position, count))
            return self.rates[position:position + min(count, 2)]

    class ShortPageClient(FakeMT5Client):
        def __init__(self, rates):
            self.mt5 = ShortPageMT5(rates)
            self.initialized_symbols = None

    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['M5'],
        minimum_bars={'M5': 5},
        retention_margin_bars=1,
        fetch_page_size=5,
    )
    bars = [_bar(f'2026-09-08T10:{minute:02d}:00Z') for minute in range(0, 35, 5)]
    client = ShortPageClient(bars)
    service = HistoryService(config_path=config_path, mt5_client=client)

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    assert [request[2:] for request in client.mt5.requests] == [
        (0, 5), (2, 5), (4, 3), (6, 1)
    ]
    saved = pd.read_csv(tmp_path / 'history' / 'BTC_M5.csv')
    assert len(saved) == 6
    assert service.get_sync_status()['BTC:M5']['state'] == 'ready'
    assert service.get_sync_status()['BTC:M5']['source_exhausted'] is False


def test_mt5_structured_array_preserves_named_columns(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['M5'],
        minimum_bars={'M5': 1},
        retention_margin_bars=0,
    )
    dtype = [
        ('time', 'i8'), ('open', 'f8'), ('high', 'f8'), ('low', 'f8'),
        ('close', 'f8'), ('tick_volume', 'i8'),
    ]
    rates = np.array(
        [(int(pd.Timestamp('2026-09-08T10:00:00Z').timestamp()), 100, 101, 99, 100.5, 10)],
        dtype=dtype,
    )
    service = HistoryService(
        config_path=config_path,
        mt5_client=FakeMT5Client(rates),
    )

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    saved = pd.read_csv(tmp_path / 'history' / 'BTC_M5.csv')
    assert list(saved.columns) == [name for name, _ in dtype] + ['source_symbol']
    assert set(saved['source_symbol']) == {'broker-BTC'}
    assert service.get_sync_status()['BTC:M5']['state'] == 'ready'


def test_incremental_merge_preserves_iso_cache_and_new_epoch_bar(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(
        config_path,
        data_path,
        timeframes=['M5'],
        minimum_bars={'M5': 2},
        retention_margin_bars=0,
    )
    data_path.mkdir()
    pd.DataFrame([_bar('2026-09-08T10:00:00Z')]).assign(
        time='2026-09-08 10:00:00+00:00', source_symbol='broker-BTC'
    ).to_csv(data_path / 'BTC_M5.csv', index=False)
    dtype = [
        ('time', 'i8'), ('open', 'f8'), ('high', 'f8'), ('low', 'f8'),
        ('close', 'f8'), ('tick_volume', 'i8'),
    ]
    epoch = int(pd.Timestamp('2026-09-08T10:05:00Z').timestamp())
    rates = np.array([(epoch, 100, 101, 99, 100.5, 10)], dtype=dtype)
    service = HistoryService(config_path=config_path, mt5_client=FakeMT5Client(rates))

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    saved = pd.read_csv(data_path / 'BTC_M5.csv')
    assert pd.to_datetime(saved['time'], utc=True).tolist() == [
        pd.Timestamp('2026-09-08T10:00:00Z'),
        pd.Timestamp('2026-09-08T10:05:00Z'),
    ]
    assert service.get_sync_status()['BTC:M5']['state'] == 'ready'


def test_changed_source_identity_rebuilds_without_mixing_instruments(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(
        config_path,
        data_path,
        timeframes=['M5'],
        minimum_bars={'M5': 1},
        retention_margin_bars=0,
    )
    data_path.mkdir()
    pd.DataFrame([_bar('2026-09-08T10:00:00Z')]).assign(
        source_symbol='BTCUSDTm'
    ).to_csv(data_path / 'BTC_M5.csv', index=False)
    client = FakeMT5Client([_bar('2026-09-08T10:05:00Z')])
    service = HistoryService(config_path=config_path, mt5_client=client)

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    saved = pd.read_csv(data_path / 'BTC_M5.csv')
    assert len(saved) == 1
    assert saved.iloc[0]['time'].startswith('2026-09-08 10:05')
    assert set(saved['source_symbol']) == {'broker-BTC'}
    assert (data_path / 'BTC_M5.csv.ready').read_text() == 'broker-BTC'


def test_account_switch_before_read_uses_reconciled_alias_for_later_timeframe(tmp_path):
    class SwitchBeforeReadClient(MT5Client):
        first_read = True

        def call(self, operation):
            if self.first_read:
                self.first_read = False
                terminal.login = 2
            return super().call(operation)

    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(
        config_path, data_path, timeframes=['M5', 'M15'],
        minimum_bars={'M5': 1, 'M15': 1}, retention_margin_bars=0,
    )
    data_path.mkdir()
    ready = data_path / 'BTC_M5.csv.ready'
    ready.write_text('BTCUSDm', encoding='utf-8')
    terminal = AccountMT5()
    client = _account_client(terminal, SwitchBeforeReadClient)
    service = HistoryService(config_path=config_path, mt5_client=client)
    try:
        service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

        assert not ready.exists()
        assert not (data_path / 'BTC_M5.csv').exists()
        saved = pd.read_csv(data_path / 'BTC_M15.csv')
        assert set(saved['source_symbol']) == {'BTC_USD'}
        assert set(saved['close']) == {200}
        assert {request[0] for request in terminal.requests} == {'BTC_USD'}
        assert terminal.read_accounts == [2]
        assert (data_path / 'BTC_M15.csv.ready').read_text() == 'BTC_USD'
        status = service.get_sync_status()['BTC:M15']
        assert status['source_symbol'] == 'BTC_USD'
        assert status['session_generation'] == client.session_guard.generation == 2
    finally:
        service.close()


@pytest.mark.parametrize('new_alias', ['BTC_USD', 'BTCUSDm'])
@pytest.mark.parametrize(
    ('page_size', 'minimum', 'error'),
    [(1, 2, 'changed between history pages'),
     (2, 1, 'changed before history publication')],
)
def test_account_generation_change_never_publishes_mixed_history(
    tmp_path, new_alias, page_size, minimum, error
):
    class ReconcileBetweenPagesClient(MT5Client):
        first_read = True

        def call(self, operation):
            result = super().call(operation)
            if self.first_read:
                self.first_read = False
                terminal.login = 2
                assert self.ensure_connected() is False
                assert self.session_guard.ready is True
            return result

    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(
        config_path, data_path, timeframes=['M5'], minimum_bars={'M5': minimum},
        retention_margin_bars=0, fetch_page_size=page_size,
    )
    data_path.mkdir()
    ready = data_path / 'BTC_M5.csv.ready'
    ready.write_text('BTCUSDm', encoding='utf-8')
    terminal = AccountMT5(aliases=('BTCUSDm', new_alias))
    client = _account_client(terminal, ReconcileBetweenPagesClient)
    service = HistoryService(config_path=config_path, mt5_client=client)
    try:
        service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

        assert terminal.requests == [('BTCUSDm', 5, 0, page_size)]
        assert terminal.read_accounts == [1]
        assert not (data_path / 'BTC_M5.csv').exists()
        assert not ready.exists()
        status = service.get_sync_status()['BTC:M5']
        assert status['state'] == 'error'
        assert error in status['detail']
    finally:
        service.close()


def test_account_switch_during_csv_write_revokes_publication(tmp_path, monkeypatch):
    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(
        config_path, data_path, timeframes=['M5'], minimum_bars={'M5': 1},
        retention_margin_bars=0,
    )
    data_path.mkdir()
    ready = data_path / 'BTC_M5.csv.ready'
    ready.write_text('BTCUSDm', encoding='utf-8')
    terminal = AccountMT5()
    client = _account_client(terminal)
    service = HistoryService(config_path=config_path, mt5_client=client)
    write_csv = service._atomic_write_csv

    def write_then_reconcile(dataframe, filepath):
        write_csv(dataframe, filepath)
        terminal.login = 2
        assert client.ensure_connected() is False

    monkeypatch.setattr(service, '_atomic_write_csv', write_then_reconcile)
    try:
        service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

        assert not ready.exists()
        status = service.get_sync_status()['BTC:M5']
        assert status['state'] == 'error'
        assert 'changed before history publication' in status['detail']
    finally:
        service.close()


def test_legacy_cache_without_source_identity_is_rebuilt(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(
        config_path,
        data_path,
        timeframes=['M5'],
        minimum_bars={'M5': 1},
        retention_margin_bars=0,
    )
    data_path.mkdir()
    pd.DataFrame([_bar('2026-09-08T10:00:00Z')]).to_csv(
        data_path / 'BTC_M5.csv', index=False
    )
    service = HistoryService(
        config_path=config_path,
        mt5_client=FakeMT5Client([_bar('2026-09-08T10:05:00Z')]),
    )

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    saved = pd.read_csv(data_path / 'BTC_M5.csv')
    assert len(saved) == 1
    assert set(saved['source_symbol']) == {'broker-BTC'}


def test_changed_identity_with_empty_fetch_revokes_ready_marker(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(
        config_path,
        data_path,
        timeframes=['M5'],
        minimum_bars={'M5': 1},
        retention_margin_bars=0,
    )
    data_path.mkdir()
    path = data_path / 'BTC_M5.csv'
    pd.DataFrame([_bar('2026-09-08T10:00:00Z')]).assign(
        source_symbol='BTCUSDTm'
    ).to_csv(path, index=False)
    (data_path / 'BTC_M5.csv.ready').write_text('BTCUSDTm', encoding='utf-8')
    service = HistoryService(config_path=config_path, mt5_client=FakeMT5Client([]))

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    assert path.exists()
    assert not (data_path / 'BTC_M5.csv.ready').exists()
    assert service.get_sync_status()['BTC:M5']['state'] == 'insufficient'


def test_unresolved_symbol_revokes_existing_ready_marker(tmp_path):
    class UnresolvedClient(FakeMT5Client):
        def is_resolved(self, symbol):
            return False

    config_path = tmp_path / 'settings.yaml'
    data_path = tmp_path / 'history'
    _write_config(config_path, data_path, timeframes=['M5'])
    data_path.mkdir()
    path = data_path / 'BTC_M5.csv'
    pd.DataFrame([_bar('2026-09-08T10:00:00Z')]).assign(
        source_symbol='BTCUSDTm'
    ).to_csv(path, index=False)
    ready = data_path / 'BTC_M5.csv.ready'
    ready.write_text('BTCUSDTm', encoding='utf-8')
    service = HistoryService(config_path=config_path, mt5_client=UnresolvedClient([]))

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    assert not ready.exists()


def test_source_exhaustion_is_explicitly_insufficient(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['M5'],
        minimum_bars={'M5': 5},
        fetch_page_size=2,
    )
    client = FakeMT5Client([_bar('2026-09-08T10:00:00Z')])
    service = HistoryService(config_path=config_path, mt5_client=client)

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    status = service.get_sync_status()['BTC:M5']
    assert status['state'] == 'insufficient'
    assert status['bars'] == 1
    assert status['source_exhausted'] is True
    assert not (tmp_path / 'history' / 'BTC_M5.csv.ready').exists()


def test_retention_is_bounded_to_minimum_plus_margin(tmp_path):
    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['M5'],
        minimum_bars={'M5': 3},
        retention_margin_bars=2,
        fetch_page_size=10,
    )
    bars = [_bar(f'2026-09-08T10:{minute:02d}:00Z') for minute in range(0, 30, 5)]
    service = HistoryService(config_path=config_path, mt5_client=FakeMT5Client(bars))

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    saved = pd.read_csv(tmp_path / 'history' / 'BTC_M5.csv')
    assert len(saved) == 5
    assert saved.iloc[0]['time'].startswith('2026-09-08 10:05')


def test_page_timeout_sets_error_status_without_writing(tmp_path):
    class SlowClient(FakeMT5Client):
        def call(self, operation):
            time.sleep(0.05)
            return operation(self.mt5)

    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['M5'],
        minimum_bars={'M5': 1},
        fetch_timeout_seconds=0.001,
    )
    service = HistoryService(
        config_path=config_path,
        mt5_client=SlowClient([_bar('2026-09-08T10:00:00Z')]),
    )

    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    status = service.get_sync_status()['BTC:M5']
    assert status['state'] == 'error'
    assert 'timed out' in status['detail']
    assert not (tmp_path / 'history' / 'BTC_M5.csv').exists()


def test_repeated_refresh_does_not_queue_calls_behind_timed_out_request(tmp_path):
    class SlowClient(FakeMT5Client):
        def __init__(self, rates):
            super().__init__(rates)
            self.calls = 0

        def call(self, operation):
            self.calls += 1
            # Keep the first call in flight well beyond status-file fsync
            # variance so this test verifies queueing, not host I/O speed.
            time.sleep(0.5)
            return operation(self.mt5)

    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path,
        tmp_path / 'history',
        timeframes=['M5'],
        minimum_bars={'M5': 1},
        fetch_timeout_seconds=0.001,
    )
    client = SlowClient([_bar('2026-09-08T10:00:00Z')])
    service = HistoryService(config_path=config_path, mt5_client=client)

    for _ in range(6):
        service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))

    assert client.calls == 1
    assert service.get_sync_status()['BTC:M5']['state'] == 'error'
    assert 'still running' in service.get_sync_status()['BTC:M5']['detail']


def test_timed_out_guarded_read_does_not_block_other_timeframes_or_refresh(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingMT5(AccountMT5):
        def copy_rates_from_pos(self, *args):
            entered.set()
            assert release.wait(timeout=5)
            return super().copy_rates_from_pos(*args)

    config_path = tmp_path / 'settings.yaml'
    _write_config(
        config_path, tmp_path / 'history', timeframes=['M5', 'M15'],
        minimum_bars={'M5': 1, 'M15': 1}, fetch_timeout_seconds=0.01,
    )
    terminal = BlockingMT5()
    service = HistoryService(config_path=config_path, mt5_client=_account_client(terminal))

    def refresh_twice():
        try:
            for _ in range(2):
                service.fetch_incremental(
                    now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
                )
        finally:
            finished.set()

    thread = threading.Thread(target=refresh_twice)
    thread.start()
    try:
        assert entered.wait(timeout=1)
        # The real MT5Client lock is still held by the stalled first read.
        assert finished.wait(timeout=1)
        assert service._inflight_future is not None
        assert not service._inflight_future.done()
        assert all(status['state'] == 'error' for status in service.sync_status.values())
    finally:
        release.set()
        thread.join(timeout=2)
        service._executor.shutdown(wait=True)
        service.close()
    assert len(terminal.requests) == 1


def test_status_publication_failure_does_not_stop_history_worker(tmp_path, monkeypatch):
    config_path = tmp_path / 'settings.yaml'
    _write_config(config_path, tmp_path / 'history')
    service = HistoryService(config_path=str(config_path), mt5_client=FakeMT5Client([]))
    monkeypatch.setattr(
        'service.history.worker.atomic_write_status',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError('read only')),
    )
    assert service._publish_status('degraded') is None
    service._executor.shutdown(wait=False)
