import sqlite3

import pandas as pd
import pytest

from service.history.archive import HistoricalOHLCArchive


KEY = ('opaque-session', 'broker-BTC', 'BTC', 'M5')


def bars(times=(100, 200), close=2):
    return pd.DataFrame([dict(time=t, open=2, high=4, low=1, close=close, tick_volume=10) for t in times])


def test_restart_idempotency_correction_and_sorted_range(tmp_path):
    path = tmp_path / 'archive.sqlite'
    archive = HistoricalOHLCArchive(path)
    archive.upsert(*KEY, bars((200, 100)))
    archive.upsert(*KEY, bars())
    archive.mark_completed(*KEY, 100, 200, bars())
    archive = HistoricalOHLCArchive(path)
    assert archive.read_range(*KEY, 100, 200).time.tolist() == [100, 200]
    assert archive.coverage(*KEY)[0]['source_count'] == 2
    archive.upsert(*KEY, bars((100,), close=3))
    assert archive.read_range(*KEY, 100, 200).close.tolist() == [3, 2]
    assert archive.coverage(*KEY) == []


def test_source_and_session_isolation_and_sql_parameters(tmp_path):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    archive.upsert(*KEY, bars())
    for key in [('another-session', *KEY[1:]), (KEY[0], "source'; DROP TABLE bars;--", *KEY[2:])]:
        assert archive.read_range(*key, 100, 200).empty
        archive.upsert(*key, bars((100,), close=3))
    assert archive.read_range(*KEY, 100, 200).close.tolist() == [2, 2]


def test_transaction_rolls_back_rows_and_coverage(tmp_path):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    archive.upsert(*KEY, bars((100,)))
    archive.mark_completed(*KEY, 100, 200, bars((100,)))
    with sqlite3.connect(archive.path) as connection:
        connection.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON bars WHEN NEW.time=200 BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        archive.upsert(*KEY, bars(close=3))
    assert archive.read_range(*KEY, 100, 200).close.tolist() == [2]
    assert len(archive.coverage(*KEY)) == 1


def test_completion_requires_exact_timestamps_and_content(tmp_path):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    archive.upsert(*KEY, bars())
    for source in [bars((100,)), bars(close=3), bars((100, 300)), pd.DataFrame()]:
        with pytest.raises(ValueError):
            archive.mark_completed(*KEY, 100, 200, source)
        assert archive.coverage(*KEY) == []


@pytest.mark.parametrize('frame', [pd.DataFrame(), bars().drop(columns='tick_volume'), bars((100, 100)), bars().assign(high=0), bars().assign(close=float('nan')), bars().assign(time='2025-01-01')])
def test_reject_malformed_frames(tmp_path, frame):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    with pytest.raises(ValueError):
        archive.upsert(*KEY, frame)
    assert archive.read_range(*KEY, 0, 1000).empty


def test_limits_and_aware_timestamp_conversion(tmp_path):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    frame = bars((pd.Timestamp('2025-01-01T08:00:00+08:00'),))
    archive.upsert(*KEY, frame)
    stamp = 1735689600
    assert archive.read_range(*KEY, stamp, stamp).time.tolist() == [stamp]
    archive.upsert(*KEY, bars())
    for args in [(100, 200, 1), (200, 100, 10), (100, 200, 100001)]:
        with pytest.raises(ValueError):
            archive.read_range(*KEY, *args)


def test_contract_digest_is_reproducible_and_order_independent(tmp_path):
    """The coverage digest identifies canonical bar content, not input order."""
    frame = bars((200, 100))
    first = HistoricalOHLCArchive(tmp_path / 'first.sqlite')
    second = HistoricalOHLCArchive(tmp_path / 'second.sqlite')

    first.upsert(*KEY, frame)
    second.upsert(*KEY, bars((100, 200)))
    first_coverage = first.mark_completed(*KEY, 100, 200, bars())
    second_coverage = second.mark_completed(*KEY, 100, 200, bars((200, 100)))

    assert first_coverage['digest'] == second_coverage['digest']
    assert len(first_coverage['digest']) == 64
    assert first_coverage['source_count'] == 2


def test_contract_digest_changes_when_canonical_bar_content_changes(tmp_path):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    archive.upsert(*KEY, bars())
    original = archive.mark_completed(*KEY, 100, 200, bars())

    archive.upsert(*KEY, bars(close=3))
    changed = archive.mark_completed(*KEY, 100, 200, bars(close=3))

    assert original['digest'] != changed['digest']


def test_contract_scope_requires_matching_session_and_source_identity(tmp_path):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    archive.upsert(*KEY, bars())
    archive.mark_completed(*KEY, 100, 200, bars())

    assert archive.read_range(*KEY, 100, 200).shape[0] == 2
    assert archive.read_range('other-session', *KEY[1:], 100, 200).empty
    assert archive.read_range(KEY[0], 'other-source', *KEY[2:], 100, 200).empty


def test_contract_returns_canonical_utc_order_and_required_ohlc_fields(tmp_path):
    archive = HistoricalOHLCArchive(tmp_path / 'archive.sqlite')
    archive.upsert(*KEY, bars((200, 100)))
    archive.mark_completed(*KEY, 100, 200, bars())

    result = archive.read_range(*KEY, 100, 200)

    assert result.columns.tolist() == [
        'time', 'open', 'high', 'low', 'close',
        'tick_volume', 'spread', 'real_volume',
    ]
    assert result.time.tolist() == [100, 200]
    assert result.time.is_monotonic_increasing
