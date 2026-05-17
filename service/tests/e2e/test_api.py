"""
Layer C: REST API Verification (19 tests)
===========================================
Test every endpoint exposed by the API Gateway.
Skip tests gracefully when MT5 connectivity is missing.
"""
import pytest


class TestHealthEndpoint:
    """C1: /health"""

    def test_health_status(self, api):
        resp = api("/health")
        assert resp.status_code in (200, 502), f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert "status" in data, "Health response missing 'status'"

    def test_health_tick_service(self, api):
        """Health response should indicate tick_service status"""
        resp = api("/health")
        if resp.status_code != 200:
            pytest.skip("Health endpoint not available")
        data = resp.json()
        assert "tick_service" in data, "Health response missing 'tick_service'"
        assert data["tick_service"] == "running"


class TestSymbolsEndpoint:
    """C2: /symbols"""

    def test_symbols_status_ok(self, api):
        resp = api("/symbols")
        assert resp.status_code in (200, 502), f"Unexpected status {resp.status_code}"

    def test_symbols_list(self, api):
        resp = api("/symbols")
        if resp.status_code != 200:
            pytest.skip("Symbols endpoint not available")
        data = resp.json()
        assert "symbols" in data, "Symbols response missing 'symbols' key"
        sym_list = data["symbols"]
        # Can be a list of strings or list of dicts with 'name' key
        if sym_list and isinstance(sym_list[0], dict):
            sym_names = {s["name"] for s in sym_list}
        else:
            sym_names = set(sym_list)
        known_symbols = {"XAUUSDm", "BTCUSDm", "EURUSDm", "GBPUSDm"}
        assert known_symbols.intersection(sym_names), \
            f"No known symbols found in {sym_names}"


class TestTicksEndpoint:
    """C3: /ticks/<symbol>"""

    @pytest.mark.parametrize("symbol", ["XAUUSDm", "BTCUSDm", "EURUSDm", "GBPUSDm"])
    def test_ticks_status(self, api, symbol):
        resp = api(f"/ticks/{symbol}")
        assert resp.status_code in (200, 404, 502), f"Unexpected status {resp.status_code} for {symbol}"

    @pytest.mark.parametrize("symbol", ["XAUUSDm", "BTCUSDm", "EURUSDm", "GBPUSDm"])
    def test_ticks_data_shape(self, api, symbol):
        resp = api(f"/ticks/{symbol}")
        if resp.status_code != 200:
            pytest.skip(f"Ticks endpoint not available for {symbol} (HTTP {resp.status_code})")
        data = resp.json()
        if not data:
            pytest.skip(f"No tick data for {symbol}")
        record = data[0] if isinstance(data, list) else data
        for field in ("time", "bid", "ask"):
            assert field in record, f"Tick record missing '{field}' for {symbol}"


class TestRatesEndpoint:
    """C4: /rates/<symbol>"""

    @pytest.mark.parametrize("symbol", ["XAUUSDm", "BTCUSDm", "EURUSDm", "GBPUSDm"])
    def test_rates_status(self, api, symbol):
        resp = api(f"/rates/{symbol}")
        assert resp.status_code in (200, 404, 502), f"Unexpected status {resp.status_code} for {symbol}"

    @pytest.mark.parametrize("symbol", ["XAUUSDm", "BTCUSDm", "EURUSDm", "GBPUSDm"])
    def test_rates_data_shape(self, api, symbol):
        resp = api(f"/rates/{symbol}")
        if resp.status_code != 200:
            pytest.skip(f"Rates endpoint not available for {symbol} (HTTP {resp.status_code})")
        data = resp.json()
        if not data:
            pytest.skip(f"No rate data for {symbol}")
        # API wraps data in a dict with 'data' key
        records = data.get("data", data)
        if not records:
            pytest.skip(f"Empty rate data for {symbol}")
        record = records[0] if isinstance(records, list) else data
        for field in ("time", "open", "high", "low", "close"):
            assert field in record, f"Rate record missing '{field}' for {symbol}"


class TestMetricsEndpoint:
    """C5: /metrics"""

    def test_metrics_status(self, api):
        resp = api("/metrics")
        assert resp.status_code in (200, 404, 502), f"Unexpected status {resp.status_code}"

    def test_metrics_content_type(self, api):
        resp = api("/metrics")
        if resp.status_code != 200:
            pytest.skip("Metrics endpoint not available")
        assert "text/plain" in resp.headers.get("content-type", ""), \
            "Metrics should be plain text"

    def test_metrics_values(self, api):
        resp = api("/metrics")
        if resp.status_code != 200:
            pytest.skip("Metrics endpoint not available")
        text = resp.text
        for key in ("tick_count", "symbol_count", "uptime_seconds"):
            assert key in text, f"Metrics missing '{key}'"


class TestAccountEndpoint:
    """C6: /account/balance"""

    def test_account_balance_status(self, api):
        resp = api("/account/balance")
        assert resp.status_code in (200, 404, 503), \
            f"Unexpected status {resp.status_code}"

    def test_account_balance_value(self, api):
        resp = api("/account/balance")
        if resp.status_code != 200:
            pytest.skip("Account balance not available")
        data = resp.json()
        assert "balance" in data, "Balance response missing 'balance'"
        assert isinstance(data["balance"], (int, float)), \
            f"Balance should be numeric, got {type(data['balance'])}"


class TestChartEndpoint:
    """C7: /chart/<symbol>"""

    @pytest.mark.parametrize("symbol", ["XAUUSDm", "BTCUSDm"])
    def test_chart_status(self, api, symbol):
        resp = api(f"/chart/{symbol}")
        assert resp.status_code in (200, 404, 503), \
            f"Unexpected status {resp.status_code} for {symbol}"

    @pytest.mark.parametrize("symbol", ["XAUUSDm", "BTCUSDm"])
    def test_chart_data_shape(self, api, symbol):
        resp = api(f"/chart/{symbol}")
        if resp.status_code != 200:
            pytest.skip(f"Chart endpoint not available for {symbol}")
        data = resp.json()
        if not data:
            pytest.skip(f"No chart data for {symbol}")
        record = data[0] if isinstance(data, list) else data
        for field in ("time", "open", "high", "low", "close"):
            assert field in record, f"Chart record missing '{field}' for {symbol}"


class TestOpenApiSpec:
    """C8: /openapi.yaml — OpenAPI specification endpoint"""

    def test_openapi_status_ok(self, api):
        resp = api("/openapi.yaml")
        assert resp.status_code == 200, \
            f"Expected 200, got {resp.status_code}"

    def test_openapi_content_type(self, api):
        resp = api("/openapi.yaml")
        if resp.status_code != 200:
            pytest.skip("OpenAPI spec endpoint not available")
        ct = resp.headers.get("content-type", "")
        assert "text/yaml" in ct or "application/x-yaml" in ct or "text/plain" in ct, \
            f"Expected YAML content type, got '{ct}'"

    def test_openapi_contains_info(self, api):
        """Verify the YAML content is a valid OpenAPI spec."""
        resp = api("/openapi.yaml")
        if resp.status_code != 200:
            pytest.skip("OpenAPI spec endpoint not available")
        text = resp.text
        assert "openapi:" in text, "Response does not contain 'openapi:'"
        assert "info:" in text, "Response does not contain 'info:'"
        assert "paths:" in text, "Response does not contain 'paths:'"


class TestRatesQueryEndpoint:
    """C9: /rates/{symbol}/query — Direct MT5 historical data query"""

    def test_query_missing_params(self, api):
        """Missing start_time/end_time should return 400"""
        resp = api("/rates/XAUUSDm/query")
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"

    def test_query_invalid_date(self, api):
        """Invalid date format should return 400"""
        resp = api("/rates/XAUUSDm/query?start_time=abc&end_time=def")
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"

    def test_query_success(self, api):
        """Valid query should return 200 with data structure"""
        resp = api("/rates/XAUUSDm/query?timeframe=M5&start_time=2025-01-01&end_time=2025-01-07")
        if resp.status_code == 503:
            pytest.skip("MT5 not connected")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "symbol" in data
        assert "timeframe" in data
        assert "data" in data
        assert "count" in data
        assert isinstance(data["count"], int)

    def test_query_source_field(self, api):
        """Response should contain source: 'mt5'"""
        resp = api("/rates/XAUUSDm/query?timeframe=M5&start_time=2025-01-01&end_time=2025-01-07")
        if resp.status_code != 200:
            pytest.skip("MT5 query not available")
        data = resp.json()
        assert data.get("source") == "mt5", f"Expected source='mt5', got '{data.get('source')}'"
