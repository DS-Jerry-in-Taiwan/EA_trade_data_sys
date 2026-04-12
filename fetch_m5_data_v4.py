import os
import sys
import pandas as pd
from datetime import datetime, timedelta, timezone
import time
from core.connection_manager import MT5Connector

def fetch_data(years, filename):
    connector = MT5Connector()
    mt5 = connector.connect()
    
    if not mt5:
        print('>>> [CRITICAL] All connection attempts failed!')
        return

    acc = connector.get_active_account()
    symbol = f'XAUUSD{acc["suffix"]}'
    print(f'>>> [START] Downloading {symbol} ({years}y / M5) from {acc["server"]}')
    
    all_data = []
    utc_to = datetime.now(timezone.utc)
    total_days = years * 365
    chunk_days = 30
    steps = (total_days // chunk_days) + 1

    for i in range(steps):
        end_date = utc_to - timedelta(days=i*chunk_days)
        start_date = utc_to - timedelta(days=(i+1)*chunk_days)
        if start_date < datetime(2014, 1, 1, tzinfo=timezone.utc):
            start_date = datetime(2014, 1, 1, tzinfo=timezone.utc)

        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_date, end_date)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            all_data.append(df)
            print(f'  - OK: {start_date.date()} to {end_date.date()} ({len(df)} bars)')
        
        if start_date <= datetime(2014, 1, 1, tzinfo=timezone.utc): break
        time.sleep(0.05)

    if all_data:
        final_df = pd.concat(all_data).drop_duplicates().sort_values('time')
        final_df.to_csv(f'/app/{filename}', index=False)
        print(f'>>> [DONE] Rows: {len(final_df)} | Path: /app/{filename}')
    mt5.shutdown()

if __name__ == "__main__":
    fetch_data(10, 'gold_history_10years_m5_mq.csv')
