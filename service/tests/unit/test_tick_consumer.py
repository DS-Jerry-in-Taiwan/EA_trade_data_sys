import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from service.core.tick_consumer import TickConsumer
from service.core.tick_ipc import PROTOCOL_VERSION, TickPublisher


def _event(symbol="XAUUSDm", received_at=None, bid=2300.0):
    return {
        "version": PROTOCOL_VERSION,
        "type": "tick",
        "symbol": symbol,
        "bid": bid,
        "ask": bid + 0.2,
        "last": bid + 0.1,
        "volume": 3,
        "source_time": "2026-09-28T10:00:00+00:00",
        "received_at": received_at or datetime.now(timezone.utc).isoformat(),
    }


def _wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_consumer_receives_publisher_snapshot_and_live_event(tmp_path):
    path = str(tmp_path / "ticks.sock")
    publisher = TickPublisher(path)
    publisher.start()
    publisher.publish(_event())
    consumer = TickConsumer(path, reconnect_initial=0.01, reconnect_max=0.05)
    consumer.start()
    try:
        assert _wait_for(lambda: consumer.get("XAUUSDm") is not None)
        publisher.publish(_event(bid=2301.0))
        assert _wait_for(lambda: consumer.get("XAUUSDm")["bid"] == 2301.0)
    finally:
        consumer.stop()
        publisher.stop()


def test_consumer_reconnects_after_publisher_restart(tmp_path):
    path = str(tmp_path / "ticks.sock")
    consumer = TickConsumer(path, reconnect_initial=0.01, reconnect_max=0.05)
    first = TickPublisher(path)
    first.start()
    consumer.start()
    try:
        first.publish(_event(bid=1.0))
        assert _wait_for(lambda: consumer.get("XAUUSDm") is not None)
        first.stop()
        assert _wait_for(lambda: not consumer.connected)

        second = TickPublisher(path)
        second.start()
        try:
            second.publish(_event(bid=2.0))
            assert _wait_for(lambda: consumer.get("XAUUSDm")["bid"] == 2.0)
            assert consumer.connected
        finally:
            second.stop()
    finally:
        consumer.stop()


def test_consumer_handles_fragmented_and_multiple_ndjson_frames(tmp_path):
    path = str(tmp_path / "ticks.sock")
    ready = threading.Event()

    def serve():
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        ready.set()
        client, _ = server.accept()
        frames = "\n".join(json.dumps(item) for item in (
            _event("XAUUSDm"), _event("BTC", bid=60000.0)
        )) + "\n"
        encoded = frames.encode()
        client.sendall(encoded[:7])
        client.sendall(encoded[7:31])
        client.sendall(encoded[31:])
        client.close()
        server.close()

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(1)
    consumer = TickConsumer(path, reconnect_initial=0.05, reconnect_max=0.05)
    consumer.start()
    try:
        assert _wait_for(lambda: consumer.get("XAUUSDm") and consumer.get("BTC"))
    finally:
        consumer.stop()
        thread.join(timeout=1)


def test_freshness_rejects_old_or_future_events():
    consumer = TickConsumer("unused")
    now = datetime.now(timezone.utc)
    assert consumer.is_fresh(_event(received_at=(now - timedelta(seconds=4)).isoformat()), 5, now)
    assert not consumer.is_fresh(
        _event(received_at=(now - timedelta(seconds=6)).isoformat()), 5, now
    )
    assert not consumer.is_fresh(
        _event(received_at=(now + timedelta(seconds=1)).isoformat()), 5, now
    )


def test_invalid_frame_is_ignored():
    consumer = TickConsumer("unused")
    consumer._accept_line(b"not-json")
    consumer._accept_line(json.dumps({"version": 99, "type": "tick"}).encode())
    assert consumer.symbols == []


def test_wrong_field_types_do_not_pollute_snapshot():
    consumer = TickConsumer("unused")
    invalid_events = []
    for field, value in (
        ("symbol", 123),
        ("received_at", 123),
        ("received_at", "not-a-timestamp"),
        ("source_time", 123),
        ("source_time", "2026-09-28 10:00:00"),
        ("bid", "2300"),
        ("ask", None),
        ("last", float("nan")),
        ("volume", True),
    ):
        event = _event()
        event[field] = value
        invalid_events.append(event)

    for event in invalid_events:
        consumer._accept_line(json.dumps(event).encode())
    assert consumer.symbols == []
