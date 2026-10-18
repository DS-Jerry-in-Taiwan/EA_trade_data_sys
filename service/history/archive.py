"""Independent durable OHLC archive; never connects to MT5 or touches hot caches."""
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd


COLUMNS = ('time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume')


def _timestamp(value):
    if isinstance(value, bool):
        raise ValueError('UTC timestamp required')
    if isinstance(value, (int, float)) and math.isfinite(value) and int(value) == value:
        return int(value)
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None or stamp.value % 1_000_000_000:
        raise ValueError('UTC second precision timestamp required')
    return int(stamp.timestamp())


def _key(session_token, source_symbol, logical_symbol, timeframe):
    values = (session_token, source_symbol, logical_symbol, timeframe)
    if any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in values):
        raise ValueError('Nonempty opaque archive identity required')
    return values


def _rows(bars):
    if not isinstance(bars, pd.DataFrame) or bars.empty or not set(COLUMNS[:6]).issubset(bars.columns):
        raise ValueError('Nonempty canonical OHLC frame required')
    rows = []
    for record in bars.to_dict('records'):
        timestamp = _timestamp(record['time'])
        prices = tuple(float(record[c]) for c in COLUMNS[1:5])
        if not all(math.isfinite(p) for p in prices) or prices[1] < max(prices[0], prices[2], prices[3]) or prices[2] > min(prices[0], prices[1], prices[3]):
            raise ValueError('Invalid OHLC prices')
        volumes = tuple(float(record.get(c, 0)) for c in COLUMNS[5:])
        if not all(math.isfinite(v) and v >= 0 for v in volumes):
            raise ValueError('Invalid OHLC volume or spread')
        rows.append((timestamp, *prices, *volumes))
    if len({r[0] for r in rows}) != len(rows):
        raise ValueError('Duplicate timestamps')
    return sorted(rows)


def _digest(rows):
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


class HistoricalOHLCArchive:
    """Callers supply validated closed bars and opaque session identities, never credentials."""

    def __init__(self, path):
        # A concrete filesystem path, never a SQLite URI or a symbol-derived filename.
        path = Path(path)
        if str(path).startswith('file:') or str(path) == ':memory:':
            raise ValueError('Persistent filesystem path required')
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS bars (
                    session TEXT, source TEXT, symbol TEXT, timeframe TEXT,
                    time INTEGER, open REAL, high REAL, low REAL, close REAL,
                    tick_volume REAL, spread REAL, real_volume REAL,
                    PRIMARY KEY(session, source, symbol, timeframe, time));
                CREATE TABLE IF NOT EXISTS coverage (
                    session TEXT, source TEXT, symbol TEXT, timeframe TEXT,
                    start INTEGER, end INTEGER, source_count INTEGER, digest TEXT,
                    PRIMARY KEY(session, source, symbol, timeframe, start, end));
            ''')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def upsert(self, session_token, source_symbol, logical_symbol, timeframe, bars):
        key = _key(session_token, source_symbol, logical_symbol, timeframe)
        rows = _rows(bars)
        with self._connect() as connection:
            # Any edit invalidates coverage intersecting the submitted timestamps.
            connection.execute('DELETE FROM coverage WHERE session=? AND source=? AND symbol=? AND timeframe=? AND start<=? AND end>=?', (*key, rows[-1][0], rows[0][0]))
            connection.executemany('INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [(*key, *r) for r in rows])
        return len(rows)

    def read_range(self, session_token, source_symbol, logical_symbol, timeframe, start, end, limit=10000):
        key = _key(session_token, source_symbol, logical_symbol, timeframe)
        start, end = _timestamp(start), _timestamp(end)
        if start > end or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise ValueError('Invalid archive range or limit')
        with self._connect() as connection:
            rows = connection.execute('SELECT time,open,high,low,close,tick_volume,spread,real_volume FROM bars WHERE session=? AND source=? AND symbol=? AND timeframe=? AND time>=? AND time<=? ORDER BY time LIMIT ?', (*key, start, end, limit + 1)).fetchall()
        if len(rows) > limit:
            raise ValueError('Archive range exceeds limit')
        return pd.DataFrame(rows, columns=COLUMNS)

    def mark_completed(self, session_token, source_symbol, logical_symbol, timeframe, start, end, source_bars):
        key = _key(session_token, source_symbol, logical_symbol, timeframe)
        start, end = _timestamp(start), _timestamp(end)
        source = _rows(source_bars)
        if start > end or any(r[0] < start or r[0] > end for r in source):
            raise ValueError('Source bars outside verified range')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            stored = connection.execute('SELECT time,open,high,low,close,tick_volume,spread,real_volume FROM bars WHERE session=? AND source=? AND symbol=? AND timeframe=? AND time>=? AND time<=? ORDER BY time', (*key, start, end)).fetchall()
            if stored != source:
                raise ValueError('Archived timestamps or content do not match source')
            digest = _digest(source)
            connection.execute('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?,?,?)', (*key, start, end, len(source), digest))
        return {'start': start, 'end': end, 'source_count': len(source), 'digest': digest}

    def coverage(self, session_token, source_symbol, logical_symbol, timeframe):
        key = _key(session_token, source_symbol, logical_symbol, timeframe)
        with self._connect() as connection:
            rows = connection.execute('SELECT start,end,source_count,digest FROM coverage WHERE session=? AND source=? AND symbol=? AND timeframe=? ORDER BY start,end', key).fetchall()
        return [dict(zip(('start', 'end', 'source_count', 'digest'), row)) for row in rows]
