"""Reconnectable consumer for the Realtime worker's Unix-socket stream."""

import json
import math
import socket
import threading
from datetime import datetime, timezone

from service.infrastructure.ipc.tick_protocol import (
    DEFAULT_SOCKET_PATH,
    PROTOCOL_VERSION,
)


class TickConsumer:
    """Maintain the Gateway's in-memory Tick snapshot from NDJSON events."""

    def __init__(self, socket_path=DEFAULT_SOCKET_PATH, on_tick=None,
                 reconnect_initial=0.1, reconnect_max=5.0, stop_event=None):
        self.socket_path = socket_path
        self.on_tick = on_tick
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._ticks = {}
        self._lock = threading.RLock()
        self._connected = False
        self._thread = None
        self._socket = None

    @property
    def connected(self):
        with self._lock:
            return self._connected

    @property
    def symbols(self):
        with self._lock:
            return list(self._ticks)

    def get(self, symbol):
        with self._lock:
            event = self._ticks.get(symbol)
            return dict(event) if event is not None else None

    def is_fresh(self, event, max_age_seconds, now=None):
        if not event:
            return False
        try:
            received_at = datetime.fromisoformat(event["received_at"].replace("Z", "+00:00"))
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            current = now if now is not None else datetime.now(timezone.utc)
            return 0 <= (current - received_at).total_seconds() <= max_age_seconds
        except (KeyError, TypeError, ValueError):
            return False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(
            target=self.run, name="tick-ipc-consumer", daemon=True
        )
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None

    def run(self):
        retry_delay = self.reconnect_initial
        while not self.stop_event.is_set():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            self._socket = sock
            try:
                sock.connect(self.socket_path)
                with self._lock:
                    self._connected = True
                retry_delay = self.reconnect_initial
                self._read_stream(sock)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                pass
            finally:
                with self._lock:
                    self._connected = False
                try:
                    sock.close()
                except OSError:
                    pass
                if self._socket is sock:
                    self._socket = None
            if not self.stop_event.is_set():
                self.stop_event.wait(retry_delay)
                retry_delay = min(self.reconnect_max, max(retry_delay * 2, 0.01))

    def _read_stream(self, sock):
        buffer = b""
        while not self.stop_event.is_set():
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._accept_line(line)

    def _accept_line(self, line):
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        required = {
            "version", "type", "symbol", "bid", "ask", "last", "volume",
            "source_time", "received_at",
        }
        if (not isinstance(event, dict) or not required.issubset(event)
                or event["version"] != PROTOCOL_VERSION or event["type"] != "tick"):
            return
        if not isinstance(event["symbol"], str) or not event["symbol"]:
            return
        if not isinstance(event["received_at"], str):
            return
        if event["source_time"] is not None and not isinstance(event["source_time"], str):
            return
        if not all(
            isinstance(event[field], (int, float))
            and not isinstance(event[field], bool)
            and math.isfinite(event[field])
            for field in ("bid", "ask", "last", "volume")
        ):
            return
        try:
            received_at = datetime.fromisoformat(event["received_at"].replace("Z", "+00:00"))
            if received_at.tzinfo is None:
                return
            if event["source_time"] is not None:
                source_time = datetime.fromisoformat(event["source_time"].replace("Z", "+00:00"))
                if source_time.tzinfo is None:
                    return
        except ValueError:
            return
        with self._lock:
            self._ticks[event["symbol"]] = dict(event)
        if self.on_tick is not None:
            try:
                self.on_tick(dict(event))
            except Exception:
                pass


__all__ = ["TickConsumer"]
