import os
import importlib.util
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


SUPERVISOR = Path(__file__).parents[3] / "mt5docker" / "process_supervisor.py"
SERVICE_NAMES = ("tick_service.py", "history_service.py", "api_gateway.py")


def _load_supervisor_module():
    spec = importlib.util.spec_from_file_location("process_supervisor", SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _wait_for(path: Path, timeout: float = 5) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and (content := path.read_text().strip()):
            return content
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {path}")


def _make_services(root: Path, source: str) -> None:
    service_dir = root / "service"
    service_dir.mkdir()
    for name in SERVICE_NAMES:
        (service_dir / name).write_text(source)


def _start(root: Path, timeout: str = "1") -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(TRADE_DATA_APP_ROOT=str(root), SUPERVISOR_SHUTDOWN_TIMEOUT=timeout)
    return subprocess.Popen(
        [sys.executable, "-u", str(SUPERVISOR)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def test_nonempty_log_is_preserved_as_backup_before_new_current(tmp_path: Path) -> None:
    supervisor_module = _load_supervisor_module()
    current = tmp_path / "api_gateway.log"
    backup = tmp_path / "api_gateway.log.1"
    current.write_bytes(b"current-log")
    backup.write_bytes(b"older-backup")

    assert supervisor_module.rotate_log(current) is True
    assert not current.exists()
    assert backup.read_bytes() == b"current-log"

    # Opening the normal current path after rotation starts a new session log.
    with current.open("ab", buffering=0) as log:
        log.write(b"new-log")
    assert current.read_bytes() == b"new-log"


def test_empty_current_log_is_not_rotated(tmp_path: Path) -> None:
    supervisor_module = _load_supervisor_module()
    current = tmp_path / "tick_service.log"
    current.touch()

    assert supervisor_module.rotate_log(current) is False
    assert current.exists()
    assert not (tmp_path / "tick_service.log.1").exists()


def test_signal_is_forwarded_and_all_three_children_stop(tmp_path: Path) -> None:
    _make_services(
        tmp_path,
        """import os, signal, time
from pathlib import Path
name = Path(__file__).name
Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
def stop(signum, frame):
    Path(__file__).with_suffix('.stopped').write_text(str(signum))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
while True: time.sleep(.05)
""",
    )
    supervisor = _start(tmp_path)
    for name in SERVICE_NAMES:
        _wait_for(tmp_path / "service" / name.replace(".py", ".pid"))

    supervisor.send_signal(signal.SIGTERM)
    assert supervisor.wait(timeout=5) == 128 + signal.SIGTERM
    for name in SERVICE_NAMES:
        assert _wait_for(tmp_path / "service" / name.replace(".py", ".stopped")) == str(signal.SIGTERM)


def test_child_exit_stops_siblings_and_fails_supervisor(tmp_path: Path) -> None:
    _make_services(
        tmp_path,
        """import os, signal, time
from pathlib import Path
name = Path(__file__).name
Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
def stop(signum, frame):
    Path(__file__).with_suffix('.stopped').write_text(str(signum))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
if name == 'tick_service.py':
    time.sleep(.3)
    raise SystemExit(7)
while True: time.sleep(.05)
""",
    )
    supervisor = _start(tmp_path)

    assert supervisor.wait(timeout=5) == 7
    assert "tick-service exited unexpectedly with status 7" in supervisor.stderr.read()
    for name in ("history_service.py", "api_gateway.py"):
        assert _wait_for(tmp_path / "service" / name.replace(".py", ".stopped")) == str(signal.SIGTERM)


def test_shutdown_is_bounded_and_escalates_to_kill(tmp_path: Path) -> None:
    _make_services(
        tmp_path,
        """import os, signal, time
from pathlib import Path
Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True: time.sleep(.05)
""",
    )
    supervisor = _start(tmp_path, timeout="0.1")
    for name in SERVICE_NAMES:
        _wait_for(tmp_path / "service" / name.replace(".py", ".pid"))

    started = time.monotonic()
    supervisor.send_signal(signal.SIGTERM)
    assert supervisor.wait(timeout=3) == 128 + signal.SIGTERM
    assert time.monotonic() - started < 2
    assert "sending SIGKILL" in supervisor.stderr.read()
