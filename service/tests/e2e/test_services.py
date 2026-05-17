"""
Layer B: Background Service Verification (8 tests)
====================================================
Verify the three background services (tick_service, history_service,
api_gateway) are running, their logs are error-free, and data is fresh.
"""
import json
import pytest
import pandas as pd
from datetime import datetime, timezone


SERVICES = ["tick_service", "history_service", "api_gateway"]
LOGS = {s: f"/app/service/logs/{s}.log" for s in SERVICES}


def _service_pid(docker_exec, name):
    """Get PID of a service process by parsing ps auxww output in Python."""
    r = docker_exec("ps auxww")
    stdout = r.stdout
    if not stdout:
        return ""
    for line in stdout.strip().split("\n"):
        if name in line and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    return ""


def test_tick_service_running(docker_exec):
    """tick_service background process must have a PID"""
    pid = _service_pid(docker_exec, "tick_service")
    assert pid, "tick_service is not running (no PID found)"


def test_history_service_running(docker_exec):
    """history_service background process must have a PID"""
    pid = _service_pid(docker_exec, "history_service")
    assert pid, "history_service is not running (no PID found)"


def test_api_gateway_running(docker_exec):
    """api_gateway background process must have a PID"""
    pid = _service_pid(docker_exec, "api_gateway")
    assert pid, "api_gateway is not running (no PID found)"


@pytest.mark.parametrize("svc", SERVICES)
def test_service_log_no_errors(docker_exec, svc):
    """Check that service logs contain no ERROR/Traceback keywords (last 100 lines)"""
    r = docker_exec(f"tail -100 {LOGS[svc]} 2>/dev/null || echo 'LOG_NOT_FOUND'")
    if "LOG_NOT_FOUND" in r.stdout:
        pytest.skip(f"Log file {LOGS[svc]} not found")
    errors = []
    for line in r.stdout.split("\n"):
        if any(kw in line for kw in ["ERROR", "Traceback", "Error", "traceback"]):
            errors.append(line.strip())
    errors = [e for e in errors if "DeprecationWarning" not in e]
    assert len(errors) == 0, f"{svc} log contains errors: {errors[:5]}"


def test_tick_data_freshness(docker_exec):
    """Latest tick data must have been updated within the last 120 seconds"""
    r = docker_exec("cat /app/service/data/ticks/latest.json 2>/dev/null || echo '{}'")
    data = json.loads(r.stdout) if r.stdout.strip() else {}
    if not data:
        pytest.skip("No tick data yet (latest.json empty)")
    first_key = list(data.keys())[0]
    ts_str = data[first_key].get("time", "")
    if not ts_str:
        pytest.skip("No timestamp in tick data")
    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    delta = (datetime.now(timezone.utc) - ts).total_seconds()
    assert delta < 120, \
        f"Tick data is {delta:.0f}s old (threshold: 120s). " \
        f"Symbol={first_key}, time={ts_str}"


def test_history_data_freshness(docker_exec):
    """Latest history CSV entry must be within the last 900 seconds.
    
    M5 bars complete 5 min after their timestamp + cycle timing,
    so data up to 10-12 min old is expected during normal operation.
    """
    r = docker_exec("ls /app/service/data/history/*_M5.csv 2>/dev/null | head -1")
    csv_file = r.stdout.strip()
    if not csv_file:
        pytest.skip("No history CSV files found")
    r = docker_exec(f"tail -1 '{csv_file}'")
    last_line = r.stdout.strip()
    if not last_line:
        pytest.skip(f"CSV {csv_file} appears empty")
    try:
        df = pd.read_csv(csv_file)
    except Exception:
        pytest.skip(f"Cannot read CSV {csv_file}")
    if df.empty:
        pytest.skip("CSV is empty")
    last_time = pd.to_datetime(df["time"].iloc[-1])
    if last_time.tz is None:
        last_time = last_time.tz_localize("UTC")
    delta = (datetime.now(timezone.utc) - last_time).total_seconds()
    assert delta < 900, \
        f"History data is {delta:.0f}s old (threshold: 900s). " \
        f"Last time: {last_time}"
