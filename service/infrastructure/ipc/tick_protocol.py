"""Versioned container-local IPC protocol for normalized Tick events."""

import json
import os
import socket
import stat
import threading
import queue


PROTOCOL_VERSION = 1
DEFAULT_SOCKET_PATH = "/run/trade-data/ticks.sock"


class _ClientChannel:
    """Bounded asynchronous writer so consumers never block Tick polling."""

    def __init__(self, client, on_close, queue_size):
        self.client = client
        self._on_close = on_close
        self._queue = queue.Queue(maxsize=queue_size)
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._write_loop, name="tick-ipc-writer", daemon=True
        )

    def start(self):
        self._thread.start()

    def enqueue(self, payload):
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except queue.Full:
            return False

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.client.close()
        except OSError:
            pass

    def _write_loop(self):
        try:
            while not self._closed.is_set():
                try:
                    payload = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                self.client.sendall(payload)
        except OSError:
            pass
        finally:
            self._on_close(self)


class TickPublisher:
    """Publish snapshots and live events as versioned newline-delimited JSON."""

    def __init__(self, socket_path=DEFAULT_SOCKET_PATH, socket_mode=0o660,
                 client_queue_size=256):
        self.socket_path = socket_path
        self.socket_mode = socket_mode
        self.client_queue_size = client_queue_size
        self._server = None
        self._clients = set()
        self._latest = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._accept_thread = None
        self._socket_identity = None

    @property
    def latest(self):
        with self._lock:
            return dict(self._latest)

    def start(self):
        if self._server is not None:
            return
        parent = os.path.dirname(self.socket_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._remove_stale_socket()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(self.socket_path)
            self._socket_identity = (os.stat(self.socket_path).st_dev,
                                     os.stat(self.socket_path).st_ino)
            os.chmod(self.socket_path, self.socket_mode)
            server.listen()
            server.settimeout(0.2)
        except Exception:
            server.close()
            self._unlink_owned_socket()
            raise
        self._server = server
        self._stop_event.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="tick-ipc-accept", daemon=True
        )
        self._accept_thread.start()

    def publish(self, event):
        payload = self._encode_event(event)
        symbol = event["symbol"]
        with self._lock:
            self._latest[symbol] = dict(event)
            failed = []
            for channel in self._clients:
                if not channel.enqueue(payload):
                    failed.append(channel)
            for channel in failed:
                self._drop_client(channel)

    def stop(self):
        self._stop_event.set()
        server, self._server = self._server, None
        if server is not None:
            server.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1)
            self._accept_thread = None
        with self._lock:
            for channel in list(self._clients):
                self._drop_client(channel)
        self._unlink_owned_socket()

    def _accept_loop(self):
        while not self._stop_event.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except (AttributeError, OSError):
                if self._stop_event.is_set():
                    break
                continue
            # Serialize snapshot registration with publish(). This guarantees that
            # snapshot messages precede all live messages on a new connection.
            with self._lock:
                channel = _ClientChannel(
                    client, self._drop_client, self.client_queue_size
                )
                snapshots_fit = all(
                    channel.enqueue(self._encode_event(self._latest[symbol]))
                    for symbol in sorted(self._latest)
                )
                if snapshots_fit:
                    self._clients.add(channel)
                    channel.start()
                else:
                    channel.close()

    @staticmethod
    def _encode_event(event):
        required = {
            "version", "type", "symbol", "bid", "ask", "last", "volume",
            "source_time", "received_at",
        }
        missing = required.difference(event)
        if missing:
            raise ValueError(f"Tick event missing fields: {sorted(missing)}")
        if event["version"] != PROTOCOL_VERSION or event["type"] != "tick":
            raise ValueError("Unsupported Tick event protocol")
        return (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")

    def _remove_stale_socket(self):
        try:
            mode = os.stat(self.socket_path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"IPC path exists and is not a socket: {self.socket_path}")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            if probe.connect_ex(self.socket_path) == 0:
                raise RuntimeError(f"Tick IPC socket is already active: {self.socket_path}")
        finally:
            probe.close()
        os.unlink(self.socket_path)

    def _drop_client(self, channel):
        with self._lock:
            self._clients.discard(channel)
        channel.close()

    def _unlink_owned_socket(self):
        try:
            current = os.stat(self.socket_path)
            identity = (current.st_dev, current.st_ino)
            if stat.S_ISSOCK(current.st_mode) and identity == self._socket_identity:
                os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        finally:
            self._socket_identity = None
