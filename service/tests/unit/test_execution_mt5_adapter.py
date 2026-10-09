from types import SimpleNamespace

import pytest

from service.execution.errors import AmbiguousMT5Result, ExecutionError
from service.execution.idempotency import IdempotencyStore, correlation_token
from service.execution.mt5_adapter import MT5ExecutionAdapter
from service.infrastructure.mt5.session import AccountSessionGuard, AccountSessionTransition
from service.infrastructure.mt5.symbol_resolver import SymbolResolutionError


class Client:
    def __init__(self, module):
        self.module = module
        self._resolver = object()

    def call(self, callback):
        return callback(self.module)

    def resolve(self, symbol):
        return symbol


class SessionClient(Client):
    def __init__(self, module, session):
        super().__init__(module)
        self.session = session

    def session_status(self, *, refresh=False):
        return self.session


def test_account_requires_explicit_demo_mode():
    module = SimpleNamespace(
        ACCOUNT_TRADE_MODE_DEMO=0,
        account_info=lambda: SimpleNamespace(trade_mode=2, balance=1),
    )
    adapter = MT5ExecutionAdapter(Client(module))
    with pytest.raises(ExecutionError, match="Demo"):
        adapter.require_demo()


def test_account_session_transition_is_stable_read_error():
    class TransitionClient:
        def call(self, _callback):
            raise AccountSessionTransition("transition")

    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(TransitionClient()).account()
    assert error.value.code == "account_session_transition"
    assert error.value.status == 503


def test_symbol_resolver_runs_after_guarded_connection_readiness():
    class ResolverClient(Client):
        def __init__(self, module):
            super().__init__(module)
            self._resolver = None
            self.connected = False
            self.initialized_symbols = None

        def call(self, callback):
            self.connected = True
            return super().call(callback)

        def init_resolver(self, symbols):
            assert self.connected
            self.initialized_symbols = symbols
            self._resolver = object()

    module = SimpleNamespace(
        symbol_info=lambda _symbol: SimpleNamespace(
            digits=2, ask=1, point=0.01, volume_min=0.01, volume_max=10, volume_step=0.01,
        ),
        order_calc_margin=lambda *_args: None,
    )
    client = ResolverClient(module)
    result = MT5ExecutionAdapter(client, ("XAUUSDm", "BTC")).symbol("BTC")
    assert result["symbol"] == "BTC"
    assert client.initialized_symbols == ("XAUUSDm", "BTC")
    assert result["margin_per_lot"] is None


def test_unavailable_symbol_resolver_returns_stable_error_before_initialization():
    class DisconnectedClient:
        _resolver = None

        def call(self, _callback):
            raise ConnectionError("private connection diagnostics")

        def init_resolver(self, _symbols):
            raise AssertionError("resolver must not initialize before MT5 is connected")

    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(DisconnectedClient()).symbol("XAUUSDm")
    assert error.value.code == "mt5_unavailable"
    assert error.value.status == 503
    assert "private" not in error.value.message


@pytest.mark.parametrize("method,code", [
    ("order", "invalid_order_id"), ("cancel", "invalid_order_id"),
    ("close", "invalid_position_id"),
])
@pytest.mark.parametrize("ticket", [None, True, "bad", "-1", "0", "1.0", "١", "18446744073709551616"])
def test_invalid_tickets_never_call_mt5(method, code, ticket):
    class NoTerminalClient:
        def call(self, _callback):
            raise AssertionError("invalid tickets must not access MT5")

    with pytest.raises(ExecutionError) as error:
        getattr(MT5ExecutionAdapter(NoTerminalClient()), method)(ticket)
    assert error.value.status == 400
    assert error.value.code == code


def test_invalid_mt5_deal_is_a_redacted_mapping_error():
    module = SimpleNamespace(history_deals_get=lambda *_args: [SimpleNamespace(ticket=1)])
    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(Client(module)).deals()
    assert error.value.code == "mt5_deal_mapping_error"
    assert error.value.status == 502
    assert "required MT5" not in error.value.message


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
    assert result["stops_level"] == "20"
    assert result["freeze_level"] == "10"
    assert result["margin_per_lot"] == "1000"
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


@pytest.mark.parametrize("session,code,status", [
    ({"state": "ready", "ready": True, "generation": 2,
      "fingerprint": {"id": "real", "account_mode": "REAL"}},
     "real_account_forbidden", 403),
    ({"state": "unknown", "ready": False, "generation": 2,
      "fingerprint": {"account_mode": "UNKNOWN"}},
     "account_mode_unknown", 503),
    ({"state": "switch_detected", "ready": False, "generation": 3,
      "fingerprint": {"account_mode": "DEMO"}},
     "account_session_transition", 503),
])
def test_mutation_session_has_stable_mode_and_transition_errors(session, code, status):
    adapter = MT5ExecutionAdapter(SessionClient(SimpleNamespace(), session))
    with pytest.raises(ExecutionError) as error:
        adapter.mutation_session()
    assert error.value.code == code
    assert error.value.status == status


def test_mutation_session_rejects_generation_change_before_send():
    account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    session = AccountSessionGuard().observe_account_info(account)
    module = SimpleNamespace(account_info=lambda: account, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2)
    adapter = MT5ExecutionAdapter(SessionClient(module, session))
    expected = adapter.mutation_session()
    session["generation"] = 3
    with pytest.raises(ExecutionError) as error:
        adapter.validate_mutation_session(expected)
    assert error.value.code == "session_changed_before_send"
    assert error.value.status == 409


def raw_order(**changes):
    return SimpleNamespace(**{
        "ticket": 7, "symbol": "GOLD.sim", "type": 0, "state": 1, "type_time": 0,
        "volume_initial": .1, "volume_current": .1, "price_open": 2001,
        "sl": 0, "tp": 0, "time_setup": 1_798_459_200, "time_done": 0,
        "comment": correlation_token("original-client"), **changes,
    })


def raw_deal(**changes):
    return SimpleNamespace(**{
        "ticket": 8, "order": 7, "position_id": 9, "symbol": "GOLD.sim", "type": 0,
        "entry": 0, "volume": .1, "price": 2001, "time": 1_798_459_200, **changes,
    })


@pytest.mark.parametrize("margin", [None, 0, -1, float("nan"), float("inf")])
def test_missing_risk_facts_stay_unknown_and_invalid_margin_is_null(margin):
    info = SimpleNamespace(digits=2, point=.01, volume_min=.01, volume_max=10, volume_step=.01, ask=2001)
    module = SimpleNamespace(symbol_info=lambda _symbol: info, order_calc_margin=lambda *_args: margin)
    result = MT5ExecutionAdapter(Client(module)).symbol("XAUUSDm")
    assert result["stops_level"] is None
    assert result["freeze_level"] is None
    assert result["margin_per_lot"] is None
    info.trade_stops_level = 0
    info.trade_freeze_level = 0
    result = MT5ExecutionAdapter(Client(module)).symbol("XAUUSDm")
    assert result["stops_level"] == result["freeze_level"] == "0"


@pytest.mark.parametrize("reason,status,code", [
    ("unsupported_symbol", 404, "symbol_not_found"),
    ("ambiguous_broker_symbol", 404, "symbol_not_found"),
    ("symbol_catalog_unavailable", 503, "mt5_unavailable"),
    ("resolver_not_initialized", 503, "mt5_unavailable"),
])
def test_symbol_resolution_errors_have_stable_public_codes(reason, status, code):
    class FailedResolver(Client):
        def resolve(self, symbol):
            raise SymbolResolutionError(symbol, reason, ("private-candidate",))

    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(FailedResolver(SimpleNamespace())).symbol("XAUUSDm")
    assert error.value.status == status
    assert error.value.code == code
    assert "private" not in error.value.message


def test_nontrading_deals_are_validated_and_excluded_from_execution_contract():
    module = SimpleNamespace(history_deals_get=lambda *_args: [
        raw_deal(type=2, symbol="", volume=0, price=0, order=0, position_id=0),
        raw_deal(),
    ])
    result = MT5ExecutionAdapter(Client(module)).deals()
    assert len(result) == 1
    assert result[0]["deal_id"] == "8"
    assert result[0]["deal_type"] == "open"
    assert result[0]["direction"] == 1
    assert result[0]["volume"] == "0.1"


@pytest.mark.parametrize("changes", [
    {"type": 99}, {"entry": 99}, {"entry": 2}, {"volume": 0}, {"order": 0}, {"position_id": 0},
])
def test_unrepresentable_trade_deal_fails_mapping(changes):
    module = SimpleNamespace(history_deals_get=lambda *_args: [raw_deal(**changes)])
    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(Client(module)).deals()
    assert error.value.status == 502
    assert error.value.code == "mt5_deal_mapping_error"


def test_order_list_and_lookup_use_original_durable_intent_and_logical_alias(tmp_path):
    payload = {
        "request_id": "original-request", "submitted_at": "2026-01-01T00:00:00Z",
        "intent": {
            "client_order_id": "original-client", "symbol": "XAUUSDm", "direction": 1,
            "order_type": "market", "volume": "0.1", "limit_price": None,
            "stop_loss": "1990", "take_profit": "2020", "time_in_force": "gtc",
        },
    }
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    session = AccountSessionGuard().observe_account_info(account)
    expected = {"generation": session["generation"], "fingerprint": session["fingerprint"]["id"]}
    store.reserve("original-client", "fingerprint", "original-request", payload=payload, session=expected)
    module = SimpleNamespace(
        orders_get=lambda: [raw_order()], history_orders_get=lambda *_args: [],
        account_info=lambda: account, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2,
    )

    class AliasClient(SessionClient):
        def logical_symbol(self, _broker_name):
            return "XAUUSDm"

    adapter = MT5ExecutionAdapter(AliasClient(module, session))
    adapter.bind_idempotency_store(store)
    for order in (adapter.orders()[0], adapter.order("7"), adapter.order_by_client("original-client")):
        assert order["request_id"] == "original-request"
        assert order["intent"] == payload["intent"]
        assert "GOLD.sim" not in str(order)


def test_unknown_position_direction_is_not_assumed_short():
    module = SimpleNamespace(positions_get=lambda: [SimpleNamespace(type=99)])
    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(Client(module)).positions()
    assert error.value.status == 502
    assert error.value.code == "mt5_contract_invalid"


def test_average_fill_price_uses_actual_deals_instead_of_requested_order_price():
    order = raw_order(state=4, volume_current=0, price_open=1990)
    fills = [raw_deal(volume=.04, price=2000), raw_deal(ticket=10, volume=.06, price=2005)]
    module = SimpleNamespace(
        orders_get=lambda: [order], history_orders_get=lambda *_args: [],
        history_deals_get=lambda *, ticket: fills if ticket == 7 else [],
    )
    result = MT5ExecutionAdapter(Client(module)).order("7")
    assert result["average_fill_price"] == "2003"
    assert result["filled_volume"] == "0.1"


def test_connection_loss_after_order_send_is_ambiguous_and_never_retries():
    calls = []

    def send(_request):
        calls.append("send")
        raise ConnectionError("private disconnect after acceptance")

    adapter = MT5ExecutionAdapter(Client(SimpleNamespace(order_send=send)))
    with pytest.raises(AmbiguousMT5Result):
        adapter.send_once({})
    assert calls == ["send"]


def test_session_transition_before_order_send_is_known_not_sent():
    class BeforeSendClient:
        def call(self, _callback):
            raise AccountSessionTransition("private transition")

    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(BeforeSendClient()).send_once({}, expected_session={"generation": 1})
    assert error.value.code == "session_changed_before_send"
    assert error.value.status == 409


@pytest.mark.parametrize("method,module", [
    ("orders", SimpleNamespace(orders_get=lambda: None, history_orders_get=lambda *_args: [])),
    ("positions", SimpleNamespace(positions_get=lambda: None)),
    ("deals", SimpleNamespace(history_deals_get=lambda *_args: None)),
])
def test_mt5_read_failure_is_not_reported_as_an_empty_book(method, module):
    with pytest.raises(ExecutionError) as error:
        getattr(MT5ExecutionAdapter(Client(module)), method)()
    assert error.value.status == 503
    assert error.value.code == "mt5_unavailable"


def test_close_fetches_deal_by_order_ticket_and_sends_only_once():
    sends, history_tickets = [], []
    position = SimpleNamespace(ticket=9, symbol="GOLD.sim", type=0, volume=.1)
    close_deal = raw_deal(type=1, entry=1)

    def send(request):
        sends.append(request)
        return SimpleNamespace(order=7, deal=8, retcode=10009, comment="")

    def history(*, ticket):
        history_tickets.append(ticket)
        return [close_deal]

    module = SimpleNamespace(
        positions_get=lambda *, ticket: [position] if ticket == 9 else [],
        symbol_info=lambda _symbol: SimpleNamespace(ask=2001, bid=2000),
        order_send=send, history_deals_get=history,
    )
    result = MT5ExecutionAdapter(Client(module)).close("9")
    assert result["deal_id"] == "8"
    assert result["broker_order_id"] == "7"
    assert result["deal_type"] == "close"
    assert history_tickets == [7]
    assert len(sends) == 1
    assert sends[0]["position"] == 9


@pytest.mark.parametrize("changed", [
    {"generation": 2}, {"login": 456}, {"trade_mode": 2},
])
def test_locked_send_rechecks_fresh_identity_and_generation_before_invocation(changed):
    account_values = {"login": 123, "server": "demo-test", "trade_mode": 0}
    initial = AccountSessionGuard().observe_account_info(SimpleNamespace(**account_values))
    expected = {"generation": initial["generation"], "fingerprint": initial["fingerprint"]["id"]}
    current = dict(initial)
    sends, fresh_checks, in_call = [], [], []
    account_values.update({field: value for field, value in changed.items() if field != "generation"})
    current["generation"] = changed.get("generation", current["generation"])

    def account_info():
        assert in_call == [True], "Fresh identity must be checked inside the guarded client callback"
        fresh_checks.append(True)
        return SimpleNamespace(**account_values)

    module = SimpleNamespace(
        account_info=account_info, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2,
        order_send=lambda request: sends.append(request),
    )

    class LockedClient(SessionClient):
        def call(self, callback):
            in_call.append(True)
            try:
                return super().call(callback)
            finally:
                in_call.pop()

    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(LockedClient(module, current)).send_once({}, expected_session=expected)
    assert error.value.code == "session_changed_before_send"
    assert error.value.status == 409
    assert fresh_checks == [True]
    assert sends == []


def test_fresh_demo_identity_allows_one_send_and_loss_afterward_is_ambiguous():
    account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    session = AccountSessionGuard().observe_account_info(account)
    expected = {"generation": session["generation"], "fingerprint": session["fingerprint"]["id"]}
    sends = []

    def send(request):
        sends.append(request)
        raise ConnectionError("private failure after invocation")

    module = SimpleNamespace(
        account_info=lambda: account, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2,
        order_send=send,
    )
    with pytest.raises(AmbiguousMT5Result):
        MT5ExecutionAdapter(SessionClient(module, session)).send_once({}, expected_session=expected)
    assert sends == [{}]


@pytest.mark.parametrize("position", [
    SimpleNamespace(ticket=9, symbol="XAUUSDm", type=99, volume=.1),
    SimpleNamespace(ticket=9, symbol="XAUUSDm", type=True, volume=.1),
    SimpleNamespace(ticket=10, symbol="XAUUSDm", type=0, volume=.1),
    SimpleNamespace(ticket=9, symbol="XAUUSDm", type=0, volume=0),
])
def test_close_rejects_unusable_position_facts_before_send(position):
    sends = []
    module = SimpleNamespace(
        positions_get=lambda **_kwargs: [position],
        order_send=lambda request: sends.append(request),
    )
    with pytest.raises(ExecutionError) as error:
        MT5ExecutionAdapter(Client(module)).close("9")
    assert error.value.code == "mt5_contract_invalid"
    assert error.value.status == 502
    assert sends == []


@pytest.mark.parametrize("intent_changes", [
    {"volume": "0.2"}, {"direction": -1}, {"symbol": "BTC"},
])
def test_durable_intent_must_agree_observed_order_before_enrichment(tmp_path, intent_changes):
    from service.tests.unit.test_execution_api import submit_payload

    account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    session = AccountSessionGuard().observe_account_info(account)
    expected = {"generation": session["generation"], "fingerprint": session["fingerprint"]["id"]}
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    store.reserve(
        "original-client", "fingerprint", "request-1",
        payload=submit_payload("original-client", volume="0.1", **intent_changes) if "volume" not in intent_changes
        else submit_payload("original-client", **intent_changes), session=expected,
    )
    module = SimpleNamespace(
        orders_get=lambda: [raw_order(symbol="XAUUSDm")], history_orders_get=lambda *_args: [],
        account_info=lambda: account, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2,
    )
    adapter = MT5ExecutionAdapter(SessionClient(module, session))
    adapter.bind_idempotency_store(store)
    with pytest.raises(ExecutionError) as error:
        adapter.order("7")
    assert error.value.status == 502
    assert error.value.code == "mt5_contract_invalid"


def test_cross_account_recovery_and_unscoped_legacy_records_fail_closed(tmp_path):
    from service.tests.unit.test_execution_api import submit_payload

    first_account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    first = AccountSessionGuard().observe_account_info(first_account)
    second_account = SimpleNamespace(login=456, server="demo-test", trade_mode=0)
    second = AccountSessionGuard().observe_account_info(second_account)
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    store.reserve(
        "original-client", "fingerprint", "request-1", payload=submit_payload("original-client"),
        session={"generation": first["generation"], "fingerprint": first["fingerprint"]["id"]},
    )
    store.reserve("legacy-client", "fingerprint", "request-2")
    module = SimpleNamespace(
        account_info=lambda: second_account, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2,
        orders_get=lambda: [raw_order()], history_orders_get=lambda *_args: [],
    )
    adapter = MT5ExecutionAdapter(SessionClient(module, second))
    adapter.bind_idempotency_store(store)
    with pytest.raises(ExecutionError) as cross_account:
        adapter.order_by_client("original-client")
    assert cross_account.value.status == 503
    assert cross_account.value.code == "account_session_transition"
    with pytest.raises(ExecutionError) as legacy:
        adapter.validate_recovery_session(store.get("legacy-client"))
    assert legacy.value.status == 503
    assert legacy.value.code == "account_session_unverifiable"
    assert store.get("original-client").state == "pending"


def test_recovery_same_account_allows_new_connection_generation(tmp_path):
    account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    session = AccountSessionGuard().observe_account_info(account)
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    record, _created = store.reserve(
        "original-client", "fingerprint", "request-1",
        session={"generation": session["generation"], "fingerprint": session["fingerprint"]["id"]},
    )
    session["generation"] += 10
    module = SimpleNamespace(account_info=lambda: account, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2)
    adapter = MT5ExecutionAdapter(SessionClient(module, session))
    result = adapter.validate_recovery_session(record)
    assert result["generation"] == session["generation"]
    assert result["fingerprint"]["id"] == record.session["fingerprint"]


@pytest.mark.parametrize("method,args", [("orders", ()), ("symbol", ("XAUUSDm",))])
def test_get_rejects_account_switch_between_guarded_read_calls(method, args):
    account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    session = AccountSessionGuard().observe_account_info(account)
    info = SimpleNamespace(digits=2, point=.01, volume_min=.01, volume_max=10, volume_step=.01, ask=2001)
    module = SimpleNamespace(
        orders_get=lambda: [], history_orders_get=lambda *_args: [],
        symbol_info=lambda _symbol: info, order_calc_margin=lambda *_args: 100,
    )

    class SwitchingClient(SessionClient):
        def __init__(self):
            super().__init__(module, session)
            self.calls = 0

        def call(self, callback):
            result = super().call(callback)
            self.calls += 1
            if self.calls == 1:
                self.session["generation"] += 1
            return result

    with pytest.raises(ExecutionError) as error:
        getattr(MT5ExecutionAdapter(SwitchingClient()), method)(*args)
    assert error.value.status == 503
    assert error.value.code == "account_session_transition"


def test_mutation_uses_locked_guard_instead_of_refreshing_monitor_snapshot():
    account = SimpleNamespace(login=123, server="demo-test", trade_mode=0)
    guard = AccountSessionGuard()
    status = guard.observe_account_info(account)
    expected = {"generation": status["generation"], "fingerprint": status["fingerprint"]["id"]}
    sends = []
    module = SimpleNamespace(
        account_info=lambda: account, ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_REAL=2,
        order_send=lambda request: sends.append(request) or SimpleNamespace(retcode=10009, order=7, deal=8),
    )

    class MonitorClient(Client):
        session_guard = guard

        def session_status(self, *, refresh=False):
            return dict(status, state="refreshing", ready=False)

    MT5ExecutionAdapter(MonitorClient(module)).send_once({}, expected_session=expected)
    assert sends == [{}]


@pytest.mark.parametrize("retcode", [10012, 10031])
def test_timeout_and_connection_retcodes_are_ambiguous_after_one_send(retcode):
    sends = []

    def send(request):
        sends.append(request)
        return SimpleNamespace(retcode=retcode, order=0, deal=0, comment="private broker details")

    with pytest.raises(AmbiguousMT5Result) as error:
        MT5ExecutionAdapter(Client(SimpleNamespace(order_send=send))).send_once({"symbol": "XAUUSDm"})
    assert error.value.status == 503
    assert error.value.code == "execution_outcome_unknown"
    assert error.value.retcode == retcode
    assert "private" not in error.value.message
    assert sends == [{"symbol": "XAUUSDm"}]
