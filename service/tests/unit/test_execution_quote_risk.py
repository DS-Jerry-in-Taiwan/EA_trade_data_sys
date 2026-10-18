"""No network, login, order_check or mutation; broker facts are fake."""
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from service.execution.mt5_adapter import MT5ExecutionAdapter
from service.execution.errors import ExecutionError
from service.tests.unit.test_execution_mt5_adapter import Client


def adapter(age=0, loss=2):
    info = SimpleNamespace(trade_tick_size=.01, trade_tick_value_loss=loss,
        point=.001, volume_min=.01, volume_step=.01, trade_stops_level=10,
        trade_freeze_level=5)
    mt5 = SimpleNamespace(symbol_info=lambda _: info,
        symbol_info_tick=lambda _: SimpleNamespace(bid=100, ask=101,
            time_msc=int((datetime.now(timezone.utc).timestamp()-age)*1000)),
        account_info=lambda: SimpleNamespace(currency="USD"),
        order_calc_profit=lambda kind, *args: -3 if kind == 0 else -4)
    client = Client(mt5)
    client.resolve = lambda _: "BROKER.synthetic"
    result = MT5ExecutionAdapter(client)
    result._fresh_mutation_status = lambda _: {"ready": True,"generation": 7,
        "fingerprint": {"id": "synthetic", "account_mode": "DEMO"}}
    return result, mt5


def test_loss_side_currency_and_deviation():
    a, _ = adapter()
    data = a.quote_risk("BTC", risk=True)
    assert data["broker_symbol"] == "BROKER.synthetic"
    assert data["loss_tick_value"] == "4"
    assert data["tick_value_loss"] == "2"
    assert data["tick_value_currency"] == data["account_currency"] == "USD"
    assert data["entry_deviation_points"] == data["close_deviation_points"] == 20
    assert data["deviation_price"] == "0.020"
    assert data["account_session"]["generation"] == 7
    assert data["risk_ready"]


@pytest.mark.parametrize("age", [60, -10])
def test_stale_or_future_quote_not_risk_ready(age):
    a, _ = adapter(age)
    assert not a.quote_risk("BTC", risk=True)["risk_ready"]


@pytest.mark.parametrize("loss", [None, 0, -1, float("nan")])
def test_general_tick_value_never_replaces_missing_loss(loss):
    a, _ = adapter(loss=loss)
    with pytest.raises(ExecutionError):
        a.quote_risk("BTC", risk=True)


@pytest.mark.parametrize("profit", [None, 0, 3, float("inf"), True])
def test_invalid_loss_calculation_fails_closed(profit):
    a, mt5 = adapter()
    mt5.order_calc_profit = lambda *args: profit
    with pytest.raises(ExecutionError) as error:
        a.quote_risk("BTC", risk=True)
    assert error.value.code == "risk_facts_unavailable"


def test_session_change_rejects_mixed_observation():
    a, _ = adapter()
    count = iter([7, 8])
    a._fresh_mutation_status = lambda _: {"ready":True,"generation":next(count),
        "fingerprint":{"id":"synthetic","account_mode":"DEMO"}}
    with pytest.raises(ExecutionError) as error:
        a.quote_risk("BTC")
    assert error.value.code == "account_session_transition"


@pytest.mark.parametrize("suffix", ["quote", "risk"])
def test_authenticated_wire_contract_and_boot_provenance(tmp_path, monkeypatch, suffix):
    from service.execution.app import ExecutionContext, create_execution_app
    from service.execution.idempotency import IdempotencyStore
    a, _ = adapter()
    monkeypatch.setenv("READONLY_API_KEY", "synthetic-only")
    context = ExecutionContext(a, IdempotencyStore(tmp_path / "fake.sqlite"), session_epoch="fake-epoch")
    client = create_execution_app(context).test_client()
    path = "/api/v1/symbols/BTC/"+suffix
    assert client.get(path).status_code == 401
    response = client.get(path, headers={"X-API-Key":"synthetic-only"})
    assert response.status_code == 200
    assert response.json["schema_version"] == 1
    assert response.json["data"]["account_session"]["epoch"] == "fake-epoch"
    assert response.json["data"]["symbol"] == "BTC"


def test_openapi_quote_risk_required_facts():
    from pathlib import Path
    import yaml
    spec = yaml.safe_load((Path(__file__).parents[3]/"service/execution_openapi.yaml").read_text())
    assert spec["info"]["version"] == "1.2.0"
    assert {"/symbols/{symbol}/quote","/symbols/{symbol}/risk"} <= set(spec["paths"])
    required = spec["components"]["schemas"]["RiskEnvelope"]["allOf"][1]["properties"]["data"]["required"]
    assert {"loss_tick_value","account_currency","close_deviation_points","risk_ready"} <= set(required)
