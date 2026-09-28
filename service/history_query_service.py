import json
import math

import pandas as pd

from service.history_repository import (
    HistoryNotFoundError,
    HistoryNotReadyError,
    HistoryReadError,
)


class HistoryQueryService:
    SUPPORTED_TIMEFRAMES = frozenset({'M5', 'M15', 'H1', 'D1'})
    TIMEFRAME_SECONDS = {'M5': 300, 'M15': 900, 'H1': 3600, 'D1': 86400}
    COLUMNS = ('time', 'open', 'high', 'low', 'close', 'tick_volume')
    SOURCE_COLUMN = 'source_symbol'
    MAX_LIMIT = 5000
    MAX_DAYS = 36500

    def __init__(self, repository, now=None):
        self.repository = repository
        self._now = now or (lambda: pd.Timestamp.now(tz='UTC'))

    def get_rates(self, symbol, timeframe='M5', limit=None, days='0',
                  start_time=None, end_time=None):
        timeframe = str(timeframe).strip().upper()
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            return self._error('Invalid timeframe', 400,
                               supported_timeframes=sorted(self.SUPPORTED_TIMEFRAMES))
        parsed_limit, error = self._parse_integer(limit, 'limit', 1, self.MAX_LIMIT, optional=True)
        if error:
            return error
        parsed_days, error = self._parse_integer(days, 'days', 0, self.MAX_DAYS)
        if error:
            return error

        start, error = self._parse_date(start_time, 'start_time')
        if error:
            return error
        end, error = self._parse_date(end_time, 'end_time')
        if error:
            return error
        if start is not None and end is not None and start > end:
            return self._error('start_time must be before or equal to end_time', 400)

        try:
            dataframe, published_source = self.repository.read(symbol, timeframe)
        except HistoryNotFoundError:
            return self._error('Data not found', 404, symbol=symbol, timeframe=timeframe)
        except HistoryNotReadyError:
            return self._error('Market data not ready', 503)
        except HistoryReadError:
            return self._error('Unable to read market data', 500)

        validated, error = self._validate(dataframe, published_source, timeframe)
        if error:
            return error
        available_range = self._available_range(validated)
        now = self._now()
        if parsed_days:
            validated = validated[validated['time'] >= now - pd.Timedelta(days=parsed_days)]
        if start is not None:
            validated = validated[validated['time'] >= start]
        if end is not None:
            validated = validated[validated['time'] <= end]
        if parsed_limit is not None:
            validated = validated.tail(parsed_limit)
        records = json.loads(validated.to_json(orient='records', date_format='iso'))
        return {
            'data': records,
            'status': 200,
            'timeframe': timeframe,
            'available_range': available_range,
        }

    @staticmethod
    def _available_range(dataframe):
        if dataframe.empty:
            return {'start_time': None, 'end_time': None, 'count': 0}
        return {
            'start_time': dataframe['time'].iloc[0].isoformat().replace('+00:00', 'Z'),
            'end_time': dataframe['time'].iloc[-1].isoformat().replace('+00:00', 'Z'),
            'count': len(dataframe.index),
        }

    def _validate(self, dataframe, published_source, timeframe):
        required = (*self.COLUMNS, self.SOURCE_COLUMN)
        missing = [column for column in required if column not in dataframe.columns]
        if missing:
            return None, self._error('Invalid market data', 422,
                                     detail=f'Missing required columns: {", ".join(missing)}')
        sources = dataframe[self.SOURCE_COLUMN]
        values = set(sources.dropna().astype(str))
        if sources.isna().any() or any(not value.strip() for value in values) or values != {published_source}:
            return None, self._error('Invalid market data', 422,
                                     detail='CSV source identity is missing or ambiguous')
        result = dataframe.loc[:, self.COLUMNS].copy()
        numeric_times = pd.to_numeric(result['time'], errors='coerce')
        parsed_times = pd.to_datetime(result['time'], utc=True, errors='coerce')
        mask = numeric_times.notna()
        if mask.any():
            parsed_times.loc[mask] = pd.to_datetime(numeric_times.loc[mask], unit='s', utc=True, errors='coerce')
        result['time'] = parsed_times
        numeric_columns = self.COLUMNS[1:]
        for column in numeric_columns:
            result[column] = pd.to_numeric(result[column], errors='coerce')
        invalid = result['time'].isna().any() or result.loc[:, numeric_columns].isna().any().any()
        invalid = invalid or any(not series.map(lambda item: math.isfinite(float(item))).all()
                                 for _, series in result.loc[:, numeric_columns].items())
        invalid = invalid or (result['tick_volume'] < 0).any()
        invalid = invalid or ((result['high'] < result[['open', 'close', 'low']].max(axis=1)) |
                              (result['low'] > result[['open', 'close', 'high']].min(axis=1))).any()
        if invalid:
            return None, self._error('Invalid market data', 422,
                                     detail='CSV contains invalid time or OHLCV values')
        if result['time'].duplicated().any() or not result['time'].is_monotonic_increasing:
            return None, self._error('Invalid market data', 422,
                                     detail='CSV timestamps must be ordered and unique')
        seconds = self.TIMEFRAME_SECONDS[timeframe]
        epochs = result['time'].map(lambda value: int(value.timestamp()))
        cadence = result['time'].diff().dropna().dt.total_seconds()
        forming = (result['time'] + pd.Timedelta(seconds=seconds) > self._now()).any()
        if (epochs % seconds != 0).any() or (cadence % seconds != 0).any() or forming:
            return None, self._error('Invalid market data', 422,
                                     detail='CSV contains future, forming, misaligned, or invalid-cadence bars')
        return result, None

    @staticmethod
    def _parse_integer(value, name, minimum, maximum, optional=False):
        if optional and value is None:
            return None, None
        value = '0' if value is None else value
        try:
            stripped = value.strip()
            if not stripped or any(char not in '0123456789' for char in stripped):
                raise ValueError
            parsed = int(stripped)
        except (AttributeError, TypeError, ValueError):
            return None, HistoryQueryService._error(
                f'{name} must be an integer between {minimum} and {maximum}', 400)
        if not minimum <= parsed <= maximum:
            return None, HistoryQueryService._error(
                f'{name} must be an integer between {minimum} and {maximum}', 400)
        return parsed, None

    @staticmethod
    def _parse_date(value, name):
        if value is None:
            return None, None
        try:
            parsed = pd.to_datetime(value, utc=True)
        except Exception:
            return None, HistoryQueryService._error(
                'Invalid date format. Use ISO 8601 (e.g., 2023-01-01 or 2023-01-01T00:00:00Z)', 400)
        return parsed, None

    @staticmethod
    def _error(message, status, **details):
        payload = {'error': message}
        payload.update(details)
        return {'error_response': payload, 'status': status}
