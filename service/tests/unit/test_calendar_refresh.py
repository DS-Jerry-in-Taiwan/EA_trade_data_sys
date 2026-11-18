from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread
import time

from service.infrastructure.mt5.calendar_refresh import CalendarRefreshCoordinator
from service.infrastructure.mt5.calendar_snapshot import CalendarSnapshotStore


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_snapshot(session, *, now=NOW, valid_until=None):
    return __import__("service.infrastructure.mt5.calendar_snapshot", fromlist=["CalendarSnapshot"]).CalendarSnapshot(
        schema_version="calendar.snapshot.v1", snapshot_id="s1", source="test", revision="r1",
        captured_at=now, valid_from=now - timedelta(minutes=1),
        valid_until=valid_until or now + timedelta(hours=1),
        session_fingerprint=session["fingerprint"], session_generation=session["generation"],
        session_epoch=session["epoch"],
        symbols={"EURUSDm": {"source_symbol": "EURUSD", "utc_windows": [{"from": now.isoformat(), "until": (now + timedelta(hours=1)).isoformat()}]}},
    )


class Provider:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def fetch(self, *, session):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result(session)


def test_valid_snapshot_is_reused_without_refresh(tmp_path):
    session = {"fingerprint": "fp", "generation": 1, "epoch": "e1"}
    store = CalendarSnapshotStore(tmp_path / "snapshot.json")
    store.save(make_snapshot(session))
    provider = Provider(lambda s: (_ for _ in ()).throw(AssertionError("must not fetch")))
    coordinator = CalendarRefreshCoordinator(store, provider, clock=lambda: NOW)
    assert coordinator.snapshot(session=session).snapshot_id == "s1"
    assert provider.calls == 0
    assert coordinator.status().state == "valid"


def test_failed_refresh_uses_bounded_backoff(tmp_path):
    session = {"fingerprint": "fp", "generation": 1, "epoch": "e1"}
    provider = Provider(RuntimeError("offline"))
    coordinator = CalendarRefreshCoordinator(CalendarSnapshotStore(tmp_path / "snapshot.json"), provider, clock=lambda: NOW, base_backoff_seconds=10, max_backoff_seconds=20)
    assert coordinator.snapshot(session=session) is None
    assert provider.calls == 1
    assert coordinator.snapshot(session=session) is None
    assert provider.calls == 1
    assert coordinator.status().state == "backoff"


def test_session_change_invalidates_snapshot_and_provider_cannot_rebind_old(tmp_path):
    session = {"fingerprint": "fp", "generation": 1, "epoch": "e1"}
    changed = {"fingerprint": "other", "generation": 2, "epoch": "e2"}
    store = CalendarSnapshotStore(tmp_path / "snapshot.json")
    store.save(make_snapshot(session))
    provider = Provider(lambda s: make_snapshot(session))
    coordinator = CalendarRefreshCoordinator(store, provider, clock=lambda: NOW, base_backoff_seconds=10)
    assert coordinator.snapshot(session=changed) is None
    assert coordinator.status().state == "error"
    assert provider.calls == 1


def test_successful_refresh_persists_and_is_available_after_restart(tmp_path):
    session = {"fingerprint": "fp", "generation": 1, "epoch": "e1"}
    store = CalendarSnapshotStore(tmp_path / "snapshot.json")
    provider = Provider(lambda s: make_snapshot(s))
    coordinator = CalendarRefreshCoordinator(store, provider, clock=lambda: NOW)
    assert coordinator.snapshot(session=session).session_fingerprint == "fp"
    restarted = CalendarRefreshCoordinator(store, Provider(RuntimeError("must not fetch")), clock=lambda: NOW)
    assert restarted.snapshot(session=session).session_generation == 1


def test_concurrent_refreshes_are_single_flight(tmp_path):
    session = {"fingerprint": "fp", "generation": 1, "epoch": "e1"}
    started = Barrier(2)

    class BlockingProvider:
        calls = 0

        def fetch(self, *, session):
            self.calls += 1
            started.wait(timeout=2)
            time.sleep(0.05)
            return make_snapshot(session)

    provider = BlockingProvider()
    coordinator = CalendarRefreshCoordinator(CalendarSnapshotStore(tmp_path / "snapshot.json"), provider, clock=lambda: NOW)
    results = []

    def run():
        results.append(coordinator.refresh(session=session))

    first = Thread(target=run)
    second = Thread(target=run)
    first.start()
    # Ensure the first caller owns the in-flight refresh before the second.
    started.wait(timeout=2)
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)
    assert provider.calls == 1
    assert len(results) == 2
    assert all(result.state == "valid" for result in results)
