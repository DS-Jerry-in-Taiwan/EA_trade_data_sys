#!/usr/bin/env python3
"""Run the three trade-data processes as one container lifecycle.

The supervisor is intentionally dependency-free so it can become PID 1.  A
service exiting is always unexpected: the remaining services are stopped and
the supervisor exits non-zero, allowing Docker health/restart policy to react.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO


@dataclass
class Child:
    name: str
    process: subprocess.Popen[bytes]
    log: IO[bytes]


SERVICE_SCRIPTS = (
    ("tick-service", "tick_service.py", "tick_service.log"),
    ("history-service", "history_service.py", "history_service.log"),
    ("api-gateway", "api_gateway.py", "api_gateway.log"),
)


def rotate_log(path: Path) -> bool:
    """Preserve a non-empty previous-session log as one restart backup."""
    try:
        has_content = path.stat().st_size > 0
    except FileNotFoundError:
        return False
    if not has_content:
        return False
    path.replace(path.with_name(f"{path.name}.1"))
    return True


class Supervisor:
    def __init__(self, app_root: Path, shutdown_timeout: float) -> None:
        self.app_root = app_root
        self.shutdown_timeout = shutdown_timeout
        self.children: list[Child] = []
        self.stop_signal: int | None = None

    def request_stop(self, signum: int, _frame: object) -> None:
        # Signal handlers only record intent. Process management remains in the
        # main loop, avoiding re-entrant waits while a child is being spawned.
        if self.stop_signal is None:
            self.stop_signal = signum

    def start(self) -> None:
        service_dir = self.app_root / "service"
        log_dir = service_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        try:
            for name, script, log_name in SERVICE_SCRIPTS:
                log_path = log_dir / log_name
                if rotate_log(log_path):
                    print(f">>> Rotated {log_path} to {log_path.name}.1", flush=True)
                log = log_path.open("ab", buffering=0)
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-u", str(service_dir / script)],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except BaseException:
                    log.close()
                    raise
                self.children.append(Child(name, process, log))
                print(f"  \u2713 {name} (PID: {process.pid})", flush=True)
        except BaseException:
            self.shutdown()
            raise

    def shutdown(self) -> None:
        running = [child for child in self.children if child.process.poll() is None]
        for child in running:
            try:
                os.killpg(child.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        deadline = time.monotonic() + self.shutdown_timeout
        while running and time.monotonic() < deadline:
            running = [child for child in running if child.process.poll() is None]
            if running:
                time.sleep(0.05)

        for child in running:
            print(f">>> {child.name} did not stop in time; sending SIGKILL", file=sys.stderr, flush=True)
            try:
                os.killpg(child.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        for child in self.children:
            try:
                child.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            child.log.close()

    def run(self) -> int:
        self.start()
        print(">>> All services started; supervisor is monitoring them.", flush=True)
        while self.stop_signal is None:
            for child in self.children:
                returncode = child.process.poll()
                if returncode is not None:
                    print(
                        f">>> {child.name} exited unexpectedly with status {returncode}",
                        file=sys.stderr,
                        flush=True,
                    )
                    self.shutdown()
                    return returncode if returncode != 0 else 1
            time.sleep(0.1)

        signum = self.stop_signal
        print(f">>> Received signal {signum}; stopping all services.", flush=True)
        self.shutdown()
        return 128 + signum


def main() -> int:
    app_root = Path(os.environ.get("TRADE_DATA_APP_ROOT", "/app"))
    try:
        shutdown_timeout = float(os.environ.get("SUPERVISOR_SHUTDOWN_TIMEOUT", "10"))
    except ValueError:
        print("SUPERVISOR_SHUTDOWN_TIMEOUT must be numeric", file=sys.stderr)
        return 2
    if shutdown_timeout < 0:
        print("SUPERVISOR_SHUTDOWN_TIMEOUT must be non-negative", file=sys.stderr)
        return 2

    supervisor = Supervisor(app_root, shutdown_timeout)
    signal.signal(signal.SIGTERM, supervisor.request_stop)
    signal.signal(signal.SIGINT, supervisor.request_stop)
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
