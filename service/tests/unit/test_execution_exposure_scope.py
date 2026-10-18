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


def test_final_authorization_runs_after_baseline_and_scope_reads():
    events, sends = [], []
    def positions(**kwargs):
        events.append("positions")
        return []
    module = NS(positions_get=positions, orders_get=lambda **kw: [],
                symbol_info=lambda symbol: NS(volume_min=.1), order_send=lambda request: sends.append(request))
    def authorization(mt5):
        assert events == ["positions", "scope"]
        raise ExecutionError("authorization_entry_closed", "Expired during broker reads", status=403)
    with pytest.raises(ExecutionError):
        MT5ExecutionAdapter(Client(module)).send_once({"symbol": "TEST", "volume": .1},
            expected_empty_symbol="TEST", before_send=lambda mt5: events.append("scope"), final_authorization=authorization)
    assert sends == []


def test_fresh_minimum_volume_change_rejects_send():
    sends = []
    module = NS(positions_get=lambda **kw: [], orders_get=lambda **kw: [],
                symbol_info=lambda symbol: NS(volume_min=.2), order_send=lambda request: sends.append(request))
    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(Client(module)).send_once({"symbol": "TEST", "volume": .1}, expected_empty_symbol="TEST")
    assert error.value.code == "authorization_volume_denied"
    assert sends == []


@pytest.mark.parametrize("changes", [{"volume": .2}, {"type": 0}, {"symbol": "OTHER"}, {"position": 10}])
def test_stale_close_request_cannot_use_current_valid_scope(changes):
    module, _, _, scope = book()
    request = dict(position=9, symbol="TEST", volume=.1, type=1, **{})
    request.update(changes)
    with pytest.raises(ExecutionError):
        MT5ExecutionAdapter(Client(module))._exit_check(module, scope, "close", None, request)
