import os
import json
import sys
import time
import threading
import yaml
import hmac
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, send_file
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import time as time_module
import metrics

sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client
from service.account_service import AccountService

account_svc = AccountService()

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

latest_ticks = {}
tick_lock = threading.Lock()
history_path = '/app/service/data/history'
cfg_path = '/app/service/config/settings.yaml'


class TickFetcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f).get('tick_service', {})
        self.symbols = cfg.get('symbols', ['XAUUSDm'])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.mt5_client = MT5Client()
        self._resolver_initialized = False

    def run(self):
        print('[TickFetcher] Background thread started')
        while True:
            try:
                if self.mt5_client.ensure_connected():
                    # Lazy init resolver
                    if not self._resolver_initialized:
                        self.mt5_client.init_resolver(self.symbols)
                        self._resolver_initialized = True

                    for symbol in self.symbols:
                        broker_symbol = self.mt5_client.resolve(symbol)
                        tick = self.mt5_client.call(lambda m: m.symbol_info_tick(broker_symbol))
                        if tick:
                            data = {
                                'symbol': symbol,  # 保持 logical name
                                'bid': tick.bid,
                                'ask': tick.ask,
                                'last': tick.last,
                                'volume': tick.volume,
                                'time': datetime.now(timezone.utc).isoformat()
                            }
                            with tick_lock:
                                latest_ticks[symbol] = data
                            # 更新 Prometheus metrics（使用 broker 實際名稱）
                            metrics.mt5_tick_bid.labels(symbol=broker_symbol).set(tick.bid)
                            metrics.mt5_tick_ask.labels(symbol=broker_symbol).set(tick.ask)
                            if hasattr(tick, 'time') and tick.time:
                                metrics.mt5_last_tick_timestamp.labels(symbol=broker_symbol).set(tick.time)
                            else:
                                metrics.mt5_last_tick_timestamp.labels(symbol=broker_symbol).set(time_module.time())
                            metrics.mt5_connected.set(1)
                            socketio.emit('tick', data, room=symbol)
                            print(f'[TickFetcher] {symbol}: Bid={tick.bid}, Ask={tick.ask}')
            except Exception as e:
                print(f'[TickFetcher] Error: {e}')
                metrics.mt5_connected.set(0)
                self.mt5_client.reset()
            time.sleep(self.interval)


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
    # 移除 .upper() - services 存的是 logical name（原樣從 settings.yaml）
    with tick_lock:
        tick = latest_ticks.get(symbol)
    if tick:
        return jsonify(tick)
    return jsonify({'error': 'Symbol not found', 'symbol': symbol}), 404


@app.route('/api/v1/rates/<symbol>')
def get_rates(symbol):
    # 移除 .upper() - CSV 檔名用的是 logical name
    timeframe = request.args.get('timeframe', 'M5')
    days = request.args.get('days', 0, type=int)
    limit = request.args.get('limit', 0, type=int)

    filepath = os.path.join(history_path, f'{symbol}_{timeframe}.csv')
    if not os.path.exists(filepath):
        return jsonify({'error': 'Data not found', 'symbol': symbol, 'timeframe': timeframe}), 404

    import pandas as pd
    df = pd.read_csv(filepath)

    if days > 0:
        cutoff = datetime.now() - timedelta(days=days)
        df = df[pd.to_datetime(df['time']) >= cutoff]

    if limit > 0:
        df = df.tail(limit)

    return jsonify(json.loads(df.to_json(orient='records')))


@app.route('/api/v1/rates/<symbol>/query')
def query_rates_by_range(symbol):
    """直接查詢 MT5 server 指定時間範圍的歷史 K 線資料。"""
    timeframe = request.args.get('timeframe', 'M5')
    start_time_str = request.args.get('start_time')
    end_time_str = request.args.get('end_time')

    if not start_time_str or not end_time_str:
        return jsonify({'error': 'start_time and end_time are required'}), 400

    import pandas as pd
    try:
        start_dt = pd.to_datetime(start_time_str).to_pydatetime()
        end_dt = pd.to_datetime(end_time_str).to_pydatetime()
    except Exception:
        return jsonify({'error': 'Invalid date format. Use ISO 8601 (e.g., 2023-01-01 or 2023-01-01T00:00:00Z)'}), 400

    mt5_client = MT5Client()
    if not mt5_client.ensure_connected():
        return jsonify({'error': 'MT5 not connected'}), 503

    # Resolve symbol
    mt5_client.init_resolver([symbol])
    broker_symbol = mt5_client.resolve(symbol)

    mt5 = mt5_client.mt5
    tf = getattr(mt5, f'TIMEFRAME_{timeframe}', None)
    if tf is None:
        return jsonify({'error': f'Unknown timeframe: {timeframe}'}), 400

    try:
        rates = mt5_client.call(lambda m: m.copy_rates_range(broker_symbol, tf, start_dt, end_dt))
    except Exception as e:
        return jsonify({'error': f'MT5 query failed: {str(e)}'}), 500

    if rates is None or len(rates) == 0:
        return jsonify({'error': 'No data found', 'symbol': symbol, 'timeframe': timeframe}), 404

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')

    return jsonify({
        'symbol': symbol,
        'timeframe': timeframe,
        'start_time': start_time_str,
        'end_time': end_time_str,
        'data': json.loads(df.to_json(orient='records')),
        'count': len(df),
        'source': 'mt5'
    })


@app.route('/api/v1/health')
def health():
    # 更新帳戶 metrics
    try:
        balance_info = account_svc.get_balance()
        if balance_info and 'balance' in balance_info:
            metrics.mt5_account_balance.set(balance_info['balance'])
            metrics.mt5_account_equity.set(balance_info['equity'])
    except Exception:
        pass

    with tick_lock:
        tick_count = len(latest_ticks)
    return jsonify({
        'status': 'ok',
        'tick_service': 'running' if tick_count > 0 else 'no_data',
        'symbols_tracked': list(latest_ticks.keys()),
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


@app.route('/metrics')
@app.route('/api/v1/metrics')
def prometheus_metrics():
    """Prometheus metrics export endpoint."""
    # 更新 uptime metric
    metrics.service_uptime_seconds.set(time_module.time() - _start_time)
    # 更新連接狀態
    try:
        with tick_lock:
            has_ticks = len(latest_ticks) > 0
        metrics.mt5_connected.set(1 if has_ticks else 0)
    except Exception:
        metrics.mt5_connected.set(0)
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
    spec_path = '/app/docs/api/v3_data_api.yaml'
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
        with tick_lock:
            tick = latest_ticks.get(symbol)
        if tick:
            emit('tick', tick)
        print(f'[WS] Client subscribed to {symbol}')


@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    symbol = data.get('symbol', '')  # 移除 .upper()
    if symbol:
        print(f'[WS] Client unsubscribed from {symbol}')


_start_time = time_module.time()

# ─── Main ───

if __name__ == '__main__':
    fetcher = TickFetcher()
    fetcher.start()

    port = int(os.getenv('API_GATEWAY_PORT', 8090))
    host = os.getenv('API_GATEWAY_HOST', '0.0.0.0')

    print(f'[APIGateway] Starting on {host}:{port}')
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
