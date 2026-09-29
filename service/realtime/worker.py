"""Exclusive owner of periodic MT5 Tick acquisition."""

import threading
from datetime import datetime, timezone

from service.config import Settings, load_settings
from service.infrastructure.ipc.tick_protocol import DEFAULT_SOCKET_PATH, PROTOCOL_VERSION
from service.infrastructure.mt5.client import MT5Client
from service.infrastructure.status.component_status import atomic_write_status
from service.realtime.publisher import TickPublisher
from service.realtime.snapshot_store import TickSnapshotStore


class TickService:
    """Poll MT5, normalize Tick events, and publish them to local consumers."""

    def __init__(self, config_path='/app/service/config/settings.yaml', mt5_client=None,
                 publisher=None, snapshot_store=None, stop_event=None,
                 settings: Settings | None = None):
        settings = settings or load_settings(config_path)
        cfg = settings.tick_service
        self.symbols = list(cfg.symbols)
        self.interval = cfg.update_interval_seconds
        self.output_dir = cfg.output_dir
        self.socket_path = cfg.socket_path or DEFAULT_SOCKET_PATH
        self.max_retry_seconds = cfg.max_retry_seconds
        self.status_path = cfg.status_path
        self.mt5_client = mt5_client if mt5_client is not None else MT5Client()
        self.publisher = publisher if publisher is not None else TickPublisher(self.socket_path)
        self.snapshot_store = (
            snapshot_store if snapshot_store is not None
            else TickSnapshotStore(self.output_dir)
        )
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._resolver_initialized = False

    def _publish_status(self, state, **details):
        try:
            return atomic_write_status(
                self.status_path, 'tick_service', state, symbols=self.symbols, **details
            )
        except OSError as exc:
            print(f'[TickService] Status publication failed: {exc}')
            return None

    def fetch_ticks(self):
        if not self.mt5_client.ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed, retrying...')
            self._resolver_initialized = False
            return {}, False
        if not self._resolver_initialized:
            self.mt5_client.init_resolver(self.symbols)
            self._resolver_initialized = True
            resolver = getattr(self.mt5_client, '_resolver', None)
            if resolver and resolver.unresolved:
                print(f'[TickService] WARNING: Unresolved symbols: {resolver.unresolved}')
        result = {}
        for symbol in self.symbols:
            try:
                broker_symbol = self.mt5_client.resolve(symbol)
                tick = self.mt5_client.call(lambda mt5: mt5.symbol_info_tick(broker_symbol))
                if tick:
                    source_timestamp = getattr(tick, 'time_msc', None)
                    if source_timestamp:
                        source_timestamp /= 1000
                    else:
                        source_timestamp = getattr(tick, 'time', None)
                    received_at = datetime.now(timezone.utc).isoformat()
                    source_time = (
                        datetime.fromtimestamp(source_timestamp, timezone.utc).isoformat()
                        if source_timestamp else received_at
                    )
                    result[symbol] = {
                        'version': PROTOCOL_VERSION, 'type': 'tick', 'symbol': symbol,
                        'bid': tick.bid, 'ask': tick.ask, 'last': tick.last,
                        'volume': tick.volume, 'source_time': source_time,
                        'received_at': received_at,
                    }
                    print(f'[{datetime.now()}] {symbol}: Bid={tick.bid}, Ask={tick.ask}')
            except Exception as exc:
                print(f'[{datetime.now()}] Error fetching {symbol}: {exc}')
                self.mt5_client.reset()
                self._resolver_initialized = False
                return result, False
        return result, True

    def save_ticks(self, ticks):
        self.snapshot_store.save(ticks)

    def run(self):
        print(f'[TickService] Started. Symbols: {self.symbols}, Interval: {self.interval}s')
        retry_delay = min(1, self.max_retry_seconds)
        self.publisher.start()
        self._publish_status('starting')
        try:
            while not self.stop_event.is_set():
                try:
                    ticks, healthy = self.fetch_ticks()
                    self.snapshot_store.save(ticks)
                    for event in ticks.values():
                        self.publisher.publish(event)
                    self._publish_status(
                        'healthy' if healthy and len(ticks) == len(self.symbols) else 'unhealthy',
                        connected=healthy, published_symbols=sorted(ticks),
                    )
                    delay = self.interval if healthy else retry_delay
                    retry_delay = min(self.max_retry_seconds, retry_delay * 2 if not healthy else 1)
                except Exception as exc:
                    print(f'[{datetime.now()}] TickService error: {exc}')
                    self.mt5_client.reset()
                    self._resolver_initialized = False
                    self._publish_status('unhealthy', connected=False, detail=str(exc))
                    delay = retry_delay
                    retry_delay = min(self.max_retry_seconds, retry_delay * 2)
                self.stop_event.wait(delay)
        finally:
            self._publish_status('stopped', connected=False)
            self.publisher.stop()
            self.mt5_client.shutdown()

    def stop(self):
        self.stop_event.set()


__all__ = ["TickService"]
