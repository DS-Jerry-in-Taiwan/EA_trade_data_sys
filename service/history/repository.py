import os
import hashlib

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

        try:
            dataframe = pd.read_csv(filepath)
        except Exception as exc:
            raise HistoryReadError from exc
        return dataframe, published_source
