"""
Layer E: Data Integrity Verification (8 tests)
================================================
Check that CSV history data is well-formed, chronological, and covers expected
symbols/timeframes.
"""
import pandas as pd
import numpy as np
import pytest
from datetime import datetime, timezone
from pathlib import Path
from service.config import load_settings


REQUIRED_COLUMNS = {"time", "open", "high", "low", "close", "tick_volume"}

_history_settings = load_settings().history_service
EXPECTED_SYMBOLS = {item.name for item in _history_settings.symbols}
EXPECTED_CACHES = {
    f"{item.name}_{timeframe}"
    for item in _history_settings.symbols for timeframe in item.timeframes
}
TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "D1": 86400}
MAX_OPEN_AGE_SECONDS = {"M5": 900, "M15": 2700, "H1": 10800, "D1": 259200}


@pytest.fixture(scope="module")
def history_data():
    """Read configured logical caches; historical broker artifacts are unrelated."""
    assert EXPECTED_CACHES, "No history caches configured"
    dataframes = {}
    for name in sorted(EXPECTED_CACHES):
        path = Path(_history_settings.data_path) / f"{name}.csv"
        assert path.is_file(), f"Missing configured cache: {name}"
        df = pd.read_csv(path)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        dataframes[name] = df
    return dataframes


class TestDataShape:

    def test_all_expected_symbols_present(self, history_data):
        """The set of known symbols must be present in the data"""
        found_symbols = set()
        for key in history_data:
            found_symbols.add(key.split("_")[0])
        missing = EXPECTED_SYMBOLS - found_symbols
        assert not missing, f"Missing symbols: {missing}"

    def test_all_expected_timeframes_present(self, history_data):
        """The set of known timeframes must be present in the data"""
        missing = EXPECTED_CACHES - set(history_data)
        assert not missing, f"Missing timeframes: {missing}"

    def test_required_columns_present(self, history_data):
        """Each CSV must contain all required columns (time, OHLC, volume)"""
        missing_cols = {}
        for name, df in history_data.items():
            cols = set(df.columns)
            missing = REQUIRED_COLUMNS - cols
            if missing:
                missing_cols[name] = missing
        assert not missing_cols, f"Missing columns: {missing_cols}"

    def test_no_empty_dataframes(self, history_data):
        """No CSV should be empty (0 rows)"""
        empty = [name for name, df in history_data.items() if df.empty]
        assert not empty, f"Empty dataframes: {empty}"


class TestChronology:

    MIN_ROWS = {"M5": 500, "M15": 200, "H1": 500, "D1": 200}

    def test_minimum_rows(self, history_data):
        """Each timeframe should have at least a minimum number of rows"""
        short = {}
        for name, df in history_data.items():
            tf = name.split("_")[1]
            min_rows = self.MIN_ROWS.get(tf, 10)
            if len(df) < min_rows:
                short[name] = len(df)
        assert not short, f"Too few rows: {short}"

    def test_time_monotonic(self, history_data):
        """Time column must be strictly increasing"""
        bad = {}
        for name, df in history_data.items():
            if not (df["time"].diff().dropna() > pd.Timedelta(0)).all():
                bad[name] = "non-monotonic time"
        assert not bad, f"Non-monotonic time: {bad}"

    def test_timeframe_cadence_and_broker_crypto_schedule(self, history_data):
        """Crypto is not promised 24/7: gaps must agree with live broker history.

        The HTTP range endpoint reads this same cache, so it cannot independently
        establish a broker closure. Sample the latest gap per timeframe using
        MT5 read-only; older gaps retain grid/order validation but are not
        independently checked against the broker. Retain boundary bars
        to reject failed/empty queries rather than accepting them as closures.
        """
        bad = {}
        client = None
        for name, df in history_data.items():
            symbol, timeframe = name.rsplit("_", 1)
            interval = TIMEFRAME_SECONDS[timeframe]
            deltas = df["time"].diff().dropna().dt.total_seconds()
            if (deltas % interval != 0).any():
                bad[name] = "non-integral timeframe cadence"
            elif symbol.upper().startswith(("BTC", "ETH", "CRYPTO")) and (
                deltas != interval
            ).any():
                from service.infrastructure.mt5.client import MT5Client
                if client is None:
                    assert load_settings().connection.mode == "terminal"
                    client = MT5Client()
                    assert client.ensure_connected(), "Broker comparison unavailable"
                    client.init_resolver(sorted(EXPECTED_SYMBOLS))
                broker_symbol = client.resolve(symbol)
                gap_index = int(np.flatnonzero(
                    df["time"].diff().dt.total_seconds().to_numpy() > interval
                )[-1])
                start = df["time"].iloc[gap_index - 1]
                end = df["time"].iloc[gap_index]
                rates = client.call(lambda mt5: mt5.copy_rates_range(
                    broker_symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"),
                    start.to_pydatetime(), end.to_pydatetime(),
                ))
                assert rates is not None and len(rates), "Broker comparison unavailable"
                broker_times = set(pd.to_datetime(
                    [bar["time"] for bar in rates], unit="s", utc=True
                ))
                cache_times = set(df.loc[
                    df["time"].between(start, end), "time"
                ])
                if start not in broker_times or end not in broker_times:
                    bad[name] = "broker range did not include cache boundary bars"
                elif broker_times != cache_times:
                    bad[name] = "cache timestamps differ from broker history"
        assert not bad, f"Invalid cadence: {bad}"

    def test_recent_data(self, history_data):
        """Caches must be current; bounded weekend tolerance applies off-session."""
        stale = {}
        now = pd.Timestamp.now(tz=timezone.utc)
        for name, df in history_data.items():
            last_time = df["time"].max()
            if last_time.tz is None:
                last_time = last_time.tz_localize("UTC")
            delta = (now - last_time).total_seconds()
            symbol, timeframe = name.rsplit("_", 1)
            maximum_age = MAX_OPEN_AGE_SECONDS[timeframe]
            if (
                not symbol.upper().startswith(("BTC", "ETH", "CRYPTO"))
                and now.weekday() in {5, 6}
            ):
                maximum_age = 259200
            if delta > maximum_age:
                stale[name] = f"{delta:.0f}s old"
        assert not stale, f"Stale data: {stale}"


class TestValues:

    def test_ohlc_reasonable(self, history_data):
        """OHLC values must be positive and high >= low"""
        bad = {}
        for name, df in history_data.items():
            values = df[["open", "high", "low", "close", "tick_volume"]]
            ohlc_ok = np.isfinite(values.to_numpy(dtype=float)).all() and (
                df[["open", "high", "low", "close"]] > 0
            ).all().all()
            if not ohlc_ok:
                bad[name] = "non-positive OHLC values found"
            price_order = (
                (df["high"] >= df[["open", "close", "low"]].max(axis=1)).all()
                and (df["low"] <= df[["open", "close", "high"]].min(axis=1)).all()
                and (df["tick_volume"] >= 0).all()
            )
            if not price_order:
                bad[name] = bad.get(name, "") + "; OHLCV invariant violation"
        assert not bad, f"Unreasonable OHLC: {bad}"
