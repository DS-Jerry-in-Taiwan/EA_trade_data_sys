import os

import pandas as pd


class HistoryStorageError(Exception):
    """Base error for completed history storage reads."""


class HistoryNotFoundError(HistoryStorageError):
    pass


class HistoryNotReadyError(HistoryStorageError):
    pass


class HistoryReadError(HistoryStorageError):
    pass


class HistoryRepository:
    """Read-only access to atomically published history CSV files."""

    def __init__(self, data_path='/app/service/data/history'):
        self.data_path = data_path

    def read(self, symbol, timeframe):
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
