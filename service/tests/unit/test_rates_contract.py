import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture(scope='module')
def gateway_module():
    """Import the gateway without connecting to MT5 or starting background services."""
    metrics = types.ModuleType('metrics')
    socketio_module = types.ModuleType('flask_socketio')

    class SocketIO:
        def __init__(self, *_args, **_kwargs):
            pass

        def on(self, _event):
            return lambda function: function

        def emit(self, *_args, **_kwargs):
            return None

    socketio_module.SocketIO = SocketIO
    socketio_module.emit = lambda *_args, **_kwargs: None
    socketio_module.join_room = lambda *_args, **_kwargs: None
    socketio_module.leave_room = lambda *_args, **_kwargs: None
    cors_module = types.ModuleType('flask_cors')
    cors_module.CORS = lambda app, *_args, **_kwargs: app

    class Metric:
        def labels(self, **_kwargs):
            return self

        def inc(self):
            return None

        def observe(self, _value):
            return None

        def set(self, _value):
            return None

    for name in (
        'api_requests_total', 'api_request_duration_seconds', 'mt5_tick_bid',
        'mt5_tick_ask', 'mt5_last_tick_timestamp', 'mt5_connected',
        'mt5_account_balance', 'mt5_account_equity', 'service_uptime_seconds',
        'component_state', 'gateway_ready', 'tick_ipc_connected',
        'fresh_tick_symbols', 'component_status_age_seconds',
        'mt5_deal_mapping_errors_total',
    ):
        setattr(metrics, name, Metric())
    metrics.generate_latest = lambda: b''

    mt5_client_module = types.ModuleType('service.infrastructure.mt5.client')

    class MT5Client:
        def __init__(self, *, symbol_aliases=None, settings=None):
            self.symbol_aliases = dict(symbol_aliases or {})
            self.settings = settings

        def shutdown(self):
            return None

    mt5_client_module.MT5Client = MT5Client

    account_module = types.ModuleType('service.trade_query.account_service')

    class AccountService:
        def __init__(self, mt5_client=None, **_kwargs):
            self.mt5_client = mt5_client

    account_module.AccountService = AccountService

    tick_consumer_module = types.ModuleType('service.realtime.consumer')

    class TickConsumer:
        def __init__(self, *_args, **_kwargs):
            self.symbols = []
            self.connected = False

        def stop(self):
            return None

        def get(self, _symbol):
            return None

        def is_fresh(self, *_args):
            return False

    tick_consumer_module.TickConsumer = TickConsumer
    tick_ipc_module = types.ModuleType('service.infrastructure.ipc.tick_protocol')
    tick_ipc_module.DEFAULT_SOCKET_PATH = '/tmp/test-ticks.sock'

    saved_modules = {
        name: sys.modules.get(name)
        for name in (
            'flask_socketio', 'flask_cors', 'service.infrastructure.observability.metrics',
            'service.infrastructure.mt5.client', 'service.trade_query.account_service',
            'service.realtime.consumer', 'service.infrastructure.ipc.tick_protocol',
        )
    }
    sys.modules['flask_socketio'] = socketio_module
    sys.modules['flask_cors'] = cors_module
    sys.modules['service.infrastructure.observability.metrics'] = metrics
    sys.modules['service.infrastructure.mt5.client'] = mt5_client_module
    sys.modules['service.trade_query.account_service'] = account_module
    sys.modules['service.realtime.consumer'] = tick_consumer_module
    sys.modules['service.infrastructure.ipc.tick_protocol'] = tick_ipc_module
    try:
        gateway_path = Path(__file__).parents[2] / 'entrypoints' / 'api_gateway.py'
        spec = importlib.util.spec_from_file_location('rates_contract_gateway', gateway_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, previous in saved_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@pytest.fixture
def client(gateway_module, tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_module.history_query_svc.repository, 'data_path', str(tmp_path))
    from service.history.repository import history_storage_id
    monkeypatch.setattr(gateway_module.history_query_svc.repository, 'publication_status',
                        lambda: {'fresh': True, 'storage_id': history_storage_id(tmp_path)})
    gateway_module.app.config.update(TESTING=True)
    return gateway_module.app.test_client(), tmp_path


def write_rates(directory, rows, symbol='XAUUSDm', timeframe='M5'):
    path = directory / f'{symbol}_{timeframe}.csv'
    frame = pd.DataFrame(rows)
    frame.to_csv(path, index=False)
    sources = set(frame.get('source_symbol', pd.Series(dtype=object)).dropna().astype(str))
    if len(sources) == 1:
        path.with_name(f'{path.name}.ready').write_text(next(iter(sources)), encoding='utf-8')


def valid_row(time, close=100.5):
    return {
        'time': time,
        'open': 100.0,
        'high': 101.0,
        'low': 99.0,
        'close': close,
        'tick_volume': 12,
        'source_symbol': 'XAUUSDm',
    }


def test_gateway_passes_broker_aliases_without_renaming_public_symbols(gateway_module, tmp_path):
    config = tmp_path / 'settings.yaml'
    config.write_text(
        'symbol_aliases: {XAUUSDm: XAU_USD, BTC: BTCUSD.sim}\n'
        'tick_service:\n  symbols: [XAUUSDm, BTC]\n'
        'history_service:\n  symbols:\n'
        '    - {name: XAUUSDm, timeframes: [M5]}\n'
        '    - {name: BTC, timeframes: [M5]}\n',
        encoding='utf-8',
    )

    gateway, mt5_client = gateway_module.build_gateway(config)

    assert mt5_client.symbol_aliases == {'XAUUSDm': 'XAU_USD', 'BTC': 'BTCUSD.sim'}
    assert gateway.context.tick_symbols == ['XAUUSDm', 'BTC']
    assert gateway.context.symbol_names == ['XAUUSDm', 'BTC']
    assert gateway.context.account_service.mt5_client is mt5_client


def test_rejects_unsorted_and_duplicate_cache(client):
    http, data_dir = client
    rows = [
        valid_row('2026-09-08T10:10:00Z', 100.2),
        valid_row('2026-09-08T10:00:00+00:00', 100.0),
        valid_row('2026-09-08T10:05:00Z', 100.1),
        valid_row('2026-09-08T10:05:00Z', 100.15),
    ]
    write_rates(data_dir, rows)

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=m5&limit=2')

    assert response.status_code == 422
    assert response.get_json()['detail'] == 'CSV timestamps must be ordered and unique'


def test_returns_latest_limit_from_ordered_unique_cache(client):
    http, data_dir = client
    rows = [
        valid_row('2026-09-08T10:00:00Z', 100.0),
        valid_row('2026-09-08T10:05:00Z', 100.1),
        valid_row('2026-09-08T10:10:00Z', 100.2),
    ]
    write_rates(data_dir, rows)

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=m5&limit=2')

    assert response.status_code == 200
    assert [row['time'] for row in response.get_json()] == [
        '2026-09-08T10:05:00.000Z',
        '2026-09-08T10:10:00.000Z',
    ]


def test_rejects_cache_without_source_identity(client):
    http, data_dir = client
    row = valid_row('2026-09-08T10:00:00Z')
    del row['source_symbol']
    write_rates(data_dir, [row])
    (data_dir / 'XAUUSDm_M5.csv.ready').write_text('XAUUSDm', encoding='utf-8')

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 422


def test_rejects_cache_without_ready_marker(client):
    http, data_dir = client
    path = data_dir / 'XAUUSDm_M5.csv'
    pd.DataFrame([valid_row('2026-09-08T10:00:00Z')]).to_csv(path, index=False)

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 503


def test_rejects_ready_marker_for_different_source(client):
    http, data_dir = client
    write_rates(data_dir, [valid_row('2026-09-08T10:00:00Z')])
    (data_dir / 'XAUUSDm_M5.csv.ready').write_text('OTHER', encoding='utf-8')

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 422


def test_rejects_mixed_source_identity(client):
    http, data_dir = client
    first = valid_row('2026-09-08T10:00:00Z')
    second = valid_row('2026-09-08T10:05:00Z')
    second['source_symbol'] = 'OTHER'
    write_rates(data_dir, [first, second])
    (data_dir / 'XAUUSDm_M5.csv.ready').write_text('XAUUSDm', encoding='utf-8')

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 422


@pytest.mark.parametrize('timeframe', ['M1', 'H4', '', 'unknown'])
def test_rejects_unsupported_timeframe(client, timeframe):
    http, _ = client
    response = http.get('/api/v1/rates/XAUUSDm', query_string={
        'timeframe': timeframe,
        'limit': '10',
    })
    assert response.status_code == 400


@pytest.mark.parametrize('limit', ['', '0', '-1', '1.5', 'abc', '5001'])
def test_rejects_invalid_limit(client, limit):
    http, _ = client
    query = {'timeframe': 'M5'}
    if limit is not None:
        query['limit'] = limit
    response = http.get('/api/v1/rates/XAUUSDm', query_string=query)
    assert response.status_code == 400
    assert 'limit' in response.get_json()['error']


def test_omitted_limit_remains_backward_compatible(client):
    http, data_dir = client
    write_rates(data_dir, [valid_row('2026-09-08T10:00:00Z')])

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 200
    assert len(response.get_json()) == 1


def test_omitted_limit_returns_all_rows(client):
    http, data_dir = client
    start = pd.Timestamp('2026-08-01T00:00:00Z')
    rows = [valid_row(start + pd.Timedelta(minutes=5 * index)) for index in range(5001)]
    write_rates(data_dir, rows)

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 200
    assert len(response.get_json()) == 5001


@pytest.mark.parametrize('days', ['', '-1', 'abc', '36501'])
def test_rejects_invalid_days(client, days):
    http, _ = client
    response = http.get(
        '/api/v1/rates/XAUUSDm',
        query_string={'timeframe': 'M5', 'limit': '10', 'days': days},
    )
    assert response.status_code == 400
    assert 'days' in response.get_json()['error']


def test_days_filters_before_optional_limit(client, gateway_module, monkeypatch):
    http, data_dir = client
    monkeypatch.setattr(
        gateway_module.history_query_svc, '_now', lambda: pd.Timestamp('2026-09-08T12:00:00Z')
    )
    write_rates(data_dir, [
        valid_row('2026-09-05T12:00:00Z'),
        valid_row('2026-09-07T12:00:00Z'),
        valid_row('2026-09-08T10:00:00Z'),
    ], timeframe='H1')

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=H1&days=2&limit=1')

    assert response.status_code == 200
    assert [row['time'] for row in response.get_json()] == ['2026-09-08T10:00:00.000Z']


def test_numeric_epoch_seconds_are_parsed_as_seconds(client, gateway_module, monkeypatch):
    http, data_dir = client
    monkeypatch.setattr(
        gateway_module.history_query_svc, '_now', lambda: pd.Timestamp('2026-09-08T13:00:00Z')
    )
    epoch_seconds = int(pd.Timestamp('2026-09-08T12:00:00Z').timestamp())
    write_rates(data_dir, [valid_row(epoch_seconds)], timeframe='H1')

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=H1')

    assert response.status_code == 200
    assert response.get_json()[0]['time'] == '2026-09-08T12:00:00.000Z'


def test_mixed_iso_and_epoch_seconds_are_both_preserved(client, gateway_module, monkeypatch):
    http, data_dir = client
    monkeypatch.setattr(
        gateway_module.history_query_svc, '_now', lambda: pd.Timestamp('2026-09-08T13:00:00Z')
    )
    epoch_seconds = int(pd.Timestamp('2026-09-08T12:05:00Z').timestamp())
    write_rates(
        data_dir,
        [valid_row('2026-09-08T12:00:00Z'), valid_row(epoch_seconds)],
    )

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 200
    assert [row['time'] for row in response.get_json()] == [
        '2026-09-08T12:00:00.000Z',
        '2026-09-08T12:05:00.000Z',
    ]


@pytest.mark.parametrize('timestamp', [
    '2026-09-08T12:00:00Z',
    '2026-09-08T13:00:00Z',
])
def test_rejects_forming_and_future_bars(client, gateway_module, monkeypatch, timestamp):
    http, data_dir = client
    monkeypatch.setattr(
        gateway_module.history_query_svc, '_now', lambda: pd.Timestamp('2026-09-08T12:30:00Z')
    )
    write_rates(data_dir, [valid_row(timestamp)], timeframe='H1')

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=H1')

    assert response.status_code == 422


def test_rejects_misaligned_bar(client, gateway_module, monkeypatch):
    http, data_dir = client
    monkeypatch.setattr(
        gateway_module.history_query_svc, '_now', lambda: pd.Timestamp('2026-09-08T13:00:00Z')
    )
    write_rates(data_dir, [valid_row('2026-09-08T11:03:00Z')])

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 422


def test_rejects_non_integer_cadence(client, gateway_module, monkeypatch):
    http, data_dir = client
    monkeypatch.setattr(
        gateway_module.history_query_svc, '_now', lambda: pd.Timestamp('2026-09-08T13:00:00Z')
    )
    write_rates(data_dir, [
        valid_row('2026-09-08T11:00:00Z'),
        valid_row('2026-09-08T11:07:00Z'),
    ])

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5')

    assert response.status_code == 422


def test_returns_404_for_missing_cache_without_exposing_path(client):
    http, data_dir = client
    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5&limit=10')
    assert response.status_code == 404
    assert str(data_dir) not in response.get_data(as_text=True)


def test_rejects_missing_required_column(client):
    http, data_dir = client
    row = valid_row('2026-09-08T10:00:00Z')
    del row['tick_volume']
    write_rates(data_dir, [row])

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5&limit=1')

    assert response.status_code == 422
    assert response.get_json()['error'] == 'Invalid market data'
    assert str(data_dir) not in response.get_data(as_text=True)


@pytest.mark.parametrize('field,value', [
    ('time', 'not-a-time'),
    ('open', 'not-a-number'),
    ('high', 98.0),
    ('low', 102.0),
    ('tick_volume', -1),
])
def test_rejects_invalid_market_data(client, field, value):
    http, data_dir = client
    row = valid_row('2026-09-08T10:00:00Z')
    row[field] = value
    write_rates(data_dir, [row])

    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5&limit=1')

    assert response.status_code == 422
    assert response.get_json()['error'] == 'Invalid market data'
    assert str(data_dir) not in response.get_data(as_text=True)


def test_csv_read_error_is_generic_500(client, monkeypatch):
    http, data_dir = client
    (data_dir / 'XAUUSDm_M5.csv').touch()
    (data_dir / 'XAUUSDm_M5.csv.ready').write_text('XAUUSDm', encoding='utf-8')

    def fail_read(_path):
        raise OSError(f'cannot read {data_dir}')

    monkeypatch.setattr(pd, 'read_csv', fail_read)
    response = http.get('/api/v1/rates/XAUUSDm?timeframe=M5&limit=1')

    assert response.status_code == 500
    assert response.get_json() == {'error': 'Unable to read market data'}
    assert str(data_dir) not in response.get_data(as_text=True)


def test_range_query_reads_persisted_history(client, gateway_module, monkeypatch):
    http, data_dir = client
    monkeypatch.setattr(
        gateway_module.history_query_svc, '_now',
        lambda: pd.Timestamp('2026-09-08T13:00:00Z'),
    )
    write_rates(data_dir, [
        valid_row('2026-09-08T10:00:00Z'),
        valid_row('2026-09-08T10:05:00Z'),
        valid_row('2026-09-08T10:10:00Z'),
    ])

    response = http.get(
        '/api/v1/rates/XAUUSDm/query',
        query_string={
            'timeframe': 'M5',
            'start_time': '2026-09-08T10:05:00Z',
            'end_time': '2026-09-08T10:10:00Z',
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['source'] == 'history_storage'
    assert payload['count'] == 2


def test_range_query_does_not_backfill_when_range_is_unavailable(client):
    http, data_dir = client
    write_rates(data_dir, [valid_row('2026-09-08T10:00:00Z')])

    response = http.get(
        '/api/v1/rates/XAUUSDm/query',
        query_string={
            'timeframe': 'M5',
            'start_time': '2026-09-09T10:00:00Z',
            'end_time': '2026-09-09T11:00:00Z',
        },
    )

    assert response.status_code == 404
    payload = response.get_json()
    assert payload['error'] == 'No data found'
    assert payload['available_range'] == {
        'start_time': '2026-09-08T10:00:00Z',
        'end_time': '2026-09-08T10:00:00Z',
        'count': 1,
    }
