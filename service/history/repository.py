import os
import hashlib
import json

import pandas as pd


class HistoryStorageError(Exception):
    """Base error for completed history storage reads."""


class HistoryNotFoundError(HistoryStorageError):
    pass


class HistoryNotReadyError(HistoryStorageError):
    pass


class HistoryReadError(HistoryStorageError):
    pass


def history_storage_id(data_path):
    """Compare effective storage roots without publishing filesystem paths."""
    return hashlib.sha256(os.path.realpath(data_path).encode()).hexdigest()


class HistoryRepository:
    """Read-only access to atomically published history CSV files."""

    def __init__(self, data_path='/app/service/data/history', publication_status=None):
        self.data_path = data_path
        self.publication_status = publication_status

    def read(self, symbol, timeframe):
        if self.publication_status is not None:
            status = self.publication_status()
            if not status.get('fresh') or status.get('storage_id') != history_storage_id(self.data_path):
                raise HistoryNotReadyError
            # New workers publish a process-scoped provenance id and expose
            # cache readiness separately from their heartbeat.  If that
            # manifest is present, reject a stale .ready marker left by a
            # previous process or a cache that was not published this cycle.
            readiness = status.get('cache_readiness', status.get('sync'))
            if isinstance(readiness, dict):
                cache = readiness.get(f'{symbol}:{timeframe}')
                if not isinstance(cache, dict) or cache.get('state') != 'ready':
                    raise HistoryNotReadyError
                publisher = status.get('provenance_id')
                if publisher is not None and cache.get('provenance_id') != publisher:
                    raise HistoryNotReadyError
        filepath = os.path.join(self.data_path, f'{symbol}_{timeframe}.csv')
        if not os.path.exists(filepath):
            raise HistoryNotFoundError

        try:
            with open(f'{filepath}.ready', encoding='utf-8') as ready_file:
                published_source = ready_file.read().strip()
        except OSError as exc:
            raise HistoryNotReadyError from exc
        if not published_source:
            raise HistoryNotReadyError
        manifest_path = f'{filepath}.ready.meta'
        try:
            with open(manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # Legacy markers remain readable when no publication contract was
            # supplied by the worker, but a provenance-aware status must fail
            # closed when its manifest is missing or malformed.
            if self.publication_status is not None:
                status = self.publication_status()
                if status.get('provenance_id') is not None:
                    raise HistoryNotReadyError
            manifest = None
        if manifest is not None and self.publication_status is not None:
            status = self.publication_status()
            if status.get('provenance_id') is not None and manifest.get('provenance_id') != status.get('provenance_id'):
                raise HistoryNotReadyError
            if status.get('session_epoch') is not None and manifest.get('session_epoch') != status.get('session_epoch'):
                raise HistoryNotReadyError
            if status.get('session_fingerprint') is not None and manifest.get('session_fingerprint') != status.get('session_fingerprint'):
                raise HistoryNotReadyError
            if status.get('session_generation') is not None and manifest.get('session_generation') != status.get('session_generation'):
                raise HistoryNotReadyError
            readiness = status.get('cache_readiness', status.get('sync', {}))
            cache = readiness.get(f'{symbol}:{timeframe}') if isinstance(readiness, dict) else None
            if isinstance(cache, dict) and cache.get('publication_version') is not None:
                if manifest.get('publication_version') != cache.get('publication_version'):
                    raise HistoryNotReadyError

        try:
            dataframe = pd.read_csv(filepath)
        except Exception as exc:
            raise HistoryReadError from exc
        return dataframe, published_source
