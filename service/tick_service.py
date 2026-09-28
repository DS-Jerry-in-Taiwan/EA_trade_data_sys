import os
import json
import sys
import yaml
import threading
import signal
from datetime import datetime, timezone

sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client
from service.core.tick_ipc import DEFAULT_SOCKET_PATH, PROTOCOL_VERSION, TickPublisher
from service.core.component_status import DEFAULT_STATUS_DIR, atomic_write_status


class TickService:
    def __init__(self, config_path='/app/service/config/settings.yaml', mt5_client=None,
                 publisher=None, stop_event=None):
        with open(config_path) as f:
            cfg = yaml.safe_load(f).get('tick_service', {})
        self.symbols = cfg.get('symbols', ['XAUUSDm'])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.output_dir = cfg.get('output_dir', '/app/service/data/ticks')
        self.socket_path = os.environ.get(
            'TICK_SOCKET_PATH', cfg.get('socket_path', DEFAULT_SOCKET_PATH)
        )
        self.max_retry_seconds = cfg.get('max_retry_seconds', 30)
        self.status_path = os.environ.get(
            'TICK_STATUS_PATH', cfg.get(
                'status_path', os.path.join(DEFAULT_STATUS_DIR, 'tick-status.json')
            )
        )
        self.mt5_client = mt5_client if mt5_client is not None else MT5Client()
        self.publisher = publisher if publisher is not None else TickPublisher(self.socket_path)
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._resolver_initialized = False
        os.makedirs(self.output_dir, exist_ok=True)

    def _publish_status(self, state, **details):
        try:
            return atomic_write_status(
                self.status_path, 'tick_service', state,
                symbols=self.symbols, **details,
            )
        except OSError as exc:
            # Observability must never become the reason the data process dies.
            print(f'[TickService] Status publication failed: {exc}')
            return None

    def fetch_ticks(self):
        if not self.mt5_client.ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed, retrying...')
            self._resolver_initialized = False
            return {}, False

        # Lazy init resolver (第一次成功連線後執行一次)
        if not self._resolver_initialized:
            self.mt5_client.init_resolver(self.symbols)
            self._resolver_initialized = True
            resolver = getattr(self.mt5_client, '_resolver', None)
            if resolver and resolver.unresolved:
                print(f'[TickService] WARNING: Unresolved symbols: '
                      f'{resolver.unresolved}')

        result = {}
        for symbol in self.symbols:
            try:
                broker_symbol = self.mt5_client.resolve(symbol)
                tick = self.mt5_client.call(lambda m: m.symbol_info_tick(broker_symbol))
                if tick:
                    source_timestamp = getattr(tick, 'time_msc', None)
                    if source_timestamp:
                        source_timestamp = source_timestamp / 1000
                    else:
                        source_timestamp = getattr(tick, 'time', None)
                    received_at = datetime.now(timezone.utc).isoformat()
                    source_time = (
                        datetime.fromtimestamp(source_timestamp, timezone.utc).isoformat()
                        if source_timestamp else received_at
                    )
                    data = {
                        'version': PROTOCOL_VERSION,
                        'type': 'tick',
                        'symbol': symbol,  # 保持 logical name
                        'bid': tick.bid,
                        'ask': tick.ask,
                        'last': tick.last,
                        'volume': tick.volume,
                        'source_time': source_time,
                        'received_at': received_at,
                    }
                    result[symbol] = data
                    print(f'[{datetime.now()}] {symbol}: Bid={tick.bid}, Ask={tick.ask}')
            except Exception as e:
                print(f'[{datetime.now()}] Error fetching {symbol}: {e}')
                self.mt5_client.reset()
                self._resolver_initialized = False
                return result, False
        return result, True

    def save_ticks(self, ticks):
        if not ticks:
            return
        filepath = os.path.join(self.output_dir, 'latest.json')
        with open(filepath, 'w') as f:
            json.dump(ticks, f, indent=2)

    def run(self):
        print(f'[TickService] Started. Symbols: {self.symbols}, Interval: {self.interval}s')
        retry_delay = min(1, self.max_retry_seconds)
        self.publisher.start()
        self._publish_status('starting')
        try:
            while not self.stop_event.is_set():
                try:
                    ticks, healthy = self.fetch_ticks()
                    self.save_ticks(ticks)
                    for event in ticks.values():
                        self.publisher.publish(event)
                    self._publish_status(
                        'healthy' if healthy and len(ticks) == len(self.symbols) else 'unhealthy',
                        connected=healthy,
                        published_symbols=sorted(ticks),
                    )
                    delay = self.interval if healthy else retry_delay
                    retry_delay = min(
                        self.max_retry_seconds,
                        retry_delay * 2 if not healthy else 1,
                    )
                except Exception as e:
                    print(f'[{datetime.now()}] TickService error: {e}')
                    self.mt5_client.reset()
                    self._resolver_initialized = False
                    self._publish_status('unhealthy', connected=False, detail=str(e))
                    delay = retry_delay
                    retry_delay = min(self.max_retry_seconds, retry_delay * 2)
                self.stop_event.wait(delay)
        finally:
            self._publish_status('stopped', connected=False)
            self.publisher.stop()
            self.mt5_client.shutdown()

    def stop(self):
        self.stop_event.set()


if __name__ == '__main__':
    service = TickService()
    signal.signal(signal.SIGTERM, lambda signum, frame: service.stop())
    signal.signal(signal.SIGINT, lambda signum, frame: service.stop())
    try:
        service.run()
    finally:
        service.stop()
