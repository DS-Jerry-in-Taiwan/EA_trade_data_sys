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


REQUIRED_COLUMNS = {"time", "open", "high", "low", "close", "tick_volume"}

# Dynamically read symbols from settings.yaml
import yaml
try:
    with open("/app/service/config/settings.yaml") as f:
        _cfg = yaml.safe_load(f)
    EXPECTED_SYMBOLS = {
        item["name"] for item in _cfg.get("history_service", {}).get("symbols", [])
    }
except Exception:
    EXPECTED_SYMBOLS = {"XAUUSDm", "BTC", "EURUSDm", "GBPUSDm"}  # fallback
EXPECTED_TIMEFRAMES = {"M5", "M15", "H1", "D1"}
TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "D1": 86400}
MAX_OPEN_AGE_SECONDS = {"M5": 900, "M15": 2700, "H1": 10800, "D1": 259200}


@pytest.fixture(scope="module")
def history_data(csv_paths):
    """Load all history CSVs into a {name: DataFrame} dict."""
    if not csv_paths:
        pytest.skip("No history CSV files found")
    dataframes = {}
    for sym, paths in csv_paths.items():
        for p in paths:
            try:
                df = pd.read_csv(p)
                df["time"] = pd.to_datetime(df["time"])
                key = f"{sym}_{p.split('_')[-1].replace('.csv', '')}"
                dataframes[key] = df
            except Exception:
                pass
    if not dataframes:
        pytest.skip("Could not read any history CSVs")
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
        expected = {
            f"{symbol}_{timeframe}"
            for symbol in EXPECTED_SYMBOLS
            for timeframe in EXPECTED_TIMEFRAMES
        }
        missing = expected - set(history_data)
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

    def test_timeframe_cadence_and_crypto_continuity(self, history_data):
        bad = {}
        for name, df in history_data.items():
            symbol, timeframe = name.rsplit("_", 1)
            interval = TIMEFRAME_SECONDS[timeframe]
            deltas = df["time"].diff().dropna().dt.total_seconds()
            if (deltas % interval != 0).any():
                bad[name] = "non-integral timeframe cadence"
            elif symbol.upper().startswith(("BTC", "ETH", "CRYPTO")) and (
                deltas != interval
            ).any():
                bad[name] = "continuous-market gap"
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
