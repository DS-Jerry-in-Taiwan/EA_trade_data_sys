import os
import time
import sys
import yaml
import pandas as pd
from datetime import datetime

sys.path.insert(0, '/app')
from core.connection_manager import MT5Connector


class HistoryService:
    def __init__(self, config_path='/app/service/config/settings.yaml'):
        with open(config_path) as f:
            cfg = yaml.safe_load(f).get('history_service', {})
        self.symbols = cfg.get('symbols', [])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.data_path = cfg.get('data_path', '/app/service/data/history')
        self.connector = None
        self.mt5 = None
        os.makedirs(self.data_path, exist_ok=True)

    def _ensure_connected(self):
        if self.mt5 is None:
            self.connector = MT5Connector()
            self.mt5 = self.connector.connect()
        return self.mt5 is not None

    def _get_timeframe_attr(self, tf_str):
        if not self.mt5:
            return None
        return getattr(self.mt5, f'TIMEFRAME_{tf_str}', None)

    def _get_last_timestamp(self, symbol, tf_str):
        filepath = os.path.join(self.data_path, f'{symbol}_{tf_str}.csv')
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            if not df.empty:
                return pd.to_datetime(df['time'].iloc[-1])
        return None

    def fetch_incremental(self):
        if not self._ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed')
            return

        for sym_conf in self.symbols:
            symbol = sym_conf['name']
            for tf_str in sym_conf['timeframes']:
                tf = self._get_timeframe_attr(tf_str)
                if tf is None:
                    print(f'[{datetime.now()}] Unknown timeframe: {tf_str}')
                    continue

                last_ts = self._get_last_timestamp(symbol, tf_str)
                print(f'[{datetime.now()}] Fetching {symbol} {tf_str} since {last_ts or "beginning"}...')

                try:
                    if last_ts:
                        rates = self.mt5.copy_rates_range(symbol, tf, last_ts.to_pydatetime(), datetime.now())
                    else:
                        rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, 1000)

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
                self.mt5 = None
            time.sleep(self.interval)


if __name__ == '__main__':
    HistoryService().run()
