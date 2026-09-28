import os
import sys
import yaml
import hmac
import atexit
import signal
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, send_file
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import time as time_module
import metrics

sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client
from service.account_service import AccountService
from service.history_repository import HistoryRepository
from service.history_query_service import HistoryQueryService
from service.core.tick_consumer import TickConsumer
from service.core.tick_ipc import DEFAULT_SOCKET_PATH
from service.core.component_status import DEFAULT_STATUS_DIR, read_status

# The gateway process owns exactly one MT5 client. All gateway components and
# request handlers share it instead of opening independent RPyC sessions.
mt5_client = MT5Client()
atexit.register(mt5_client.shutdown)
account_svc = AccountService(mt5_client=mt5_client)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

cfg_path = '/app/service/config/settings.yaml'
history_query_svc = HistoryQueryService(HistoryRepository())


def _public_tick(event):
    """Keep the existing API `time` field while exposing the IPC timestamps."""
    result = dict(event)
    result['time'] = event.get('source_time') or event.get('received_at')
    return result


def _on_tick(event):
    metrics.mt5_tick_bid.labels(symbol=event['symbol']).set(event['bid'])
    metrics.mt5_tick_ask.labels(symbol=event['symbol']).set(event['ask'])
    try:
        source_time = datetime.fromisoformat(
            (event.get('source_time') or event['received_at']).replace('Z', '+00:00')
        )
        metrics.mt5_last_tick_timestamp.labels(symbol=event['symbol']).set(source_time.timestamp())
    except (KeyError, TypeError, ValueError):
        pass
    metrics.mt5_connected.set(1)
    socketio.emit('tick', _public_tick(event), room=event['symbol'])


def _tick_settings():
    try:
        with open(cfg_path) as f:
            cfg = (yaml.safe_load(f) or {}).get('tick_service', {})
    except (OSError, yaml.YAMLError):
        cfg = {}
    interval = float(cfg.get('update_interval_seconds', 60))
    symbols = cfg.get('symbols', [])
    return {
        'socket_path': os.getenv('TICK_SOCKET_PATH', cfg.get('socket_path', DEFAULT_SOCKET_PATH)),
        'max_age_seconds': float(cfg.get('max_age_seconds', max(interval * 2, 5))),
        'status_path': os.getenv('TICK_STATUS_PATH', cfg.get(
            'status_path', os.path.join(DEFAULT_STATUS_DIR, 'tick-status.json'))),
        'status_max_age_seconds': float(cfg.get('status_max_age_seconds', max(interval * 2, 5))),
        'symbols': list(symbols),
    }


def _history_health_settings():
    try:
        with open(cfg_path) as f:
            cfg = (yaml.safe_load(f) or {}).get('history_service', {})
    except (OSError, yaml.YAMLError):
        cfg = {}
    interval = float(cfg.get('update_interval_seconds', 60))
    return {
        'status_path': os.getenv('HISTORY_STATUS_PATH', cfg.get(
            'status_path', os.path.join(DEFAULT_STATUS_DIR, 'history-status.json'))),
        'status_max_age_seconds': float(cfg.get('status_max_age_seconds', max(interval * 3, 30))),
    }


_tick_config = _tick_settings()
_history_health_config = _history_health_settings()
tick_consumer = TickConsumer(_tick_config['socket_path'], on_tick=_on_tick)
atexit.register(tick_consumer.stop)


def _fresh_tick(symbol):
    tick = tick_consumer.get(symbol)
    return tick if tick_consumer.is_fresh(tick, _tick_config['max_age_seconds']) else None


# ─── Request Hooks for Metrics ───

@app.before_request
def before_request():
    request._start_time = time_module.time()


@app.after_request
def after_request(response):
    dt = time_module.time() - request._start_time
    endpoint = request.path or 'unknown'
    method = request.method
    status = response.status_code
    try:
        metrics.api_requests_total.labels(method=method, endpoint=endpoint, status=status).inc()
        metrics.api_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(dt)
    except Exception:
        pass
    return response


# ─── REST Routes ───

def load_gateway_config():
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f) or {}
    return {
        'api_gateway': cfg.get('api_gateway', {}),
        'trade_query': cfg.get('trade_query', {}),
    }


def require_readonly_api_key():
    cfg = load_gateway_config()
    env_name = cfg.get('api_gateway', {}).get('readonly_api_key_env', 'READONLY_API_KEY')
    expected_key = os.getenv(env_name)
    if not expected_key:
        return jsonify({'error': 'readonly api key not configured'}), 503
    provided_key = request.headers.get('X-API-Key')
    if not provided_key or not hmac.compare_digest(provided_key, expected_key):
        return jsonify({'error': 'unauthorized'}), 401
    return None


def _parse_yyyy_mm_dd(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d')
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc)


def parse_trade_query_range():
    cfg = load_gateway_config().get('trade_query', {})
    default_days = int(cfg.get('default_days', 7))
    max_days = int(cfg.get('max_days', 90))

    from_arg = request.args.get('from')
    to_arg = request.args.get('to')
    if from_arg or to_arg:
        if not from_arg or not to_arg:
            return None, None, {'error': 'invalid date format'}
        from_dt = _parse_yyyy_mm_dd(from_arg)
        to_dt = _parse_yyyy_mm_dd(to_arg)
        if from_dt is None or to_dt is None:
            return None, None, {'error': 'invalid date format'}
        if from_dt > to_dt:
            return None, None, {'error': 'from must be before to'}
        if (to_dt - from_dt).days > max_days:
            return None, None, {'error': f'date range too large, max {max_days} days'}
        return from_dt, to_dt, None

    days_arg = request.args.get('days', default_days)
    try:
        days = int(days_arg)
    except (TypeError, ValueError):
        return None, None, {'error': 'invalid date format'}
    if days <= 0:
        return None, None, {'error': 'invalid date format'}
    if days > max_days:
        return None, None, {'error': f'date range too large, max {max_days} days'}
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)
    return from_dt, to_dt, None


def service_result_to_response(result):
    if isinstance(result, dict) and result.get('error') == 'MT5 not connected':
        return jsonify(result), 503
    if isinstance(result, dict) and result.get('error'):
        return jsonify(result), 500
    return jsonify(result)


@app.route('/api/v1/account')
def get_account():
    auth_error = require_readonly_api_key()
    if auth_error:
        return auth_error
    return service_result_to_response(account_svc.get_account())


@app.route('/api/v1/positions')
def get_positions():
    auth_error = require_readonly_api_key()
    if auth_error:
        return auth_error
    symbol = request.args.get('symbol')
    return service_result_to_response(account_svc.get_positions(symbol=symbol))


@app.route('/api/v1/history/deals')
def get_history_deals():
    auth_error = require_readonly_api_key()
    if auth_error:
        return auth_error
    from_dt, to_dt, parse_error = parse_trade_query_range()
    if parse_error:
        return jsonify(parse_error), 400
    summary = request.args.get('summary', '').lower() == 'true'
    result = account_svc.get_deals(from_dt=from_dt, to_dt=to_dt, limit=None, include_summary=summary)
    return service_result_to_response(result)


@app.route('/api/v1/history/orders')
def get_history_orders():
    auth_error = require_readonly_api_key()
    if auth_error:
        return auth_error
    from_dt, to_dt, parse_error = parse_trade_query_range()
    if parse_error:
        return jsonify(parse_error), 400
    return service_result_to_response(account_svc.get_history_orders(from_dt, to_dt))

@app.route('/api/v1/ticks/<symbol>')
def get_tick(symbol):
    tick = tick_consumer.get(symbol)
    if tick and tick_consumer.is_fresh(tick, _tick_config['max_age_seconds']):
        return jsonify(_public_tick(tick))
    if tick:
        return jsonify({
            'error': 'Tick data is stale',
            'symbol': symbol,
            'last_received_at': tick.get('received_at'),
        }), 503
    return jsonify({'error': 'Symbol not found', 'symbol': symbol}), 404


@app.route('/api/v1/rates/<symbol>')
def get_rates(symbol):
    result = history_query_svc.get_rates(
        symbol,
        timeframe=request.args.get('timeframe', 'M5'),
        limit=request.args.get('limit'),
        days=request.args.get('days', '0'),
    )
    if 'error_response' in result:
        return jsonify(result['error_response']), result['status']
    return jsonify(result['data'])


@app.route('/api/v1/rates/<symbol>/query')
def query_rates_by_range(symbol):
    """Query the completed history cache without synchronous MT5 access."""
    start_time_str = request.args.get('start_time')
    end_time_str = request.args.get('end_time')
    if not start_time_str or not end_time_str:
        return jsonify({'error': 'start_time and end_time are required'}), 400
    result = history_query_svc.get_rates(
        symbol,
        timeframe=request.args.get('timeframe', 'M5'),
        days='0',
        start_time=start_time_str,
        end_time=end_time_str,
    )
    if 'error_response' in result:
        return jsonify(result['error_response']), result['status']
    if not result['data']:
        return jsonify({
            'error': 'No data found', 'symbol': symbol,
            'timeframe': result['timeframe'],
            'available_range': result['available_range'],
        }), 404
    return jsonify({
        'symbol': symbol,
        'timeframe': result['timeframe'],
        'start_time': start_time_str,
        'end_time': end_time_str,
        'data': result['data'],
        'count': len(result['data']),
        'source': 'history_storage',
    })


@app.route('/api/v1/health')
def health():
    """Report process state without making any MT5/RPyC calls."""
    expected_symbols = _tick_config['symbols']
    fresh_symbols = [symbol for symbol in expected_symbols if _fresh_tick(symbol)]
    tick_status = read_status(
        _tick_config['status_path'], _tick_config['status_max_age_seconds']
    )
    history_status = read_status(
        _history_health_config['status_path'],
        _history_health_config['status_max_age_seconds'],
    )
    tick_ready = bool(
        tick_consumer.connected
        and tick_status.get('fresh')
        and tick_status.get('state') == 'healthy'
        and expected_symbols
        and set(fresh_symbols) == set(expected_symbols)
    )
    history_ok = bool(
        history_status.get('fresh')
        and history_status.get('state') in ('healthy', 'syncing')
    )
    if tick_ready:
        overall = 'healthy' if history_ok else 'degraded'
    elif tick_status.get('fresh') and tick_status.get('state') == 'unhealthy':
        overall = 'unhealthy'
    else:
        overall = 'not-ready'
    payload = {
        'status': overall,
        'ready': tick_ready,
        'gateway': {'state': 'healthy', 'ready': tick_ready},
        'tick_service': tick_status,
        'history_service': history_status,
        'tick_ipc_connected': tick_consumer.connected,
        'symbols_tracked': expected_symbols,
        'fresh_symbols': fresh_symbols,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    _update_component_metrics(payload)
    return jsonify(payload), (200 if tick_ready else 503)


def _update_component_metrics(health_payload=None):
    if health_payload is None:
        expected = _tick_config['symbols']
        fresh = [symbol for symbol in expected if _fresh_tick(symbol)]
        tick = read_status(_tick_config['status_path'], _tick_config['status_max_age_seconds'])
        history = read_status(
            _history_health_config['status_path'],
            _history_health_config['status_max_age_seconds'],
        )
        ready = bool(tick_consumer.connected and tick.get('fresh')
                     and tick.get('state') == 'healthy' and expected
                     and set(fresh) == set(expected))
    else:
        tick = health_payload['tick_service']
        history = health_payload['history_service']
        fresh = health_payload['fresh_symbols']
        ready = health_payload['ready']
    for component, value in (('tick_service', tick), ('history_service', history)):
        current = value.get('state', 'unknown')
        for state in ('healthy', 'syncing', 'degraded', 'unhealthy', 'starting', 'stopped', 'unknown'):
            metrics.component_state.labels(component=component, state=state).set(
                1 if state == current else 0
            )
        if 'age_seconds' in value:
            metrics.component_status_age_seconds.labels(component=component).set(
                value['age_seconds']
            )
    metrics.gateway_ready.set(1 if ready else 0)
    metrics.tick_ipc_connected.set(1 if tick_consumer.connected else 0)
    metrics.fresh_tick_symbols.set(len(fresh))


@app.route('/metrics')
@app.route('/api/v1/metrics')
def prometheus_metrics():
    """Prometheus metrics export endpoint."""
    # 更新 uptime metric
    metrics.service_uptime_seconds.set(time_module.time() - _start_time)
    _update_component_metrics()
    metrics.mt5_connected.set(1 if tick_consumer.connected else 0)
    return metrics.generate_latest(), 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/api/v1/symbols')
def list_symbols():
    """列出所有追蹤商品，含 MT5 即時資訊（描述、位數、目前報價）。"""
    try:
        cfg_symbols = []
        with open(cfg_path) as f:
            hist_cfg = yaml.safe_load(f).get('history_service', {})
            cfg_symbols = [s['name'] for s in hist_cfg.get('symbols', [])]

        if not account_svc.mt5_client.ensure_connected():
            return jsonify({'symbols': cfg_symbols, 'source': 'config'})

        # Lazy init resolver on account_svc's mt5_client
        if not hasattr(account_svc.mt5_client, '_resolver') or account_svc.mt5_client._resolver is None:
            account_svc.mt5_client.init_resolver(cfg_symbols)

        result = []
        for name in cfg_symbols:
            broker_name = account_svc.mt5_client.resolve(name)
            info = account_svc.mt5_client.call(lambda m: m.symbol_info(broker_name))
            if info:
                result.append({
                    'name': name,  # 保持 logical name
                    'digits': info.digits,
                    'spread': info.spread,
                    'description': info.description if hasattr(info, 'description') else '',
                    'trade_mode': info.trade_mode if hasattr(info, 'trade_mode') else None,
                })
            else:
                result.append({'name': name, 'digits': None, 'spread': None})

        return jsonify({'symbols': result, 'count': len(result), 'source': 'mt5'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/openapi.yaml')
def openapi_spec():
    """Serve OpenAPI v3 specification file for API discovery."""
    # Runtime assets must live under the tracked /app/service tree.  The docs/
    # directory is intentionally gitignored and is absent from clean deploys.
    spec_path = '/app/service/openapi.yaml'
    if not os.path.exists(spec_path):
        return jsonify({'error': 'Specification file not found'}), 404
    return send_file(spec_path, mimetype='text/yaml')


# ─── WebSocket Events ───

@socketio.on('connect')
def handle_connect():
    print(f'[WS] Client connected')
    emit('connected', {'message': 'Connected to MT5 API Gateway'})


@socketio.on('subscribe')
def handle_subscribe(data):
    symbol = data.get('symbol', '')  # 移除 .upper()
    if symbol:
        join_room(symbol)
        emit('subscribed', {'symbol': symbol})
        tick = _fresh_tick(symbol)
        if tick:
            emit('tick', _public_tick(tick))
        print(f'[WS] Client subscribed to {symbol}')


@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    symbol = data.get('symbol', '')  # 移除 .upper()
    if symbol:
        leave_room(symbol)
        print(f'[WS] Client unsubscribed from {symbol}')


_start_time = time_module.time()

# ─── Main ───

def _handle_shutdown_signal(signum, _frame):
    """Unwind the server so process-owned resources reach ``finally``."""
    raise SystemExit(128 + signum)


def run_gateway():
    tick_consumer.start()
    try:
        port = int(os.getenv('API_GATEWAY_PORT', 8090))
        host = os.getenv('API_GATEWAY_HOST', '0.0.0.0')

        print(f'[APIGateway] Starting on {host}:{port}')
        socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
    finally:
        # Stop IPC first so no callbacks can race with MT5 teardown.
        try:
            tick_consumer.stop()
        finally:
            mt5_client.shutdown()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    run_gateway()
