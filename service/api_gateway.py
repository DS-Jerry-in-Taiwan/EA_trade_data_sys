import os
import json
import sys
import time
import threading
import yaml
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS

sys.path.insert(0, '/app')
from core.connection_manager import MT5Connector

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
        self.symbols = cfg.get('symbols', ['XAUUSD'])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.connector = None
        self.mt5 = None

    def _ensure_connected(self):
        if self.mt5 is None:
            self.connector = MT5Connector()
            self.mt5 = self.connector.connect()
        return self.mt5 is not None

    def run(self):
        print('[TickFetcher] Background thread started')
        while True:
            try:
                if self._ensure_connected():
                    for symbol in self.symbols:
                        tick = self.mt5.symbol_info_tick(symbol)
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
                self.mt5 = None
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
