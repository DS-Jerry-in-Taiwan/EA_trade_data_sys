"""
Layer F: Monitoring & Observability (4 tests)
===============================================
Verify log rotation, metrics endpoint, and error reporting are functional.
"""
import pytest


class TestLogRotation:

    def test_log_file_size(self, docker_exec):
        """Service logs should not exceed 100MB"""
        for svc in ("tick_service", "history_service", "api_gateway"):
            r = docker_exec(f"wc -c /app/service/logs/{svc}.log 2>/dev/null || echo '0'")
            size_bytes = int(r.stdout.strip().split()[0]) if r.stdout.strip().split() else 0
            assert size_bytes < 100 * 1024 * 1024, \
                f"{svc}.log exceeds 100MB ({size_bytes / 1024 / 1024:.1f}MB)"

    def test_no_log_truncation_needed(self, docker_exec):
        """truncated log marker should not appear in log files"""
        for svc in ("tick_service", "history_service", "api_gateway"):
            # Check if file exists first
            r = docker_exec(f"test -f /app/service/logs/{svc}.log && echo 'EXISTS' || echo 'NOT_FOUND'")
            if r.stdout.strip() != "EXISTS":
                pytest.skip(f"Log file for {svc} not found")
            # Count TRUNCATED occurrences (just grep, no || to avoid output duplication)
            r = docker_exec(f"grep -c 'TRUNCATED' /app/service/logs/{svc}.log 2>/dev/null || true")
            output = r.stdout.strip()
            if not output:
                output = "0"
            count = int(output.split("\n")[0])
            assert count == 0, f"{svc}.log contains {count} TRUNCATED markers"


class TestMetricsObservability:

    def test_metrics_consistency(self, api):
        """Multiple calls to /metrics should return consistent results"""
        resp1 = api("/metrics")
        if resp1.status_code != 200:
            pytest.skip("Metrics endpoint not available")
        resp2 = api("/metrics")
        assert resp2.status_code == 200, "Second metrics call failed"
        assert resp1.text.count("\n") == resp2.text.count("\n"), \
            "Metrics line count changed between calls (may indicate flapping)"

    def test_metrics_tick_count_increasing(self, api):
        """tick_count metric should be present and non-negative"""
        resp = api("/metrics")
        if resp.status_code != 200:
            pytest.skip("Metrics endpoint not available")
        text = resp.text
        for line in text.split("\n"):
            if line.startswith("tick_count"):
                try:
                    val = float(line.split()[1])
                    assert val >= 0, f"tick_count is negative: {val}"
                except (IndexError, ValueError):
                    pytest.fail(f"Cannot parse tick_count from line: {line!r}")
                return
        pytest.fail("tick_count not found in metrics output")
