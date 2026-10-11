"""Data health additive wire contract; not Execution trading admission."""

from pathlib import Path

import yaml


def test_openapi_documents_heartbeat_receipt_and_quote_separately():
    document = yaml.safe_load((Path(__file__).parents[2] / "openapi.yaml").read_text())
    assert document["info"]["version"] == "4.1.0"
    schemas = document["components"]["schemas"]
    health = schemas["Health"]["properties"]
    assert health["fresh_symbols_basis"]["enum"] == ["received_at"]
    assert health["readiness_basis"]["enum"] == ["transport_receipt_and_account_session"]
    assert set(health["symbol_freshness"]["additionalProperties"]["properties"]) == {"receipt", "broker_quote"}
    assert schemas["ComponentStatus"]["properties"]["freshness_basis"]["enum"] == ["status_updated_at"]
    assert schemas["TimestampFreshness"]["properties"]["timestamp"]["nullable"] is True
    assert schemas["Tick"]["properties"]["source_time"]["nullable"] is True
    assert "/api/v1/health" in document["paths"]
