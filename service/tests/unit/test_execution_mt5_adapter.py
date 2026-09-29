from types import SimpleNamespace

import pytest

from service.execution.errors import ExecutionError
from service.execution.mt5_adapter import MT5ExecutionAdapter


class Client:
    def __init__(self, module):
        self.module = module
        self._resolver = object()

    def call(self, callback):
        return callback(self.module)

    def resolve(self, symbol):
        return symbol


def test_account_requires_explicit_demo_mode():
    module = SimpleNamespace(
        ACCOUNT_TRADE_MODE_DEMO=0,
        account_info=lambda: SimpleNamespace(trade_mode=2, balance=1),
    )
    adapter = MT5ExecutionAdapter(Client(module))
    assert adapter.account()["mutation_eligible"] is False
    with pytest.raises(ExecutionError, match="Demo"):
        adapter.require_demo()


def test_symbol_spec_contains_risk_facts():
    info = SimpleNamespace(
        digits=2, point=0.01, volume_min=0.01, volume_max=10, volume_step=0.01,
        trade_tick_size=0.01, trade_tick_value=1, trade_contract_size=100,
        trade_stops_level=20, trade_freeze_level=10, bid=2000, ask=2001,
    )
    module = SimpleNamespace(
        symbol_info=lambda _symbol: info,
        order_calc_margin=lambda *_args: 1000,
        ORDER_TYPE_BUY=0,
    )
    result = MT5ExecutionAdapter(Client(module)).symbol("XAUUSDm")
    assert result["stops_level"] == 20
    assert result["freeze_level"] == 10
    assert result["margin_per_lot"] == 1000
    assert result["observed_at"]


@pytest.mark.parametrize("retcode,code", [
    (10014, "invalid_volume"), (10016, "invalid_stops"),
    (10018, "market_closed"), (10019, "insufficient_margin"),
])
def test_preflight_stable_error_mapping(retcode, code):
    module = SimpleNamespace(
        symbol_info=lambda _symbol: SimpleNamespace(bid=1, ask=2),
        order_check=lambda _request: SimpleNamespace(retcode=retcode),
        TRADE_ACTION_DEAL=1, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1,
        ORDER_TIME_GTC=0, ORDER_FILLING_IOC=1,
    )
    adapter = MT5ExecutionAdapter(Client(module))
    with pytest.raises(ExecutionError) as error:
        adapter.preflight({"symbol": "XAUUSDm", "side": "BUY", "volume": 0.01})
    assert error.value.code == code
    assert error.value.retcode == retcode
