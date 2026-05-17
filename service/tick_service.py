import os
import json
import time
import sys
import yaml
from datetime import datetime, timezone

sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client


class TickService:
    def __init__(self, config_path='/app/service/config/settings.yaml'):
        with open(config_path) as f:
            cfg = yaml.safe_load(f).get('tick_service', {})
        self.symbols = cfg.get('symbols', ['XAUUSDm'])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.output_dir = cfg.get('output_dir', '/app/service/data/ticks')
        self.mt5_client = MT5Client()
        self._resolver_initialized = False
        os.makedirs(self.output_dir, exist_ok=True)

    def fetch_ticks(self):
        if not self.mt5_client.ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed, retrying next cycle...')
            return {}

        # Lazy init resolver (第一次成功連線後執行一次)
        if not self._resolver_initialized:
            self.mt5_client.init_resolver(self.symbols)
            self._resolver_initialized = True
            if self.mt5_client._resolver and self.mt5_client._resolver.unresolved:
                print(f'[TickService] WARNING: Unresolved symbols: '
                      f'{self.mt5_client._resolver.unresolved}')

        result = {}
        for symbol in self.symbols:
            try:
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
                self.mt5_client.reset()
            time.sleep(self.interval)


if __name__ == '__main__':
    TickService().run()
