import os
import tempfile
import time
import signal
import fcntl
import uuid
import json
import pandas as pd
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from service.config import Settings, load_settings
from service.history.repository import history_storage_id
from service.infrastructure.mt5.client import MT5Client
from service.infrastructure.mt5.session import AccountSessionTransition
from service.infrastructure.mt5.calendar import CalendarObservation, normalize_utc
from service.infrastructure.status.component_status import (
    atomic_write_status,
)

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

    def __init__(self, config_path=None, mt5_client=None,
                 settings: Settings | None = None, calendar_adapter=None):
        settings = settings or load_settings(config_path)
        cfg = settings.history_service
        self.symbols = [symbol.as_legacy_dict() for symbol in cfg.symbols]
        self.interval = cfg.update_interval_seconds
        self.data_path = cfg.data_path
        self.status_path = cfg.status_path
        configured_minimums = cfg.minimum_bars
        self.minimum_bars = {
            timeframe: int(configured_minimums.get(timeframe, minimum))
            for timeframe, minimum in self.DEFAULT_MINIMUM_BARS.items()
        }
        self.fetch_margin_bars = int(
            cfg.fetch_margin_bars
        )
        self.retention_margin_bars = int(
            cfg.retention_margin_bars
            if cfg.retention_margin_bars is not None
            else self.fetch_margin_bars
        )
        self.fetch_page_size = int(
            cfg.fetch_page_size
        )
        self.fetch_timeout_seconds = float(
            cfg.fetch_timeout_seconds
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
        self.mt5_client = (
            mt5_client if mt5_client is not None
            else MT5Client(symbol_aliases=settings.symbol_aliases, settings=settings)
        )
        self.calendar_adapter = calendar_adapter
        self._resolver_initialized = False
        # MT5Client serializes calls with one lock, so multiple executor workers
        # only accumulate blocked retries. Keep exactly one operation in flight
        # and recover after that timed-out operation eventually completes.
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._inflight_future = None
        self._closed = False
        self.sync_status = {}
        # A process-scoped provenance id makes an old publication observable
        # after a worker restart.  The version advances only when a cache is
        # atomically published, while heartbeat status continues independently.
        self.provenance_id = uuid.uuid4().hex
        self.publication_version = 0
        self.last_successful_scan = None
        self.last_successful_publish = None
        self._cycle_published = False
        self._verified_gaps = {}
        self._staged_gaps = {}
        # Calendar evidence is memoized with the same one-hour horizon as gap
        # certificates so status metadata remains useful on later refreshes.
        self._calendar_gap_evidence = {}
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
        generation = details.get('session_generation') or self._session_generation() or 1
        status = {
            'state': state,
            'logical_symbol': symbol,
            'source_symbol': details.get('source_symbol') or symbol,
            'timeframe': timeframe,
            'minimum_bars': self.minimum_bars[timeframe],
            'bars': int(details.get('bars', 0) or 0),
            'source_exhausted': bool(details.get('source_exhausted', False)),
            'session_generation': max(1, int(generation)),
            # Calendar integration is optional; unknown is explicit and
            # clients must not infer a market-open state from cache freshness.
            'session_state': details.get('session_state', 'unknown'),
            'calendar_revision': details.get('calendar_revision', 'unknown'),
            'effective_from': details.get('effective_from'),
            'effective_until': details.get('effective_until'),
            'gaps': details.get('gaps') if isinstance(details.get('gaps'), list) else [],
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'provenance_id': self.provenance_id,
        }
        status.update(details)
        self.sync_status[(symbol, timeframe)] = status
        return status

    def get_sync_status(self):
        return {f'{symbol}:{timeframe}': dict(status)
                for (symbol, timeframe), status in self.sync_status.items()}

    def _publish_status(self, state, **details):
        heartbeat_at = datetime.now(timezone.utc).isoformat()
        sync = self.get_sync_status()
        try:
            return atomic_write_status(
                self.status_path, 'history_service', state,
                # ``heartbeat`` is liveness only.  Consumers must use
                # ``cache_readiness`` for a cache-level readiness decision.
                heartbeat={'state': 'alive', 'updated_at': heartbeat_at},
                sync=sync,
                cache_readiness=sync,
                storage_id=history_storage_id(self.data_path),
                provenance_id=self.provenance_id,
                publication_version=self.publication_version,
                last_successful_scan=self.last_successful_scan,
                last_successful_publish=self.last_successful_publish,
                **details,
            )
        except OSError as exc:
            # A full/read-only runtime directory degrades observability, not
            # the independent history persistence loop itself.
            print(f'[HistoryService] Status publication failed: {exc}')
            return None

    def _session_generation(self):
        guard = getattr(self.mt5_client, 'session_guard', None)
        return getattr(guard, 'generation', None)

    def _history_call_running(self):
        return self._inflight_future is not None and not self._inflight_future.done()

    def _mark_pending_history_call(self, sym_conf):
        for timeframe in sym_conf['timeframes']:
            normalized = str(timeframe).upper()
            if normalized in self.minimum_bars:
                self._set_status(
                    sym_conf['name'], normalized, 'error',
                    detail='previous timed-out MT5 history call is still running',
                )

    def _fetch_pages(self, symbol, timeframe_attr, target_count,
                     filepath=None, cached_source=None):
        """Fetch newest-to-oldest MT5 pages until enough rows or source exhaustion."""
        pages = []
        offset = 0
        source_exhausted = False
        source_identity = None
        deadline = time.monotonic() + self.fetch_timeout_seconds
        while offset < target_count:
            if time.monotonic() >= deadline:
                raise TimeoutError('MT5 positional history budget exhausted')
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
            def read_page(mt5, pos=offset, count=requested,
                          expected_identity=source_identity):
                # Resolve only after call() has checked/reconciled the account
                # and while it holds the same lock as the actual MT5 read.
                broker_symbol = self.mt5_client.resolve(symbol)
                identity = (broker_symbol, self._session_generation())
                if expected_identity is not None and identity != expected_identity:
                    raise AccountSessionTransition(
                        'MT5 account session changed between history pages'
                    )
                if filepath is not None and cached_source != broker_symbol:
                    # Revoke another instrument's cache before a network call
                    # that might time out, using the current guarded mapping.
                    self._invalidate_cache(filepath)
                rates = mt5.copy_rates_from_pos(
                    broker_symbol, timeframe_attr, pos, count
                )
                return rates, identity

            future = self._executor.submit(self.mt5_client.call, read_page)
            self._inflight_future = future
            try:
                rates, identity = future.result(timeout=max(0.001, deadline - time.monotonic()))
            except FutureTimeoutError as exc:
                raise TimeoutError(
                    f'MT5 history page timed out after {self.fetch_timeout_seconds:g}s'
                ) from exc
            finally:
                if future.done():
                    self._inflight_future = None

            source_identity = identity
            if rates is None:
                raise ValueError('MT5 history returned None')
            page = pd.DataFrame(rates)
            if len(page) > requested:
                raise ValueError('MT5 history page exceeds requested count')
            if page.empty:
                source_exhausted = True
                break
            pages.append(page)
            offset += len(page.index)

        if not pages:
            return pd.DataFrame(), source_exhausted, source_identity
        return (
            pd.concat(pages, ignore_index=True).iloc[:target_count],
            source_exhausted,
            source_identity,
        )

    def _verify_gaps(self, symbol, timeframe_attr, timeframe, frame, identity, now):
        """Verify only interior holes, including both closed broker boundaries.

        A range response is authoritative only if its boundaries match the
        positional snapshot. No synthetic bars are ever inserted. Bound work
        per refresh; unfinished verification must not publish readiness.
        """
        key = (symbol, timeframe)
        staged_identity, staged, staged_at = self._staged_gaps.get(key, (None, {}, 0))
        if staged_identity != identity or time.monotonic() - staged_at >= 3600:
            staged, staged_at = {}, time.monotonic()
        current_signatures = set()
        for i in range(1, len(frame)):
            a, b = frame.iloc[i - 1], frame.iloc[i]
            if b['time'] - a['time'] > self.SUPPORTED_TIMEFRAMES[timeframe]:
                current_signatures.add(tuple((row['time'], *(row[c] for c in ('open', 'high', 'low', 'close')))
                                             for row in (a, b)))
        staged = {signature: rows for signature, rows in staged.items()
                  if signature in current_signatures}
        self._staged_gaps[key] = (identity, staged, staged_at)
        remembered_identity, remembered, verified_at = self._verified_gaps.get(key, (None, set(), 0))
        remembered_metadata = self._calendar_gap_evidence.get(key, {})
        if remembered_identity != identity or time.monotonic() - verified_at >= 3600:
            remembered = set()
            verified_at = time.monotonic()
            remembered_metadata = {}
        active = set()
        additions = []
        gap_metadata = []
        duration = self.SUPPORTED_TIMEFRAMES[timeframe]
        deadline = time.monotonic() + self.fetch_timeout_seconds
        calls = 0
        for index in range(1, len(frame)):
            left, right = frame.iloc[index - 1], frame.iloc[index]
            if right['time'] - left['time'] <= duration:
                continue
            signature = tuple((row['time'], *(row[c] for c in ('open', 'high', 'low', 'close')))
                              for row in (left, right))
            active.add(signature)
            if signature in remembered_metadata:
                gap_metadata.append(remembered_metadata[signature])
            if signature in staged:
                additions.append(staged[signature])
                continue
            if signature in remembered:
                continue
            if self.calendar_adapter is not None:
                left_time = left['time'].to_pydatetime()
                right_time = right['time'].to_pydatetime()
                try:
                    classify = getattr(self.calendar_adapter, 'classify_gap', None)
                    if classify is None:
                        classify = getattr(self.calendar_adapter, 'classify', None)
                    if classify is None:
                        raise ValueError('calendar adapter does not implement classify_gap')
                    observation = classify(symbol, left_time, right_time, timeframe)
                    if isinstance(observation, dict):
                        observation = CalendarObservation(**observation)
                    if not isinstance(observation, CalendarObservation):
                        raise ValueError('calendar adapter returned invalid observation')
                    if not observation.covers(left_time, right_time):
                        raise ValueError('calendar observation does not cover history gap')
                except Exception as exc:
                    raise ValueError(f'calendar gap classification failed: {exc}') from exc
                evidence = {
                    'start': left_time.isoformat().replace('+00:00', 'Z'),
                    'end': right_time.isoformat().replace('+00:00', 'Z'),
                    'state': observation.state,
                    'reason': observation.reason,
                    'source': observation.source,
                    'revision': observation.revision,
                    'effective_from': observation.effective_from.isoformat().replace('+00:00', 'Z'),
                    'effective_until': observation.effective_until.isoformat().replace('+00:00', 'Z'),
                }
                remembered_metadata[signature] = evidence
                gap_metadata.append(evidence)
                if observation.state == 'unknown':
                    raise ValueError('calendar gap state is unknown')
                if observation.state == 'closed':
                    remembered.add(signature)
                    self._verified_gaps[key] = (identity, remembered, verified_at)
                    continue
            if calls >= 8 or time.monotonic() >= deadline:
                raise ValueError('history gap verification budget exhausted')
            if right['time'] - left['time'] > duration * self._retention_count(timeframe) * 32:
                raise ValueError('history gap exceeds bounded verification span')
            def read_range(mt5):
                current = (self.mt5_client.resolve(symbol), self._session_generation())
                if current != identity:
                    raise AccountSessionTransition('MT5 account session changed during gap verification')
                return mt5.copy_rates_range(current[0], timeframe_attr,
                                           left['time'].to_pydatetime(), right['time'].to_pydatetime())
            future = self._executor.submit(self.mt5_client.call, read_range)
            self._inflight_future = future
            try:
                rates = future.result(timeout=max(0.001, deadline - time.monotonic()))
            except FutureTimeoutError as exc:
                raise TimeoutError('MT5 history gap verification timed out') from exc
            finally:
                if future.done():
                    self._inflight_future = None
            calls += 1
            if rates is None:
                raise ValueError('MT5 gap verification returned None')
            raw = pd.DataFrame(rates)
            verified = self._validate_bars(raw, timeframe, now)
            if len(verified) != len(raw) or verified.empty or verified.iloc[0]['time'] != left['time'] or verified.iloc[-1]['time'] != right['time']:
                raise ValueError('MT5 gap verification missing closed boundaries')
            # Boundary timestamps must match. Valid broker OHLC revisions are
            # authoritative and replace the older positional/cache snapshot.
            verified['source_symbol'] = identity[0]
            additions.append(verified)
            # Stage actual authoritative bars, never just a filled-hole
            # certificate. This lets the next bounded cycle apply them while
            # leaving the published CSV untouched until all gaps are checked.
            if len(verified) > 2 or any(
                    actual[c] != expected[c]
                    for actual, expected in ((verified.iloc[0], left), (verified.iloc[-1], right))
                    for c in ('open', 'high', 'low', 'close')):
                max_rows = self._retention_count(timeframe) * 32
                if sum(len(rows) for rows in staged.values()) + len(verified) > max_rows:
                    raise ValueError('history gap staging row budget exhausted')
                staged[signature] = verified
                self._staged_gaps[key] = (identity, staged, staged_at)
            # Persist progress between bounded refreshes without publishing an
            # unverified CSV. A failed range never receives a certificate.
            if len(verified) == 2 and all(
                    verified.iloc[i][c] == frame.iloc[index - 1 + i][c]
                    for i in range(2) for c in ('open', 'high', 'low', 'close')):
                remembered.add(signature)
            for i in range(1, len(verified)):
                a, b = verified.iloc[i - 1], verified.iloc[i]
                if b['time'] - a['time'] > duration:
                    remembered.add(tuple((row['time'], *(row[c] for c in ('open', 'high', 'low', 'close')))
                                         for row in (a, b)))
            self._verified_gaps[key] = (identity, remembered, verified_at)
            # Remember each actual remaining broker closure, not a filled hole.
            for i in range(1, len(verified)):
                a, b = verified.iloc[i - 1], verified.iloc[i]
                if b['time'] - a['time'] > duration:
                    active.add(tuple((row['time'], *(row[c] for c in ('open', 'high', 'low', 'close')))
                                     for row in (a, b)))
        self._verified_gaps[key] = (identity, active, verified_at if remembered else time.monotonic())
        self._calendar_gap_evidence[key] = {
            signature: remembered_metadata[signature]
            for signature in active
            if signature in remembered_metadata
        }
        if not additions:
            return frame
        return self._merge_bars(frame, pd.concat(additions, ignore_index=True), timeframe, now)

    @classmethod
    def _gap_metadata(cls, frame, timeframe, calendar_evidence=None):
        """Serialize observed discontinuities for the public metadata contract."""
        if frame is None or frame.empty or 'time' not in frame:
            return []
        duration = cls.SUPPORTED_TIMEFRAMES[timeframe]
        gaps = []
        evidence_by_bounds = {}
        for item in (calendar_evidence or ()):
            if not isinstance(item, dict):
                continue
            try:
                bounds = (normalize_utc(item.get('start'), 'gap start'),
                          normalize_utc(item.get('end'), 'gap end'))
            except (TypeError, ValueError):
                continue
            evidence_by_bounds[bounds] = item
        ordered = frame.sort_values('time')
        for left, right in zip(ordered.iloc[:-1].itertuples(), ordered.iloc[1:].itertuples()):
            if right.time - left.time <= duration:
                continue
            start = pd.Timestamp(left.time).isoformat().replace('+00:00', 'Z')
            end = pd.Timestamp(right.time).isoformat().replace('+00:00', 'Z')
            evidence = evidence_by_bounds.get((
                normalize_utc(start, 'gap start'), normalize_utc(end, 'gap end')
            ))
            classification = 'unknown'
            session_close = session_reopen = None
            if evidence is not None:
                if evidence.get('state') == 'closed':
                    classification = 'expected_session_break'
                    session_close = evidence.get('effective_from')
                    session_reopen = evidence.get('effective_until')
                elif evidence.get('state') == 'open':
                    classification = 'cache_backfill_gap'
            gaps.append({
                'start': start,
                'end': end,
                'session_close': session_close,
                'session_reopen': session_reopen,
                'classification': classification,
            })
        return gaps

    @classmethod
    def _validate_bars(cls, frame, timeframe, now):
        if frame.empty:
            return frame
        required = ['time', 'open', 'high', 'low', 'close', 'tick_volume']
        if any(c not in frame for c in required):
            raise ValueError('invalid MT5 history columns')
        normalized = cls._keep_closed_bars(frame, timeframe, now)
        # Forming bars are permitted; invalid timestamps and duplicate source
        # rows are not silently repaired by merge/deduplication.
        parsed = pd.to_datetime(pd.to_numeric(frame['time'], errors='coerce'), unit='s', utc=True, errors='coerce')
        if parsed.isna().any() or parsed.duplicated().any():
            raise ValueError('invalid or duplicate MT5 history timestamps')
        seconds = int(cls.SUPPORTED_TIMEFRAMES[timeframe].total_seconds())
        if timeframe != 'D1' and (pd.to_numeric(frame['time'], errors='coerce') % seconds != 0).any():
            raise ValueError('MT5 history timestamps are off grid')
        values = frame[required[1:]].apply(pd.to_numeric, errors='coerce')
        import numpy as np
        if not np.isfinite(values.to_numpy()).all() or (values['tick_volume'] < 0).any():
            raise ValueError('invalid MT5 history values')
        if ((values['high'] < values[['open', 'close', 'low']].max(axis=1)) |
                (values['low'] > values[['open', 'close', 'high']].min(axis=1))).any():
            raise ValueError('invalid MT5 history OHLC')
        return normalized.sort_values('time').reset_index(drop=True)

    @classmethod
    def _keep_closed_bars(cls, dataframe, timeframe, now=None):
        if timeframe not in cls.SUPPORTED_TIMEFRAMES:
            raise ValueError(f'Unsupported timeframe: {timeframe}')
        if dataframe.empty:
            return dataframe.copy()

        result = dataframe.copy()
        numeric_times = (pd.Series(float('nan'), index=result.index)
                         if pd.api.types.is_datetime64_any_dtype(result['time'])
                         else pd.to_numeric(result['time'], errors='coerce'))
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
        manifest_path = f'{ready_path}.meta'
        if os.path.exists(manifest_path):
            os.unlink(manifest_path)

    def _publish_cache(self, filepath, broker_symbol):
        cls = type(self)
        directory = os.path.dirname(filepath) or '.'
        temp_path = None
        manifest_temp = None
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
            # Keep the legacy source marker stable for existing readers while
            # publishing provenance/version in an atomic sidecar manifest.
            manifest = {
                'schema_version': 1,
                'provenance_id': self.provenance_id,
                'publication_version': self.publication_version + 1,
                'source_symbol': broker_symbol,
                'published_at': datetime.now(timezone.utc).isoformat(),
            }
            manifest_path = f'{cls._ready_path(filepath)}.meta'
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             prefix=f'.{os.path.basename(manifest_path)}.',
                                             suffix='.tmp', dir=directory, delete=False) as manifest_file:
                manifest_temp = manifest_file.name
                json.dump(manifest, manifest_file, separators=(',', ':'), sort_keys=True)
                manifest_file.flush()
                os.fsync(manifest_file.fileno())
            os.replace(manifest_temp, manifest_path)
            manifest_temp = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            if manifest_temp and os.path.exists(manifest_temp):
                os.unlink(manifest_temp)

    def fetch_incremental(self, now=None):
        self._cycle_published = False
        self._publish_status('syncing')
        if self._history_call_running():
            # A timed-out call can still own MT5Client's lock. Do not block on
            # even its mt5 property/ensure_connected while that call runs.
            for sym_conf in self.symbols:
                self._mark_pending_history_call(sym_conf)
            self._publish_status('degraded')
            return
        was_connected = self.mt5_client.mt5 is not None
        if not self.mt5_client.ensure_connected():
            print(f'[{datetime.now()}] MT5 connection failed')
            for sym_conf in self.symbols:
                for timeframe in sym_conf['timeframes']:
                    normalized = str(timeframe).upper()
                    if normalized in self.minimum_bars:
                        self._invalidate_cache(os.path.join(
                            self.data_path, f"{sym_conf['name']}_{normalized}.csv"
                        ))
                        self._set_status(
                            sym_conf['name'], normalized, 'error',
                            detail='MT5 connection failed',
                        )
            self._publish_status('degraded', detail='MT5 connection failed')
            return

        # Lazy init resolver (第一次成功連線後執行一次)
        if not self._resolver_initialized or not was_connected:
            symbols_list = [s['name'] for s in self.symbols]
            self.mt5_client.init_resolver(symbols_list)
            self._resolver_initialized = True

        for sym_conf in self.symbols:
            symbol = sym_conf['name']
            if self._history_call_running():
                self._mark_pending_history_call(sym_conf)
                continue
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
            for tf_str in sym_conf['timeframes']:
                tf_str = str(tf_str).upper()
                if self._history_call_running():
                    if tf_str in self.minimum_bars:
                        self._set_status(
                            symbol, tf_str, 'error',
                            detail='previous timed-out MT5 history call is still running',
                        )
                    continue
                tf = self._get_timeframe_attr(tf_str)
                if tf is None:
                    print(f'[{datetime.now()}] Unknown timeframe: {tf_str}')
                    continue

                fetch_count = self._fetch_count(tf_str)
                print(
                    f'[{datetime.now()}] Fetching {symbol} {tf_str} '
                    f'(latest {fetch_count} bars)...'
                )

                writer_lock = None
                filepath = None
                try:
                    filepath = os.path.join(self.data_path, f'{symbol}_{tf_str}.csv')
                    writer_lock = open(f'{filepath}.writer.lock', 'a')
                    fcntl.flock(writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    old_df = pd.read_csv(filepath) if os.path.exists(filepath) else None
                    cached_source = None
                    if old_df is not None:
                        source_values = (
                            old_df['source_symbol']
                            if 'source_symbol' in old_df
                            else pd.Series(dtype=object)
                        )
                        source_symbols = set(source_values.dropna().astype(str))
                        if not source_values.isna().any() and len(source_symbols) == 1:
                            cached_source = next(iter(source_symbols))
                    rates, source_exhausted, source_identity = self._fetch_pages(
                        symbol, tf, fetch_count, filepath, cached_source
                    )
                    broker_symbol, generation = source_identity
                    if rates.empty:
                        raise ValueError('MT5 returned no history bars')
                    self._validate_bars(rates, tf_str, now)
                    if self._session_generation() != generation:
                        raise AccountSessionTransition(
                            'MT5 account session changed before history publication'
                        )
                    if cached_source != broker_symbol:
                        old_df = None
                    new_df = rates if not rates.empty else None
                    if new_df is not None:
                        new_df = new_df.copy()
                        new_df['source_symbol'] = broker_symbol
                    final_df = self._merge_bars(old_df, new_df, tf_str, now=now)
                    final_df = final_df.tail(
                        self._retention_count(tf_str)
                    ).reset_index(drop=True)
                    final_df = self._verify_gaps(symbol, tf, tf_str, final_df, source_identity, now)
                    final_df = final_df.tail(self._retention_count(tf_str)).reset_index(drop=True)
                    if self._session_generation() != generation:
                        raise AccountSessionTransition('MT5 account session changed before history publication')
                    minimum = self.minimum_bars[tf_str]
                    source_closed = self._keep_closed_bars(rates, tf_str, now=now)
                    source_sufficient = not source_exhausted or len(source_closed) >= minimum
                    status = ('ready' if len(final_df) >= minimum and source_sufficient
                              else 'insufficient')
                    if not final_df.empty:
                        self._atomic_write_csv(final_df, filepath)
                        if self._session_generation() != generation:
                            raise AccountSessionTransition(
                                'MT5 account session changed before history publication'
                            )
                        if status == 'ready':
                            self._publish_cache(filepath, broker_symbol)
                            self.publication_version += 1
                            self.last_successful_publish = datetime.now(timezone.utc).isoformat()
                            self._cycle_published = True
                            details_version = self.publication_version
                        else:
                            self._invalidate_cache(filepath)
                            details_version = None
                    else:
                        self._invalidate_cache(filepath)
                        details_version = None
                    details = {
                        'bars': len(final_df),
                        'source_exhausted': source_exhausted,
                        'source_symbol': broker_symbol,
                        'gaps': self._gap_metadata(
                            final_df, tf_str,
                            self._calendar_gap_evidence.get((symbol, tf_str), {}).values(),
                        ),
                    }
                    if not final_df.empty:
                        details.update({
                            'effective_from': pd.Timestamp(final_df.iloc[0]['time']).isoformat(),
                            'effective_until': pd.Timestamp(final_df.iloc[-1]['time']).isoformat(),
                        })
                    if self.calendar_adapter is not None:
                        calendar_evidence = list(
                            self._calendar_gap_evidence.get((symbol, tf_str), {}).values()
                        )
                        details['calendar_gaps'] = calendar_evidence
                        if calendar_evidence:
                            revisions = sorted({
                                str(item.get('revision')) for item in calendar_evidence
                                if item.get('revision')
                            })
                            details['calendar_revision'] = ','.join(revisions) or 'unknown'
                            states = {item.get('state') for item in calendar_evidence}
                            details['session_state'] = (
                                'closed' if states == {'closed'} else
                                'open' if 'open' in states else 'unknown'
                            )
                            windows = [
                                (item.get('effective_from'), item.get('effective_until'))
                                for item in calendar_evidence
                                if item.get('effective_from') and item.get('effective_until')
                            ]
                            if windows:
                                details['effective_from'] = min(item[0] for item in windows)
                                details['effective_until'] = max(item[1] for item in windows)
                    if details_version is not None:
                        details.update({
                            'provenance_id': self.provenance_id,
                            'publication_version': details_version,
                            'last_successful_publish': self.last_successful_publish,
                        })
                    if generation is not None:
                        details['session_generation'] = generation
                    if rates.empty:
                        details['detail'] = 'MT5 returned no history bars'
                    self._set_status(symbol, tf_str, status, **details)
                    print(
                        f'[{datetime.now()}] Saved {len(final_df)} closed bars '
                        f'for {symbol} {tf_str} ({status}, minimum={minimum})'
                    )
                except Exception as e:
                    if filepath is not None and not isinstance(e, BlockingIOError):
                        self._invalidate_cache(filepath)
                    self._set_status(
                        symbol, tf_str, 'error', detail=str(e)
                    )
                    print(f'[{datetime.now()}] Error fetching {symbol} {tf_str}: {e}')
                finally:
                    if writer_lock is not None:
                        writer_lock.close()

        states = [item['state'] for item in self.sync_status.values()]
        state = 'healthy' if states and all(item == 'ready' for item in states) else 'degraded'
        # A completed scan is independent of cache readiness: a degraded cycle
        # still records that the worker successfully finished its scan pass.
        self.last_successful_scan = datetime.now(timezone.utc).isoformat()
        self._publish_status(state)

    def run(self):
        print(f'[HistoryService] Started.')
        self._publish_status('starting')
        while True:
            try:
                self.fetch_incremental()
            except Exception as e:
                print(f'[{datetime.now()}] HistoryService error: {e}')
                self.mt5_client.reset()
                self._publish_status('unhealthy', detail=str(e))
            time.sleep(self.interval)

    def close(self):
        """Release resources owned by the History Worker process."""
        if self._closed:
            return
        self._closed = True
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        finally:
            self.mt5_client.shutdown()


def _handle_shutdown_signal(signum, _frame):
    """Unwind the worker so its process-owned resources reach ``finally``."""
    raise SystemExit(128 + signum)


def main():
    """Run the process-owned history worker until it receives a stop signal."""
    service = HistoryService()
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    try:
        service.run()
    finally:
        service.close()


if __name__ == '__main__':
    main()
