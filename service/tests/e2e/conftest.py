"""
Shared test fixtures for E2E tests.

All fixtures detect whether they run inside the python-runner container
(no docker CLI available) or from the host, adapting subprocess calls accordingly.
"""

import os
import shutil
import subprocess
import pytest
import requests

_IN_CONTAINER = shutil.which("docker") is None


@pytest.fixture(scope="session")
def api_base() -> str:
    """Return the base URL for the API Gateway."""
    host = "localhost" if _IN_CONTAINER else "localhost"
    return f"http://{host}:8090/api/v1"


@pytest.fixture
def api(api_base):
    """Shortcut to make GET requests to the API Gateway."""
    def _get(path):
        return requests.get(f"{api_base}{path}", timeout=10)
    return _get


@pytest.fixture(scope="session")
def settings() -> dict:
    """Read key fields from /app/service/config/settings.yaml (inside container)."""
    import yaml
    try:
        with open("/app/service/config/settings.yaml") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        cfg = {}
    return cfg


@pytest.fixture
def docker_exec():
    """Run a bash command inside the python-runner container.

    Returns a function: docker_exec(cmd) -> subprocess.CompletedProcess
    """
    def _exec(cmd):
        if _IN_CONTAINER:
            return subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=15
            )
        else:
            return subprocess.run(
                ["docker", "exec", "python-runner", "bash", "-c", cmd],
                capture_output=True, text=True, timeout=15
            )
    return _exec


@pytest.fixture(scope="session")
def csv_paths(settings) -> dict:
    """Discover available history CSV files."""
    import glob
    base = "/app/service/data/history"
    paths = glob.glob(f"{base}/*.csv")
    if not paths:
        return {}
    result = {}
    for p in sorted(paths):
        name = os.path.basename(p).replace(".csv", "")
        parts = name.split("_")
        sym = parts[0]
        tf = parts[1] if len(parts) > 1 else "unknown"
        result.setdefault(sym, []).append(p)
    return result
