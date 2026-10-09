from types import SimpleNamespace as NS

import pytest

from service.execution.errors import ExecutionError
from service.execution.idempotency import correlation_token
from service.execution.mt5_adapter import MT5ExecutionAdapter


class Client:
    def __init__(self, module):
        self.module = module
        self._resolver = object()

    def call(self, callback):
        return callback(self.module)

    def resolve(self, symbol):
        return symbol


def book():
    token = correlation_token("test-client")
    position = NS(ticket=9, symbol="TEST", volume=.1, type=0)
    order = NS(ticket=7, symbol="TEST", volume_initial=.1, comment=token)
    deal = NS(order=7, position_id=9, symbol="TEST", entry=0, type=0, volume=.1, comment=token)
    module = NS(
        positions_get=lambda **kwargs: [position],
        orders_get=lambda **kwargs: [],
        history_orders_get=lambda **kwargs: [order],
        history_deals_get=lambda **kwargs: [deal],
    )
    scope = dict(order_id="7", position_id="9", symbol="TEST", volume="0.1", client_order_id="test-client")
    return module, position, deal, scope


def test_scoped_exposure_proves_originating_deals():
    module, _, _, scope = book()
    adapter = MT5ExecutionAdapter(Client(module))
    assert adapter.exposure_scope("test-client", "7", "TEST", "0.1") == scope


@pytest.mark.parametrize("field,value", [
    ("order", 8), ("entry", 1), ("symbol", "OTHER"),
    ("comment", "unrelated"), ("volume", .2), ("position_id", 10), ("type", 1),
])
def test_shared_or_mismatched_position_is_rejected(field, value):
    module, _, deal, scope = book()
    setattr(deal, field, value)
    with pytest.raises(ExecutionError, match="ownership"):
        MT5ExecutionAdapter(Client(module))._validate_exposure(module, scope, operation="close")


def test_added_netting_volume_fails_final_locked_check_before_send():
    module, position, _, scope = book()
    sends = []
    module.order_send = lambda request: sends.append(request)
    adapter = MT5ExecutionAdapter(Client(module))
    adapter._validate_exposure(module, scope, operation="close")
    position.volume = .2
    with pytest.raises(ExecutionError):
        adapter.send_once({}, before_send=lambda mt5: adapter._validate_exposure(mt5, scope, operation="close"))
    assert sends == []


def test_final_locked_scope_check_runs_before_single_send():
    module, _, _, scope = book()
    events = []
    module.order_send = lambda request: (events.append("send") or NS(retcode=10009, order=10, deal=11))
    adapter = MT5ExecutionAdapter(Client(module))
    def verify(mt5):
        events.append("verify")
        adapter._validate_exposure(mt5, scope, operation="close")
    adapter.send_once({}, before_send=verify)
    assert events == ["verify", "send"]


@pytest.mark.parametrize("positions,orders", [([NS()], []), ([], [NS()]), (None, []), ([], None)])
def test_entry_rejects_nonempty_or_unknown_baseline(positions, orders):
    module = NS(positions_get=lambda **kw: positions, orders_get=lambda **kw: orders)
    with pytest.raises(ExecutionError):
        MT5ExecutionAdapter(Client(module)).assert_entry_scope("TEST")
