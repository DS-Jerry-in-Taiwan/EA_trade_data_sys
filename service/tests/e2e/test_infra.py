"""
Layer A: Infrastructure Verification (6 tests)
=================================================
Verify the container, network, file-system, and process environment.
All tests are order-independent and stateless.
"""
import os
import subprocess
import socket
import pytest


class TestInfrastructure:

    def test_container_python_version(self):
        """Python version must be 3.12+"""
        import sys
        assert sys.version_info >= (3, 12), f"Python {sys.version} is too old"

    def test_working_directory(self):
        """We must be inside /app/service/tests/e2e or a sub-path"""
        allowed = {"app", "service", "tests"}
        cwd = os.getcwd().replace("\\", "/").split("/")
        assert allowed.issubset(set(cwd)), f"Unexpected CWD: {os.getcwd()}"

    def test_required_folders(self, docker_exec):
        """Check that critical data/log/config folders exist"""
        folders = [
            "/app/service/data/ticks",
            "/app/service/data/history",
            "/app/service/logs",
            "/app/service/config",
        ]
        for f in folders:
            r = docker_exec(f"test -d {f} && echo 'OK' || echo 'MISSING'")
            assert r.stdout.strip() == "OK", f"Folder {f} is missing"

    def test_required_files(self, docker_exec):
        """Check that critical config files exist"""
        files = [
            "/app/service/config/settings.yaml",
            "/app/service/config/accounts.json",
        ]
        for f in files:
            r = docker_exec(f"test -f {f} && echo 'OK' || echo 'MISSING'")
            assert r.stdout.strip() == "OK", f"File {f} is missing"

    def test_process_tick_service(self, docker_exec):
        """tick_service must be among running processes"""
        r = docker_exec("ps auxww | grep tick_service | grep -v grep")
        assert r.stdout.strip(), "tick_service not found in ps auxww"

    def test_process_history_service(self, docker_exec):
        """history_service must be among running processes"""
        r = docker_exec("ps auxww | grep history_service | grep -v grep")
        assert r.stdout.strip(), "history_service not found in ps auxww"


class TestMT5Client:
    """Phase 6: MT5Client unit tests (3 tests)"""

    def test_mt5_client_init(self):
        """MT5Client initialises with no connection"""
        import sys
        sys.path.insert(0, '/app')
        from service.core.mt5_client import MT5Client
        c = MT5Client()
        assert c._mt5 is None, "_mt5 should be None on init"
        assert c._connector is None, "_connector should be None on init"
        assert c.mt5 is None, "mt5 property should be None on init"

    def test_mt5_client_ensure_connected(self):
        """ensure_connected returns True when connected to MT5 server"""
        import sys
        sys.path.insert(0, '/app')
        from service.core.mt5_client import MT5Client
        c = MT5Client()
        result = c.ensure_connected()
        assert result is True, f"ensure_connected should return True, got {result}"
        assert c._mt5 is not None, "_mt5 should be set after connection"

    def test_mt5_client_call(self):
        """call() correctly executes a lambda against the mt5 object"""
        import sys
        sys.path.insert(0, '/app')
        from service.core.mt5_client import MT5Client
        c = MT5Client()
        assert c.ensure_connected(), "Must be connected to test call()"
        # Call a simple read-only method
        info = c.call(lambda m: m.account_info())
        assert info is not None, "account_info() should return a result"
        assert hasattr(info, 'balance'), "account_info should have 'balance' attribute"
        assert isinstance(info.balance, (int, float)), "balance should be numeric"
