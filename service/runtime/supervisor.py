"""Own the lifecycle of the three trade-data application processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

try:
    from service.config import load_runtime_settings
except ModuleNotFoundError as exc:
    # ``python service/runtime/supervisor.py`` is retained for direct
    # diagnostic execution; its initial sys.path contains service/runtime,
    # rather than the repository root used by the package import.
    if exc.name not in {"service", "service.config"}:
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from service.config import load_runtime_settings


@dataclass
class Child:
    name: str
    process: subprocess.Popen[bytes]
    log: IO[bytes]


SERVICE_ENTRYPOINTS = (
    ("tick-worker", "service.entrypoints.tick_worker", "tick_service.log"),
    ("history-worker", "service.entrypoints.history_worker", "history_service.log"),
    ("api-gateway", "service.entrypoints.api_gateway", "api_gateway.log"),
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
        if self.stop_signal is None:
            self.stop_signal = signum

    def start(self) -> None:
        log_dir = self.app_root / "service" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            for name, module, log_name in SERVICE_ENTRYPOINTS:
                log_path = log_dir / log_name
                if rotate_log(log_path):
                    print(f">>> Rotated {log_path} to {log_path.name}.1", flush=True)
                log = log_path.open("ab", buffering=0)
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-u", "-m", module],
                        cwd=self.app_root,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except BaseException:
                    log.close()
                    raise
                self.children.append(Child(name, process, log))
                print(f"  ✓ {name} (PID: {process.pid})", flush=True)
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
                    print(f">>> {child.name} exited unexpectedly with status {returncode}", file=sys.stderr, flush=True)
                    self.shutdown()
                    return returncode if returncode != 0 else 1
            time.sleep(0.1)

        signum = self.stop_signal
        print(f">>> Received signal {signum}; stopping all services.", flush=True)
        self.shutdown()
        return 128 + signum


def main() -> int:
    try:
        settings = load_runtime_settings()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    supervisor = Supervisor(Path(settings.app_root), settings.shutdown_timeout)
    signal.signal(signal.SIGTERM, supervisor.request_stop)
    signal.signal(signal.SIGINT, supervisor.request_stop)
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
