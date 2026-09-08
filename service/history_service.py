import os
import tempfile
import time
import sys
import yaml
import pandas as pd
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client

class HistoryService:
    SUPPORTED_TIMEFRAMES = {
        'M5': pd.Timedelta(minutes=5),
        'M15': pd.Timedelta(minutes=15),
        'H1': pd.Timedelta(hours=1),
        'D1': pd.Timedelta(days=1),
    }
    DEFAULT_MINIMUM_BARS = {
        'M5': 500,
        'M15': 200,
        'H1': 500,
        'D1': 200,
    }
    DEFAULT_FETCH_MARGIN_BARS = 20
    DEFAULT_FETCH_PAGE_SIZE = 500
    DEFAULT_FETCH_TIMEOUT_SECONDS = 10

    def __init__(self, config_path='/app/service/config/settings.yaml', mt5_client=None):
        with open(config_path) as f:
            cfg = (yaml.safe_load(f) or {}).get('history_service', {})
        self.symbols = cfg.get('symbols', [])
        self.interval = cfg.get('update_interval_seconds', 60)
        self.data_path = cfg.get('data_path', '/app/service/data/history')
        configured_minimums = cfg.get('minimum_bars', {})
        self.minimum_bars = {
            timeframe: int(configured_minimums.get(timeframe, minimum))
            for timeframe, minimum in self.DEFAULT_MINIMUM_BARS.items()
        }
        self.fetch_margin_bars = int(
            cfg.get('fetch_margin_bars', self.DEFAULT_FETCH_MARGIN_BARS)
        )
        self.retention_margin_bars = int(
            cfg.get('retention_margin_bars', self.fetch_margin_bars)
        )
        self.fetch_page_size = int(
            cfg.get('fetch_page_size', self.DEFAULT_FETCH_PAGE_SIZE)
        )
        self.fetch_timeout_seconds = float(
            cfg.get('fetch_timeout_seconds', self.DEFAULT_FETCH_TIMEOUT_SECONDS)
        )
        if self.fetch_margin_bars < 0:
            raise ValueError('fetch_margin_bars must be non-negative')
        if self.retention_margin_bars < 0:
            raise ValueError('retention_margin_bars must be non-negative')
        if self.fetch_page_size <= 0:
            raise ValueError('fetch_page_size must be positive')
        if self.fetch_timeout_seconds <= 0:
            raise ValueError('fetch_timeout_seconds must be positive')
        if any(value <= 0 for value in self.minimum_bars.values()):
            raise ValueError('minimum_bars values must be positive')
        self.mt5_client = mt5_client or MT5Client()
        self._resolver_initialized = False
        # MT5Client serializes calls with one lock, so multiple executor workers
        # only accumulate blocked retries. Keep exactly one operation in flight
        # and recover after that timed-out operation eventually completes.
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._inflight_future = None
        self.sync_status = {}
        os.makedirs(self.data_path, exist_ok=True)

    def _get_timeframe_attr(self, tf_str):
        if tf_str not in self.SUPPORTED_TIMEFRAMES:
            return None
        if not self.mt5_client.mt5:
            return None
        return getattr(self.mt5_client.mt5, f'TIMEFRAME_{tf_str}', None)

    def _fetch_count(self, timeframe):
        """Include one forming bar because MT5 position zero may be incomplete."""
        return self._retention_count(timeframe) + 1

    def _retention_count(self, timeframe):
        return self.minimum_bars[timeframe] + self.retention_margin_bars

    def _set_status(self, symbol, timeframe, state, **details):
        status = {
            'state': state,
            'minimum_bars': self.minimum_bars[timeframe],
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        status.update(details)
        self.sync_status[(symbol, timeframe)] = status
        return status

    def get_sync_status(self):
        return {f'{symbol}:{timeframe}': dict(status)
                for (symbol, timeframe), status in self.sync_status.items()}

    def _fetch_pages(self, broker_symbol, timeframe_attr, target_count):
        """Fetch newest-to-oldest MT5 pages until enough rows or source exhaustion."""
        pages = []
        offset = 0
        source_exhausted = False
        while offset < target_count:
            requested = min(self.fetch_page_size, target_count - offset)
            if self._inflight_future is not None:
                if not self._inflight_future.done():
                    raise TimeoutError('previous timed-out MT5 history call is still running')
                # Consume a late result/exception before allowing another call.
                try:
                    self._inflight_future.result()
                except Exception:
                    pass
                self._inflight_future = None
            future = self._executor.submit(
                self.mt5_client.call,
                lambda m, pos=offset, count=requested: m.copy_rates_from_pos(
                    broker_symbol, timeframe_attr, pos, count
                ),
            )
            self._inflight_future = future
            try:
                rates = future.result(timeout=self.fetch_timeout_seconds)
            except FutureTimeoutError as exc:
                raise TimeoutError(
                    f'MT5 history page timed out after {self.fetch_timeout_seconds:g}s'
                ) from exc
            finally:
                if future.done():
                    self._inflight_future = None

            page = pd.DataFrame(rates) if rates is not None else pd.DataFrame()
            if page.empty:
                source_exhausted = True
                break
            pages.append(page)
            offset += len(page.index)

        if not pages:
            return pd.DataFrame(), source_exhausted
        return pd.concat(pages, ignore_index=True).iloc[:target_count], source_exhausted

    @classmethod
    def _keep_closed_bars(cls, dataframe, timeframe, now=None):
        if timeframe not in cls.SUPPORTED_TIMEFRAMES:
            raise ValueError(f'Unsupported timeframe: {timeframe}')
        if dataframe.empty:
            return dataframe.copy()

        result = dataframe.copy()
        numeric_times = pd.to_numeric(result['time'], errors='coerce')
        parsed_times = pd.to_datetime(result['time'], utc=True, errors='coerce')
        numeric_mask = numeric_times.notna()
        if numeric_mask.any():
            parsed_times.loc[numeric_mask] = pd.to_datetime(
                numeric_times.loc[numeric_mask], unit='s', utc=True, errors='coerce'
            )
        result['time'] = parsed_times
        result = result.dropna(subset=['time'])

        current_time = pd.Timestamp(now or datetime.now(timezone.utc))
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        else:
            current_time = current_time.tz_convert('UTC')
        duration = cls.SUPPORTED_TIMEFRAMES[timeframe]
        return result[result['time'] + duration <= current_time]

    @classmethod
    def _merge_bars(cls, old_df, new_df, timeframe, now=None):
        frames = [frame for frame in (old_df, new_df) if frame is not None]
        if not frames:
            return pd.DataFrame()
        merged = pd.concat(frames, ignore_index=True)
        merged = cls._keep_closed_bars(merged, timeframe, now=now)
        return (
            merged.drop_duplicates(subset=['time'], keep='last')
            .sort_values('time')
            .reset_index(drop=True)
        )

    @staticmethod
    def _atomic_write_csv(dataframe, filepath):
        directory = os.path.dirname(filepath) or '.'
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                newline='',
                prefix=f'.{os.path.basename(filepath)}.',
                suffix='.tmp',
                dir=directory,
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                dataframe.to_csv(temp_file, index=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, filepath)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _ready_path(filepath):
        return f'{filepath}.ready'

    @classmethod
    def _invalidate_cache(cls, filepath):
        ready_path = cls._ready_path(filepath)
        if os.path.exists(ready_path):
            os.unlink(ready_path)

    @classmethod
    def _publish_cache(cls, filepath, broker_symbol):
        directory = os.path.dirname(filepath) or '.'
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                prefix=f'.{os.path.basename(filepath)}.ready.',
                suffix='.tmp',
                dir=directory,
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                temp_file.write(broker_symbol)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, cls._ready_path(filepath))
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def fetch_incremental(self, now=None):
        was_connected = self.mt5_client.mt5 is not None
        if not self.mt5_client.ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed')
            for sym_conf in self.symbols:
                for timeframe in sym_conf['timeframes']:
                    normalized = str(timeframe).upper()
                    if normalized in self.minimum_bars:
                        self._set_status(
                            sym_conf['name'], normalized, 'error',
                            detail='MT5 connection failed',
                        )
            return

        # Lazy init resolver (第一次成功連線後執行一次)
        if not self._resolver_initialized or not was_connected:
            symbols_list = [s['name'] for s in self.symbols]
            self.mt5_client.init_resolver(symbols_list)
            self._resolver_initialized = True

        for sym_conf in self.symbols:
            symbol = sym_conf['name']
            if not self.mt5_client.is_resolved(symbol):
                for timeframe in sym_conf['timeframes']:
                    normalized = str(timeframe).upper()
                    if normalized in self.minimum_bars:
                        filepath = os.path.join(
                            self.data_path, f'{symbol}_{normalized}.csv'
                        )
                        self._invalidate_cache(filepath)
                        self._set_status(
                            symbol,
                            normalized,
                            'error',
                            detail='broker symbol unresolved',
                        )
                continue
            broker_symbol = self.mt5_client.resolve(symbol)
            for tf_str in sym_conf['timeframes']:
                tf_str = str(tf_str).upper()
                tf = self._get_timeframe_attr(tf_str)
                if tf is None:
                    print(f'[{datetime.now()}] Unknown timeframe: {tf_str}')
                    continue

                fetch_count = self._fetch_count(tf_str)
                print(
                    f'[{datetime.now()}] Fetching {symbol} {tf_str} '
                    f'(latest {fetch_count} bars)...'
                )

                try:
                    filepath = os.path.join(self.data_path, f'{symbol}_{tf_str}.csv')
                    old_df = pd.read_csv(filepath) if os.path.exists(filepath) else None
                    if old_df is not None:
                        source_values = (
                            old_df['source_symbol']
                            if 'source_symbol' in old_df
                            else pd.Series(dtype=object)
                        )
                        source_symbols = set(source_values.dropna().astype(str))
                        identity_matches = (
                            not source_values.isna().any()
                            and source_symbols == {broker_symbol}
                        )
                        if not identity_matches:
                            # Revoke publication before any network operation;
                            # a timeout/error must not leave another instrument
                            # serviceable under the logical cache name.
                            self._invalidate_cache(filepath)
                            old_df = None
                    rates, source_exhausted = self._fetch_pages(
                        broker_symbol, tf, fetch_count
                    )
                    new_df = rates if not rates.empty else None
                    if new_df is not None:
                        new_df = new_df.copy()
                        new_df['source_symbol'] = broker_symbol
                    final_df = self._merge_bars(old_df, new_df, tf_str, now=now)
                    final_df = final_df.tail(
                        self._retention_count(tf_str)
                    ).reset_index(drop=True)
                    minimum = self.minimum_bars[tf_str]
                    status = 'ready' if len(final_df) >= minimum else 'insufficient'
                    if not final_df.empty:
                        self._atomic_write_csv(final_df, filepath)
                        if status == 'ready':
                            self._publish_cache(filepath, broker_symbol)
                        else:
                            self._invalidate_cache(filepath)
                    else:
                        self._invalidate_cache(filepath)
                    details = {
                        'bars': len(final_df),
                        'source_exhausted': source_exhausted,
                    }
                    if rates.empty:
                        details['detail'] = 'MT5 returned no history bars'
                    self._set_status(symbol, tf_str, status, **details)
                    print(
                        f'[{datetime.now()}] Saved {len(final_df)} closed bars '
                        f'for {symbol} {tf_str} ({status}, minimum={minimum})'
                    )
                except Exception as e:
                    self._set_status(
                        symbol, tf_str, 'error', detail=str(e)
                    )
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
