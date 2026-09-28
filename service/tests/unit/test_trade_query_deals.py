import sys
from types import SimpleNamespace
from types import ModuleType

import pytest

try:
    import pymt5linux  # noqa: F401
except ModuleNotFoundError:
    pymt5linux_stub = ModuleType("pymt5linux")
    pymt5linux_stub.MetaTrader5 = object
    sys.modules["pymt5linux"] = pymt5linux_stub

from service.trade_query.account_service import AccountService
from service.trade_query.errors import DealMappingError


class FakeMT5Client:
    def __init__(self, deals):
        self.deals = deals

    def ensure_connected(self):
        return True

    def call(self, operation):
        module = SimpleNamespace(history_deals_get=lambda _from, _to: self.deals)
        return operation(module)


def raw_deal(ticket, order, timestamp, **overrides):
    values = {
        "ticket": ticket,
        "order": order,
        "position_id": 303,
        "time": timestamp,
        "type": 0,
        "entry": 1,
        "symbol": "XAUUSDm",
        "volume": 0.1,
        "price": 2500.5,
        "profit": 12.25,
        "commission": -0.5,
        "swap": -0.1,
        "comment": "closed",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_service(tmp_path, deals):
    config = tmp_path / "settings.yaml"
    config.write_text("{}\n")
    return AccountService(config_path=config, mt5_client=FakeMT5Client(deals))


def test_get_deals_maps_before_sorting_and_presents_v1_contract(tmp_path):
    older = raw_deal(101, 201, 1_798_459_200)
    newer = raw_deal(102, 202, 1_798_545_600, profit=1.005)
    service = make_service(tmp_path, [older, newer])

    result = service.get_deals(limit=None, include_summary=True)

    assert [deal["deal"] for deal in result["data"]] == [102, 101]
    assert [deal["ticket"] for deal in result["data"]] == [102, 101]
    assert [deal["deal_id"] for deal in result["data"]] == [102, 101]
    assert [deal["order"] for deal in result["data"]] == [202, 201]
    assert [deal["order_id"] for deal in result["data"]] == [202, 201]
    assert result["data"][0]["time"] == "2026-12-29T12:00:00+00:00"
    assert result["summary"] == {
        "profit": 13.25,
        "commission": -1.0,
        "swap": -0.2,
        "count": 2,
    }


@pytest.mark.parametrize(
    ("include_summary", "expected"),
    (
        (False, []),
        (
            True,
            {
                "data": [],
                "summary": {
                    "profit": 0,
                    "commission": 0,
                    "swap": 0,
                    "count": 0,
                },
            },
        ),
    ),
)
def test_get_deals_preserves_empty_result_behavior(tmp_path, include_summary, expected):
    service = make_service(tmp_path, [])

    assert service.get_deals(include_summary=include_summary) == expected


def test_deal_mapping_error_is_returned_as_502(monkeypatch):
    flask = pytest.importorskip("flask")
    from service.gateway.routes.trade_query import create_blueprint

    class FailingService:
        def get_deals(self, **_kwargs):
            raise DealMappingError("source details must not leak")

    monkeypatch.setenv("TEST_READONLY_API_KEY", "secret")
    config = {
        "api_gateway": {"readonly_api_key_env": "TEST_READONLY_API_KEY"},
        "trade_query": {"default_days": 7, "max_days": 90},
    }
    app = flask.Flask(__name__)
    app.register_blueprint(create_blueprint(FailingService(), lambda: config))

    response = app.test_client().get(
        "/api/v1/history/deals?days=7", headers={"X-API-Key": "secret"}
    )

    assert response.status_code == 502
    assert response.get_json() == {
        "error": "upstream deal contract invalid",
        "code": "mt5_deal_mapping_error",
    }
    assert "source details" not in response.get_data(as_text=True)
