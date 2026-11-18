from datetime import datetime, timezone, timedelta
import pytest

from service.infrastructure.mt5.calendar_snapshot import (
    CalendarSnapshot,
    CalendarSnapshotError,
    CalendarSnapshotStore,
)


NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def snapshot(**changes):
    data = dict(
        schema_version="calendar.snapshot.v1",
        snapshot_id="snap-1",
        source="broker:official",
        revision="2026-11-18",
        captured_at=NOW,
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=1),
        session_fingerprint="fp-1",
        session_generation=2,
        session_epoch="epoch-1",
        symbols={"BTC": {"source_symbol": "BTCUSD", "utc_windows": [{"from": NOW.isoformat(), "until": (NOW + timedelta(hours=1)).isoformat()}]}},
    )
    data.update(changes)
    return CalendarSnapshot(**data)


def test_snapshot_round_trip_survives_restart(tmp_path):
    path = tmp_path / "calendar" / "snapshot.json"
    CalendarSnapshotStore(path).save(snapshot())
    restarted = CalendarSnapshotStore(path).load()
    assert restarted == snapshot()
    assert restarted.is_valid(now=NOW, session={"fingerprint": {"id": "fp-1"}, "generation": 2, "epoch": "epoch-1"})


def test_expired_or_session_mismatch_is_not_usable(tmp_path):
    store = CalendarSnapshotStore(tmp_path / "snapshot.json")
    store.save(snapshot())
    assert store.load_valid(now=NOW + timedelta(days=2), session={"fingerprint": {"id": "fp-1"}, "generation": 2, "epoch": "epoch-1"}) is None
    assert store.load_valid(now=NOW, session={"fingerprint": {"id": "other"}, "generation": 2, "epoch": "epoch-1"}) is None
    assert store.load_valid(now=NOW, session={"fingerprint": {"id": "fp-1"}, "generation": 3, "epoch": "epoch-1"}) is None


@pytest.mark.parametrize("field", ["source", "revision", "session_fingerprint", "session_epoch"])
def test_required_identity_fields_rejected(field):
    with pytest.raises(CalendarSnapshotError):
        snapshot(**{field: ""})


def test_unknown_fields_are_rejected(tmp_path):
    raw = snapshot().to_dict()
    raw["unreviewed"] = True
    with pytest.raises(CalendarSnapshotError):
        CalendarSnapshot.from_dict(raw)


def test_corrupt_store_is_fail_closed(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text("not-json")
    assert CalendarSnapshotStore(path).load_valid(now=NOW) is None
