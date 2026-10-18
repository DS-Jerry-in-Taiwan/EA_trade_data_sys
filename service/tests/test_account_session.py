from types import SimpleNamespace

from service.infrastructure.mt5.session import (
    ACCOUNT_IDENTITY_UNKNOWN,
    ACCOUNT_MODE_UNKNOWN,
    ACCOUNT_SESSION_CHANGED,
    AccountSessionGuard,
    DISCONNECTED,
    READY,
    SWITCH_DETECTED,
    UNKNOWN,
)


def account(login=123456, server="OANDA-Demo-1", trade_mode=0):
    return SimpleNamespace(login=login, server=server, trade_mode=trade_mode)


def test_guard_starts_disconnected_with_stable_error():
    status = AccountSessionGuard().status()
    assert status == {
        "state": DISCONNECTED,
        "ready": False,
        "generation": 0,
        "fingerprint": None,
        "error": "mt5_disconnected",
    }


def test_guard_exposes_only_hashed_identity_and_ready_facts():
    guard = AccountSessionGuard()
    status = guard.observe_account_info(account())

    assert status["state"] == READY
    assert status["ready"] is True
    assert status["generation"] == 1
    assert status["fingerprint"]["server"] == "OANDA-Demo-1"
    assert status["fingerprint"]["account_mode"] == "DEMO"
    assert status["fingerprint"]["login_hash"] != str(123456)
    assert "123456" not in str(status)


def test_changed_account_enters_transition_until_reconciled():
    guard = AccountSessionGuard()
    guard.observe_account_info(account())
    changed = guard.observe_account_info(account(login=987654))

    assert changed["state"] == SWITCH_DETECTED
    assert changed["ready"] is False
    assert changed["generation"] == 2
    assert changed["error"] == ACCOUNT_SESSION_CHANGED
    assert guard.reconcile()["state"] == READY
    assert guard.status()["generation"] == 2


def test_disconnect_and_same_account_reconnect_get_new_generation():
    guard = AccountSessionGuard()
    guard.observe_account_info(account())
    guard.mark_disconnected()
    assert guard.status()["ready"] is False

    reconnected = guard.observe_account_info(account())
    assert reconnected["state"] == READY
    assert reconnected["generation"] == 2


def test_unknown_mode_and_identity_fail_closed_with_stable_errors():
    guard = AccountSessionGuard()
    unknown_mode = guard.observe_account_info(account(trade_mode=99))
    assert unknown_mode["state"] == UNKNOWN
    assert unknown_mode["ready"] is False
    assert unknown_mode["error"] == ACCOUNT_MODE_UNKNOWN
    assert guard.reconcile()["ready"] is False

    missing_identity = AccountSessionGuard().observe_account_info(account(login=None))
    assert missing_identity["state"] == UNKNOWN
    assert missing_identity["error"] == ACCOUNT_IDENTITY_UNKNOWN


def test_repeated_unknown_identity_is_idempotent():
    guard = AccountSessionGuard()
    first = guard.observe_account_info(account(login=None))
    second = guard.observe_account_info(account(login=None))

    assert first["generation"] == 1
    assert second["generation"] == 1
    assert second["state"] == UNKNOWN


def test_repeated_same_unknown_mode_candidate_is_idempotent():
    guard = AccountSessionGuard()
    first = guard.observe_account_info(account(trade_mode=99))
    second = guard.observe_account_info(account(trade_mode=99))

    assert first["generation"] == 1
    assert second["generation"] == 1
    assert second["error"] == ACCOUNT_MODE_UNKNOWN


def test_mode_change_is_detected_as_account_transition():
    guard = AccountSessionGuard()
    guard.observe_account_info(account(trade_mode=0))
    changed = guard.observe_account_info(account(trade_mode=2))
    assert changed["state"] == SWITCH_DETECTED
    assert changed["ready"] is False
    assert changed["error"] == ACCOUNT_SESSION_CHANGED


def test_repeated_same_switch_candidate_is_idempotent():
    guard = AccountSessionGuard()
    guard.observe_account_info(account(login=123))
    first = guard.observe_account_info(account(login=456))
    second = guard.observe_account_info(account(login=456))

    assert first["generation"] == 2
    assert second["generation"] == 2
    assert second["state"] == SWITCH_DETECTED
