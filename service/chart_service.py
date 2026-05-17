import sys
import os
import base64
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
from datetime import datetime
sys.path.insert(0, '/app')


class ChartService:
    def __init__(self, history_path='/app/service/data/history'):
        self.history_path = history_path

    def _timeframe_to_minutes(self, tf):
        units = {'M': 1, 'H': 60, 'D': 1440, 'W': 10080, 'MN': 43200}
        if not tf:
            return 5
        match = __import__('re').match(r'([A-Z]+)(\d+)', tf.upper())
        if not match:
            return 5
        unit, num = match.group(1), int(match.group(2))
        return num * units.get(unit, 1)

    def get_chart(self, symbol='XAUUSD', timeframe='M5', width=800, height=500, bars=100):
        filepath = os.path.join(self.history_path, f'{symbol}_{timeframe}.csv')
        if not os.path.exists(filepath):
            return {'error': 'Data not found', 'symbol': symbol, 'timeframe': timeframe}

        df = pd.read_csv(filepath)
        df['time'] = pd.to_datetime(df['time'])
        df = df.tail(bars)

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(width / 100, height / 100),
            gridspec_kw={'height_ratios': [3, 1]},
            sharex=True
        )

        plt.subplots_adjust(left=0.08, right=0.95, top=0.95, bottom=0.08)

        times = df['time']
        opens = df['open']
        highs = df['high']
        lows = df['low']
        closes = df['close']
        volumes = df['tick_volume'] if 'tick_volume' in df.columns else pd.Series([0] * len(df))

        for i in range(len(df)):
            color = '#26a69a' if closes.iloc[i] >= opens.iloc[i] else '#ef5350'
            ax1.plot([times.iloc[i], times.iloc[i]], [lows.iloc[i], highs.iloc[i]],
                     color=color, linewidth=1)
            ax1.plot([times.iloc[i], times.iloc[i]],
                     [opens.iloc[i], closes.iloc[i]],
                     color=color, linewidth=4)

        ax1.set_ylabel('Price')
        ax1.grid(True, alpha=0.3)

        vol_colors = ['#26a69a' if closes.iloc[i] >= opens.iloc[i] else '#ef5350'
                      for i in range(len(df))]
        ax2.bar(times, volumes, color=vol_colors, width=0.8 * self._timeframe_to_minutes(timeframe))
        ax2.set_ylabel('Volume')
        ax2.grid(True, alpha=0.3)

        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close(fig)
        buf.seek(0)

        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'image': base64.b64encode(buf.read()).decode('utf-8'),
            'content_type': 'image/png',
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }


if __name__ == '__main__':
    svc = ChartService()
    result = svc.get_chart('XAUUSD', 'M5', 800, 500)
    if 'image' in result:
        img = base64.b64decode(result['image'])
        print(f'Chart: {len(img)} bytes')
    else:
        print('Error:', result.get('error'))
