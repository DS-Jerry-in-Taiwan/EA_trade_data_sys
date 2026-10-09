from types import SimpleNamespace

import pandas as pd
import pytest

from service.history.archive_backfill import backfill


class Client:
    def __init__(self, mode='DEMO', change=False):
        self.calls = 0
        self.change = change
        self.mode = mode
        self.session_guard = SimpleNamespace(status=self.status)
        self.mt5 = SimpleNamespace(TIMEFRAME_M15=15, copy_rates_range=self.rates)

    def status(self):
        return {'ready': True, 'generation': 1,
                'fingerprint': {'id': 'opaque-test-source', 'account_mode': self.mode}}

    def ensure_connected(self):
        return True

    def init_resolver(self, symbols):
        pass

    def resolve(self, symbol):
        return 'TEST_BTC'

    def call(self, callback):
        return callback(self.mt5)

    def rates(self, source, timeframe, start, end):
        self.calls += 1
        times = pd.date_range(start, end, freq='15min')
        return [{'time': int(t.timestamp()), 'open': 100, 'high': 102, 'low': 99,
                 'close': 101 if not self.change or self.calls == 1 else 100,
                 'tick_volume': 10} for t in times]


def test_readonly_backfill_persists_restart_and_repeat(tmp_path):
    from service.history.archive import HistoricalOHLCArchive
    path = tmp_path/'archive.sqlite3'
    client = Client()
    args = (client, HistoricalOHLCArchive(path), 'BTC', 'M15',
            '2026-09-01T00:00:00Z', '2026-09-01T01:00:00Z')
    result = backfill(*args)
    assert result == {'source_bars': 5, 'archive_bars': 5, 'missing': 0, 'extra': 0,
                      'mutation_attempted': False}
    assert backfill(client, HistoricalOHLCArchive(path), *args[2:]) == result
    assert client.calls == 4


@pytest.mark.parametrize('mode,change', [('REAL', False), ('DEMO', True)])
def test_backfill_fail_closed_before_archive_write(mode, change):
    archive = SimpleNamespace(upsert=lambda **kwargs: pytest.fail('unexpected write'))
    with pytest.raises(ValueError):
        backfill(Client(mode, change), archive, 'BTC', 'M15',
                 '2026-09-01T00:00:00Z', '2026-09-01T01:00:00Z')


@pytest.mark.parametrize('start,end', [
    ('2026-09-01', '2026-09-02'),
    ('2026-01-01T00:00:00Z', '2026-03-01T00:00:00Z'),
    ('2026-09-02T00:00:00Z', '2026-09-01T00:00:00Z'),
])
def test_backfill_rejects_unbounded_or_ambiguous_interval(start, end):
    client = Client()
    with pytest.raises(ValueError):
        backfill(client, None, 'BTC', 'M15', start, end)
    assert client.calls == 0
