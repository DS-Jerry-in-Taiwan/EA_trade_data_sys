import json
from types import SimpleNamespace

import pytest

from service.history.repository import HistoryNotReadyError, HistoryRepository, history_storage_id
from service.infrastructure.ipc.tick_protocol import PROTOCOL_VERSION, TickPublisher
from service.realtime.consumer import TickConsumer
from service.realtime.snapshot_store import TickSnapshotStore
from service.execution.errors import ExecutionError


def _event(*, fingerprint="account-a", generation=1, epoch="boot-a", bid=1.0):
    return {
        "version": PROTOCOL_VERSION, "type": "tick", "symbol": "BTC",
        "bid": bid, "ask": bid + 1, "last": bid, "volume": 1,
        "source_time": "2026-01-01T00:00:00+00:00",
        "received_at": "2026-01-01T00:00:01+00:00",
        "session_fingerprint": fingerprint,
        "session_generation": generation,
        "session_epoch": epoch,
    }


def test_consumer_clears_previous_session_snapshot_before_accepting_new_event():
    consumer = TickConsumer("unused")
    consumer._accept_line(json.dumps(_event(bid=1)).encode())
    consumer._accept_line(json.dumps({**_event(fingerprint="account-b", epoch="boot-b", bid=2),
                                      "symbol": "EURUSDm"}).encode())
    assert consumer.get("BTC") is None
    assert consumer.get("EURUSDm")["bid"] == 2


def test_publisher_drops_old_snapshot_when_session_changes():
    publisher = TickPublisher("unused")
    publisher.publish(_event(bid=1))
    publisher.publish({**_event(fingerprint="account-b", epoch="boot-b", bid=2), "symbol": "EURUSDm"})
    assert publisher.latest == {"EURUSDm": {**_event(fingerprint="account-b", epoch="boot-b", bid=2),
                                             "symbol": "EURUSDm"}}


def test_tick_snapshot_sidecar_invalidates_across_restart(tmp_path):
    store = TickSnapshotStore(str(tmp_path))
    store.save({"BTC": _event()}, session_identity={
        "session_fingerprint": "account-a", "session_generation": 1, "session_epoch": "boot-a",
    })
    restarted = TickSnapshotStore(str(tmp_path))
    restarted.save({}, session_identity={
        "session_fingerprint": "account-a", "session_generation": 1, "session_epoch": "boot-b",
    })
    assert not (tmp_path / "latest.json").exists()
    assert not (tmp_path / "latest.json.meta").exists()


def test_history_manifest_rejects_session_identity_mismatch(tmp_path):
    root = tmp_path / "history"
    root.mkdir()
    (root / "BTC_M5.csv").write_text("time,close\n1,2\n")
    (root / "BTC_M5.csv.ready").write_text("broker-BTC")
    (root / "BTC_M5.csv.ready.meta").write_text(json.dumps({
        "provenance_id": "boot-a", "publication_version": 1,
        "session_fingerprint": "account-a", "session_generation": 1,
    }))
    status = {
        "fresh": True, "storage_id": history_storage_id(root),
        "provenance_id": "boot-b", "session_fingerprint": "account-a",
        "session_generation": 1,
    }
    with pytest.raises(HistoryNotReadyError):
        HistoryRepository(str(root), publication_status=lambda: status).read("BTC", "M5")


def test_quote_rejects_mapping_snapshot_transition():
    from service.execution.mt5_adapter import MT5ExecutionAdapter

    info = SimpleNamespace(trade_tick_size=.01, trade_tick_value_loss=2,
                           point=.001, volume_min=.01, volume_step=.01,
                           trade_stops_level=10, trade_freeze_level=5)
    mt5 = SimpleNamespace(
        symbol_info=lambda _: info,
        symbol_info_tick=lambda _: SimpleNamespace(
            bid=100, ask=101, time_msc=1760000000000,
        ),
        account_info=lambda: SimpleNamespace(currency="USD"),
        order_calc_profit=lambda *_args: -3,
    )
    first = SimpleNamespace(
        valid=True, mapping={"BTC": "BROKER.BTC"}, mapping_revision=1,
        catalog_digest="catalog-a", mapping_digest="mapping-a",
        session_fingerprint="account-a", session_generation=1,
        bound_to=lambda fingerprint, generation: (fingerprint, generation) == ("account-a", 1),
    )
    second = SimpleNamespace(
        valid=True, mapping={"BTC": "BROKER.OTHER"}, mapping_revision=2,
        catalog_digest="catalog-b", mapping_digest="mapping-b",
        session_fingerprint="account-a", session_generation=1,
        bound_to=lambda fingerprint, generation: (fingerprint, generation) == ("account-a", 1),
    )

    class Client:
        _resolver = object()
        def __init__(self):
            self.calls = 0
        def resolve(self, _symbol):
            return "BROKER.BTC"
        @property
        def mapping_snapshot(self):
            self.calls += 1
            return first if self.calls == 1 else second
        def call(self, callback):
            return callback(mt5)

    client = Client()
    adapter = MT5ExecutionAdapter(client)
    adapter._fresh_mutation_status = lambda _mt5: {
        "ready": True, "generation": 1,
        "fingerprint": {"id": "account-a", "account_mode": "DEMO"},
    }
    with pytest.raises(ExecutionError) as error:
        adapter.quote_risk("BTC")
    assert getattr(error.value, "code", None) == "mapping_session_transition"


def test_consumer_clears_snapshot_on_partial_session_identity_event():
    consumer = TickConsumer("unused")
    consumer._accept_line(json.dumps(_event()).encode())
    malformed = _event(bid=2)
    malformed.pop("session_epoch")
    consumer._accept_line(json.dumps(malformed).encode())
    assert consumer.symbols == []


def test_publisher_clears_replay_state_on_invalid_session_identity_event():
    publisher = TickPublisher("unused")
    publisher.publish(_event())
    malformed = _event(bid=2)
    malformed["session_generation"] = "one"
    with pytest.raises(ValueError):
        publisher.publish(malformed)
    assert publisher.latest == {}


def test_snapshot_store_rejects_invalid_identity_without_partial_latest(tmp_path):
    store = TickSnapshotStore(str(tmp_path))
    store.save({"BTC": _event()}, session_identity={
        "session_fingerprint": "account-a", "session_generation": 1,
        "session_epoch": "boot-a",
    })
    with pytest.raises(ValueError):
        store.save({"BTC": _event(bid=2)}, session_identity={
            "session_fingerprint": "account-a", "session_generation": 1,
        })
    assert not (tmp_path / "latest.json").exists()
    assert not (tmp_path / "latest.json.meta").exists()


def test_snapshot_store_rejects_partial_event_identity(tmp_path):
    store = TickSnapshotStore(str(tmp_path))
    identity = {
        "session_fingerprint": "account-a", "session_generation": 1,
        "session_epoch": "boot-a",
    }
    malformed = _event()
    malformed.pop("session_epoch")
    with pytest.raises(ValueError):
        store.save({"BTC": malformed}, session_identity=identity)
    assert not (tmp_path / "latest.json").exists()
    assert not (tmp_path / "latest.json.meta").exists()


def test_quote_revalidates_mapping_snapshot_before_serialization():
    from service.execution.mt5_adapter import MT5ExecutionAdapter

    info = SimpleNamespace(trade_tick_size=.01, trade_tick_value_loss=2,
                           point=.001, volume_min=.01, volume_step=.01,
                           trade_stops_level=10, trade_freeze_level=5)
    mt5 = SimpleNamespace(
        symbol_info=lambda _: info,
        symbol_info_tick=lambda _: SimpleNamespace(
            bid=100, ask=101, time_msc=1760000000000,
        ),
        account_info=lambda: SimpleNamespace(currency="USD"),
        order_calc_profit=lambda *_args: -3,
    )
    snapshot = SimpleNamespace(
        valid=True, mapping={"BTC": "BROKER.BTC"}, mapping_revision=1,
        catalog_digest="catalog-a", mapping_digest="mapping-a",
        session_fingerprint="account-a", session_generation=1,
        bound_to=lambda fingerprint, generation: (fingerprint, generation) == ("account-a", 1),
    )
    changed = SimpleNamespace(
        valid=True, mapping={"BTC": "BROKER.BTC"}, mapping_revision=2,
        catalog_digest="catalog-b", mapping_digest="mapping-b",
        session_fingerprint="account-a", session_generation=1,
        bound_to=lambda fingerprint, generation: (fingerprint, generation) == ("account-a", 1),
    )

    class Client:
        _resolver = object()

        def __init__(self):
            self.calls = 0

        def resolve(self, _symbol):
            return "BROKER.BTC"

        @property
        def mapping_snapshot(self):
            self.calls += 1
            return changed if self.calls >= 3 else snapshot

        def call(self, callback):
            return callback(mt5)

    adapter = MT5ExecutionAdapter(Client())
    adapter._fresh_mutation_status = lambda _mt5: {
        "ready": True, "generation": 1,
        "fingerprint": {"id": "account-a", "account_mode": "DEMO"},
    }
    with pytest.raises(ExecutionError) as error:
        adapter.quote_risk("BTC")
    assert getattr(error.value, "code", None) == "mapping_session_transition"
