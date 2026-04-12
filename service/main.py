import time
import os
import yaml
import pandas as pd
from datetime import datetime
from pymt5linux import MetaTrader5

class DataFetcherService:
    def __init__(self, config_path):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        self.mt5 = MetaTrader5(host=self.config['mt5_host'], port=self.config['mt5_port'])
        self.data_path = self.config['data_path']

    def ensure_connected(self):
        if not self.mt5.initialize():
            print(f'[{datetime.now()}] MT5 Connection Lost. Retrying...')
            return False
        return True

    def get_last_timestamp(self, symbol, timeframe):
        file_path = f"{self.data_path}/{symbol}_{timeframe}.csv"
        if os.path.exists(file_path):
            df = pd.read_csv(file_path)
            return pd.to_datetime(df['time'].iloc[-1])
        return None

    def fetch_incremental(self):
        if not self.ensure_connected():
            return

        for sym_conf in self.config['symbols']:
            symbol = sym_conf['name']
            for tf_str in sym_conf['timeframes']:
                tf = getattr(self.mt5, tf_str)
                last_ts = self.get_last_timestamp(symbol, tf_str)
                
                print(f'Fetching {symbol} {tf_str} since {last_ts if last_ts else "Beginning"}...')
                
                if last_ts:
                    # 抓取從最後一個時間點至今的所有 K 棒
                    rates = self.mt5.copy_rates_range(symbol, tf, last_ts, datetime.now())
                else:
                    # 第一次抓取，抓最近 1000 筆
                    rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, 1000)

                if rates is not None and len(rates) > 1:
                    new_df = pd.DataFrame(rates)
                    new_df['time'] = pd.to_datetime(new_df['time'], unit='s')
                    
                    # 移除最後一筆（因為最後一筆可能尚未收盤/不完整）
                    new_df = new_df.iloc[:-1]
                    
                    file_path = f"{self.data_path}/{symbol}_{tf_str}.csv"
                    if os.path.exists(file_path):
                        # 合併並去重
                        old_df = pd.read_csv(file_path)
                        old_df['time'] = pd.to_datetime(old_df['time'])
                        final_df = pd.concat([old_df, new_df]).drop_duplicates(subset=['time']).sort_values('time')
                        final_df.to_csv(file_path, index=False)
                    else:
                        new_df.to_csv(file_path, index=False)
                    print(f'Saved {len(new_df)} new bars for {symbol} {tf_str}')

        self.mt5.shutdown()

    def run(self):
        print("Data Fetcher Service Started.")
        while True:
            try:
                self.fetch_incremental()
            except Exception as e:
                print(f"Error during fetch: {e}")
            time.sleep(self.config['fetch_interval_seconds'])

if __name__ == "__main__":
    service = DataFetcherService('/app/service/config/settings.yaml')
    service.run()
