import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import pytz

# 1. 初始化連接 MT5
if not mt5.initialize(path="C:/Program Files/MetaTrader 5/terminal64.exe", portable=True):
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# 2. 設定參數
symbol = "XAUUSD"  # 請確認您的券商代碼，有些會有後綴 (e.g., XAUUSD.m)
timeframe = mt5.TIMEFRAME_H1  # 支援 M1, M5, H1, D1 等
count = 1000  # 抓取筆數，或使用 copy_rates_range 指定日期區間

# 3. 抓取資料 (從當前時間往前推 count 筆)
# 如果要指定日期區間，改用 mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)

# 4. 關閉連接
mt5.shutdown()

# 5. 資料處理 (轉為 DataFrame)
if rates is None:
    print(f"無法抓取 {symbol} 資料，請檢查代碼是否正確或是尚未在 MT5 視窗中顯示")
else:
    df = pd.DataFrame(rates)
    # 將時間戳記轉換為易讀格式 (MT5 預設是 UTC timestamp)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # 選用欄位：time, open, high, low, close, tick_volume, spread, real_volume
    print(df.head())
    
    # 6. 匯出或是存入 DB
    # df.to_csv("xauusd_h1.csv", index=False)