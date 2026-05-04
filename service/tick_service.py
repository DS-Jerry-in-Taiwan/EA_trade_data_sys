import os
import json
import time
import sys
import yaml
from datetime import datetime, timezone

sys.path.insert(0, '/app')
from core.connection_manager import MT5Connector


class TickService:
    def __init__(self, config_path='/app/service/config/settings.yaml'):
        with open(config_path) as f:
            cfg = yaml.safe_load(f).get('tick_service', {})
        self.symbols = cfg.get('symbols', ['XAUUSD'])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.output_dir = cfg.get('output_dir', '/app/service/data/ticks')
        self.connector = None
        self.mt5 = None
        os.makedirs(self.output_dir, exist_ok=True)

    def _ensure_connected(self):
        if self.mt5 is None:
            self.connector = MT5Connector()
            self.mt5 = self.connector.connect()
        return self.mt5 is not None

    def fetch_ticks(self):
        if not self._ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed, retrying next cycle...')
            return {}

        result = {}
        for symbol in self.symbols:
            try:
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
                    result[symbol] = data
                    print(f'[{datetime.now()}] {symbol}: Bid={tick.bid}, Ask={tick.ask}')
            except Exception as e:
                print(f'[{datetime.now()}] Error fetching {symbol}: {e}')
        return result

    def save_ticks(self, ticks):
        if not ticks:
            return
        filepath = os.path.join(self.output_dir, 'latest.json')
        with open(filepath, 'w') as f:
            json.dump(ticks, f, indent=2)

    def run(self):
        print(f'[TickService] Started. Symbols: {self.symbols}, Interval: {self.interval}s')
        while True:
            try:
                ticks = self.fetch_ticks()
                self.save_ticks(ticks)
            except Exception as e:
                print(f'[{datetime.now()}] TickService error: {e}')
                self.mt5 = None
            time.sleep(self.interval)


if __name__ == '__main__':
    TickService().run()
