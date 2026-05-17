import os
import time
import sys
import yaml
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client

_executor = ThreadPoolExecutor(max_workers=4)


class HistoryService:
    def __init__(self, config_path='/app/service/config/settings.yaml'):
        with open(config_path) as f:
            cfg = yaml.safe_load(f).get('history_service', {})
        self.symbols = cfg.get('symbols', [])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.data_path = cfg.get('data_path', '/app/service/data/history')
        self.mt5_client = MT5Client()
        os.makedirs(self.data_path, exist_ok=True)

    def _get_timeframe_attr(self, tf_str):
        if not self.mt5_client.mt5:
            return None
        return getattr(self.mt5_client.mt5, f'TIMEFRAME_{tf_str}', None)

    def fetch_incremental(self):
        if not self.mt5_client.ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed')
            return

        for sym_conf in self.symbols:
            symbol = sym_conf['name']
            for tf_str in sym_conf['timeframes']:
                tf = self._get_timeframe_attr(tf_str)
                if tf is None:
                    print(f'[{datetime.now()}] Unknown timeframe: {tf_str}')
                    continue

                print(f'[{datetime.now()}] Fetching {symbol} {tf_str} (latest 100 bars)...')

                try:
                    future = _executor.submit(lambda: self.mt5_client.call(
                        lambda m: m.copy_rates_from_pos(symbol, tf, 0, 100)))
                    rates = future.result()

                    if rates is not None and len(rates) > 1:
                        new_df = pd.DataFrame(rates)
                        new_df['time'] = pd.to_datetime(new_df['time'], unit='s')
                        new_df = new_df.iloc[:-1]

                        filepath = os.path.join(self.data_path, f'{symbol}_{tf_str}.csv')
                        if os.path.exists(filepath):
                            old_df = pd.read_csv(filepath)
                            old_df['time'] = pd.to_datetime(old_df['time'])
                            final_df = pd.concat([old_df, new_df])
                            final_df = final_df.drop_duplicates(subset=['time']).sort_values('time')
                            final_df.to_csv(filepath, index=False)
                        else:
                            new_df.to_csv(filepath, index=False)
                        print(f'[{datetime.now()}] Saved {len(new_df)} bars for {symbol} {tf_str}')
                except Exception as e:
                    print(f'[{datetime.now()}] Error fetching {symbol} {tf_str}: {e}')

    def run(self):
        print(f'[HistoryService] Started.')
        while True:
            try:
                self.fetch_incremental()
            except Exception as e:
                print(f'[{datetime.now()}] HistoryService error: {e}')
                self.mt5_client.reset()
            time.sleep(self.interval)


if __name__ == '__main__':
    HistoryService().run()
