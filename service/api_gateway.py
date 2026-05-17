import os
import json
import sys
import time
import threading
import yaml
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, send_file
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS

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

    def run(self):
        print('[TickFetcher] Background thread started')
        while True:
            try:
                if self.mt5_client.ensure_connected():
                    for symbol in self.symbols:
                        tick = self.mt5_client.call(lambda m: m.symbol_info_tick(symbol))
                        if tick:
                            data = {
                                'symbol': symbol,
                                'bid': tick.bid,
                                'ask': tick.ask,
                                'last': tick.last,
                                'volume': tick.volume,
                                'time': datetime.now(timezone.utc).isoformat()
                            }
                            with tick_lock:
                                latest_ticks[symbol] = data
                            socketio.emit('tick', data, room=symbol)
                            print(f'[TickFetcher] {symbol}: Bid={tick.bid}, Ask={tick.ask}')
            except Exception as e:
                print(f'[TickFetcher] Error: {e}')
                self.mt5_client.reset()
            time.sleep(self.interval)


# ─── REST Routes ───

@app.route('/api/v1/ticks/<symbol>')
def get_tick(symbol):
    symbol = symbol.upper()
    with tick_lock:
        tick = latest_ticks.get(symbol)
    if tick:
        return jsonify(tick)
    return jsonify({'error': 'Symbol not found', 'symbol': symbol}), 404


@app.route('/api/v1/rates/<symbol>')
def get_rates(symbol):
    symbol = symbol.upper()
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

    mt5 = mt5_client.mt5
    tf = getattr(mt5, f'TIMEFRAME_{timeframe}', None)
    if tf is None:
        return jsonify({'error': f'Unknown timeframe: {timeframe}'}), 400

    try:
        rates = mt5_client.call(lambda m: m.copy_rates_range(symbol, tf, start_dt, end_dt))
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
    with tick_lock:
        tick_count = len(latest_ticks)
    return jsonify({
        'status': 'ok',
        'tick_service': 'running' if tick_count > 0 else 'no_data',
        'symbols_tracked': list(latest_ticks.keys()),
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


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

        result = []
        for name in cfg_symbols:
            info = account_svc.mt5_client.call(lambda m: m.symbol_info(name))
            if info:
                result.append({
                    'name': info.name,
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
    symbol = data.get('symbol', '').upper()
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
    symbol = data.get('symbol', '').upper()
    if symbol:
        print(f'[WS] Client unsubscribed from {symbol}')


# ─── Main ───

if __name__ == '__main__':
    fetcher = TickFetcher()
    fetcher.start()

    port = int(os.getenv('API_GATEWAY_PORT', 8090))
    host = os.getenv('API_GATEWAY_HOST', '0.0.0.0')

    print(f'[APIGateway] Starting on {host}:{port}')
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
