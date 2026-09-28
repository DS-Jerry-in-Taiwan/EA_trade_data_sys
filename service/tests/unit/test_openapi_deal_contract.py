from pathlib import Path

import yaml


OPENAPI_PATH = Path(__file__).parents[2] / "openapi.yaml"


def test_openapi_parses_and_defines_historical_deal_response_contract():
    spec = yaml.safe_load(OPENAPI_PATH.read_text())

    assert spec["openapi"] == "3.0.3"
    schemas = spec["components"]["schemas"]
    deal = schemas["TradeDealV1"]
    summary = schemas["DealSummary"]
    responses = spec["paths"]["/api/v1/history/deals"]["get"]["responses"]

    assert set(("deal", "ticket", "deal_id", "order", "order_id")) <= set(
        deal["required"]
    )
    assert deal["properties"]["deal"]["deprecated"] is True
    assert "identical to `deal_id`" in deal["properties"]["ticket"]["description"]
    assert "identical to `ticket`" in deal["properties"]["deal_id"]["description"]
    assert "identical to `order`" in deal["properties"]["order_id"]["description"]

    variants = responses["200"]["content"]["application/json"]["schema"]["oneOf"]
    assert variants[0]["items"]["$ref"] == "#/components/schemas/TradeDealV1"
    assert variants[1]["properties"]["data"]["items"]["$ref"] == (
        "#/components/schemas/TradeDealV1"
    )
    assert variants[1]["properties"]["summary"]["$ref"] == (
        "#/components/schemas/DealSummary"
    )
    assert set(summary["required"]) == {"profit", "commission", "swap", "count"}
    assert responses["502"]["content"]["application/json"]["schema"]["properties"][
        "code"
    ]["enum"] == ["mt5_deal_mapping_error"]
