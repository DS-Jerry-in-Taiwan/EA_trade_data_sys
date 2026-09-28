import json
import socket
import sys
import threading
import time
import types
from types import SimpleNamespace

import yaml

# Unit tests must not import the optional pymt5linux transport.
connection_manager = types.ModuleType("core.connection_manager")
connection_manager.MT5Connector = object
connection_manager.close_mt5_connection = lambda mt5: None
sys.modules.setdefault("core.connection_manager", connection_manager)

from service.infrastructure.ipc.tick_protocol import PROTOCOL_VERSION, _ClientChannel
from service.realtime.publisher import TickPublisher
from service.realtime.worker import TickService


class FakeMT5Client:
    def __init__(self, ticks=None, connected=True):
        self.ticks = list(ticks or [])
        self.connected = connected
        self.initialized = []
        self.reset_count = 0
        self.shutdown_count = 0

    def ensure_connected(self):
        return self.connected

    def init_resolver(self, symbols):
        self.initialized.append(tuple(symbols))

    def resolve(self, symbol):
        return f"broker-{symbol}"

    def call(self, operation):
        value = self.ticks.pop(0)
        if isinstance(value, Exception):
            raise value
        return operation(SimpleNamespace(symbol_info_tick=lambda symbol: value))

    def reset(self):
        self.reset_count += 1

    def shutdown(self):
        self.shutdown_count += 1


def _config(tmp_path, **overrides):
    config = {
        "symbols": ["XAUUSDm"],
        "update_interval_seconds": 0.01,
        "output_dir": str(tmp_path / "ticks"),
        "socket_path": str(tmp_path / "ticks.sock"),
        "status_path": str(tmp_path / "tick-status.json"),
        **overrides,
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"tick_service": config}), encoding="utf-8")
    return path


def _event(symbol="BTC", bid=100):
    return {
        "version": PROTOCOL_VERSION,
        "type": "tick",
        "symbol": symbol,
        "bid": bid,
        "ask": bid + 1,
        "last": bid + 0.5,
        "volume": 2,
        "source_time": "2026-09-28T10:00:00+00:00",
        "received_at": "2026-09-28T10:00:01+00:00",
    }


def _read_event(client):
    buffer = b""
    while b"\n" not in buffer:
        buffer += client.recv(4096)
    return json.loads(buffer.split(b"\n", 1)[0])


def test_fetch_tick_uses_mt5_source_time_and_protocol_schema(tmp_path):
    tick = SimpleNamespace(
        bid=1.1, ask=1.2, last=1.15, volume=3, time=1_700_000_000,
        time_msc=1_700_000_000_123,
    )
    service = TickService(
        config_path=_config(tmp_path), mt5_client=FakeMT5Client([tick])
    )

    ticks, healthy = service.fetch_ticks()

    assert healthy is True
    assert set(ticks["XAUUSDm"]) == {
        "version", "type", "symbol", "bid", "ask", "last", "volume",
        "source_time", "received_at",
    }
    assert ticks["XAUUSDm"]["source_time"].endswith("00:00")
    assert ticks["XAUUSDm"]["source_time"].startswith("2023-11-14T22:13:20.123")


def test_tick_failure_resets_transport_and_resolver_without_event(tmp_path):
    client = FakeMT5Client([ConnectionError("lost")])
    service = TickService(config_path=_config(tmp_path), mt5_client=client)

    ticks, healthy = service.fetch_ticks()

    assert ticks == {}
    assert healthy is False
    assert client.reset_count == 1
    assert service._resolver_initialized is False


def test_tick_without_mt5_time_falls_back_to_received_at_utc(tmp_path):
    tick = SimpleNamespace(bid=1.1, ask=1.2, last=1.15, volume=3)
    service = TickService(
        config_path=_config(tmp_path), mt5_client=FakeMT5Client([tick])
    )

    ticks, healthy = service.fetch_ticks()

    event = ticks["XAUUSDm"]
    assert healthy is True
    assert event["source_time"] == event["received_at"]
    assert event["source_time"].endswith("+00:00")


def test_publisher_sends_snapshot_before_live_event_and_accepts_reconnect(tmp_path):
    socket_path = str(tmp_path / "ticks.sock")
    publisher = TickPublisher(socket_path)
    publisher.start()
    try:
        publisher.publish(_event(bid=100))
        first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        first.settimeout(1)
        first.connect(socket_path)
        assert _read_event(first)["bid"] == 100
        first.close()

        publisher.publish(_event(bid=101))
        second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        second.settimeout(1)
        second.connect(socket_path)
        assert _read_event(second)["bid"] == 101
        publisher.publish(_event(bid=102))
        assert _read_event(second)["bid"] == 102
        second.close()
    finally:
        publisher.stop()

    assert not (tmp_path / "ticks.sock").exists()


def test_publisher_removes_stale_socket_and_rejects_regular_file(tmp_path):
    path = str(tmp_path / "stale.sock")
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(path)
    stale.close()

    publisher = TickPublisher(path)
    publisher.start()
    publisher.stop()
    assert not (tmp_path / "stale.sock").exists()

    regular = tmp_path / "regular"
    regular.write_text("do not delete", encoding="utf-8")
    try:
        TickPublisher(str(regular)).start()
    except RuntimeError:
        pass
    else:
        raise AssertionError("regular IPC path should be rejected")
    assert regular.read_text(encoding="utf-8") == "do not delete"


def test_run_stops_publisher_and_mt5_client(tmp_path):
    class Publisher:
        def __init__(self):
            self.started = self.stopped = False

        def start(self):
            self.started = True

        def publish(self, event):
            pass

        def stop(self):
            self.stopped = True

    stop_event = threading.Event()
    client = FakeMT5Client(connected=False)
    publisher = Publisher()
    service = TickService(
        config_path=_config(tmp_path), mt5_client=client, publisher=publisher,
        stop_event=stop_event,
    )
    thread = threading.Thread(target=service.run)
    thread.start()
    time.sleep(0.03)
    service.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert publisher.started and publisher.stopped
    assert client.shutdown_count == 1


def test_status_publication_failure_does_not_stop_tick_service(tmp_path, monkeypatch):
    service = TickService(
        config_path=_config(tmp_path), mt5_client=FakeMT5Client(connected=False)
    )
    monkeypatch.setattr(
        'service.realtime.worker.atomic_write_status',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError('read only')),
    )
    assert service._publish_status('unhealthy') is None


def test_client_channel_absorbs_transient_backpressure_without_blocking_publish():
    release = threading.Event()

    class SlowClient:
        def sendall(self, payload):
            release.wait(1)

        def shutdown(self, how):
            release.set()

        def close(self):
            pass

    closed = []
    channel = _ClientChannel(SlowClient(), closed.append, queue_size=2)
    channel.start()
    assert channel.enqueue(b"first\n") is True
    time.sleep(0.01)  # writer consumes first and becomes blocked in transport

    started = time.monotonic()
    assert channel.enqueue(b"second\n") is True
    assert channel.enqueue(b"third\n") is True
    assert time.monotonic() - started < 0.05
    assert closed == []
    # Only sustained lag beyond the bounded queue marks the consumer unhealthy.
    assert channel.enqueue(b"fourth\n") is False
    channel.close()
