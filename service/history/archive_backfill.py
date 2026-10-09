"""Explicit GET-only historical range archival; does not change the hot cache.

The caller selects an interval and a durable archive path. No credentials are
accepted as arguments, and output contains aggregate facts only.
"""
import argparse
import json
import os
from pathlib import Path

import pandas as pd

from service.config import load_settings
from service.history.worker import HistoryService
from service.infrastructure.mt5.client import MT5Client


def backfill(client, archive, symbol, timeframe, start, end):
    timeframe = timeframe.upper()
    if timeframe not in HistoryService.SUPPORTED_TIMEFRAMES:
        raise ValueError('unsupported timeframe')
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError('explicit timezone and ordered range required')
    start, end = start.tz_convert('UTC'), end.tz_convert('UTC')
    if end - start > pd.Timedelta(days=32):
        raise ValueError('range exceeds 32-day bounded job')
    now = pd.Timestamp.now(tz='UTC')
    if end + HistoryService.SUPPORTED_TIMEFRAMES[timeframe] > now:
        raise ValueError('range must contain closed bars only')
    if not client.ensure_connected():
        raise ValueError('MT5 readonly source unavailable')
    client.init_resolver([symbol])

    def read(mt5):
        state = client.session_guard.status()
        fingerprint = state.get('fingerprint') or {}
        token = fingerprint.get('id')
        if not state.get('ready') or fingerprint.get('account_mode') != 'DEMO' or not token:
            raise ValueError('confirmed Demo source required')
        source = client.resolve(symbol)
        rates = mt5.copy_rates_range(source, getattr(mt5, f'TIMEFRAME_{timeframe}'),
                                     start.to_pydatetime(), end.to_pydatetime())
        return rates, (token, source, state.get('generation'))

    snapshots = []
    identities = []
    for _ in range(2):
        rows, identity = client.call(read)
        if rows is None or not len(rows) or len(rows) > 20000:
            raise ValueError('missing or oversized readonly source')
        frame = HistoryService._validate_bars(pd.DataFrame(rows), timeframe, now)
        if len(frame) != len(rows) or frame.iloc[0]['time'] != start or frame.iloc[-1]['time'] != end:
            raise ValueError('source must include both closed boundaries')
        snapshots.append(frame)
        identities.append(identity)
    if identities[0] != identities[1]:
        raise ValueError('source changed during verification')
    columns = ['time', 'open', 'high', 'low', 'close', 'tick_volume']
    if not snapshots[0][columns].equals(snapshots[1][columns]):
        raise ValueError('source changed between readonly snapshots')
    token, source, _ = identities[0]
    scope = dict(session_token=token, source_symbol=source, logical_symbol=symbol, timeframe=timeframe)
    archive.upsert(**scope, bars=snapshots[1])
    archive.mark_completed(**scope, start=start, end=end, source_bars=snapshots[1])
    persisted = archive.read_range(**scope, start=start, end=end, limit=20000)
    source_times = set(snapshots[1]['time'])
    stored_times = set(pd.to_datetime(persisted['time'], unit='s', utc=True))
    result = {'source_bars': len(source_times), 'archive_bars': len(stored_times),
              'missing': len(source_times-stored_times), 'extra': len(stored_times-source_times),
              'mutation_attempted': False}
    if result['missing'] or result['extra']:
        raise ValueError('archive coverage mismatch')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-file', required=True)
    parser.add_argument('--archive-path', required=True)
    parser.add_argument('--symbol', default='BTC')
    parser.add_argument('--timeframe', default='M15')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        from service.history.archive import HistoricalOHLCArchive
        target = json.loads(Path(args.target_file).read_text())
        settings = load_settings()
        client = MT5Client(settings=settings, symbol_aliases=settings.symbol_aliases)
        archive = HistoricalOHLCArchive(args.archive_path)
        result = backfill(client, archive, args.symbol, args.timeframe, target['start'], target['end'])
        print(json.dumps(result))
    except Exception:
        # Never print account facts or arbitrary exception payloads.
        print(json.dumps({'completed': False, 'error': 'history_archive_verification_failed',
                          'mutation_attempted': False}))
        return 1
    # Do not invoke MT5 shutdown: it affects other RPyC clients sharing the
    # terminal. Process exit releases this job's socket, not the terminal.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
