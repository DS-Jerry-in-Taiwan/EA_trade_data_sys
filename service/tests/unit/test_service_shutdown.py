import signal
from unittest.mock import Mock

import pytest

from service.entrypoints import api_gateway
from service.history.worker import HistoryService


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_gateway_shutdown_signal_unwinds(signum):
    with pytest.raises(SystemExit) as exc:
        api_gateway._handle_shutdown_signal(signum, None)
    assert exc.value.code == 128 + signum


def test_gateway_always_closes_ipc_and_mt5(monkeypatch):
    consumer = Mock()
    client = Mock()
    server = Mock()
    server.run.side_effect = RuntimeError("server stopped")
    monkeypatch.setattr(api_gateway, "tick_consumer", consumer)
    monkeypatch.setattr(api_gateway, "mt5_client", client)
    monkeypatch.setattr(api_gateway, "socketio", server)

    with pytest.raises(RuntimeError, match="server stopped"):
        api_gateway.run_gateway()

    consumer.start.assert_called_once_with()
    consumer.stop.assert_called_once_with()
    client.shutdown.assert_called_once_with()


def test_gateway_closes_mt5_even_if_ipc_stop_fails(monkeypatch):
    consumer = Mock()
    consumer.stop.side_effect = RuntimeError("IPC stop failed")
    client = Mock()
    server = Mock()
    monkeypatch.setattr(api_gateway, "tick_consumer", consumer)
    monkeypatch.setattr(api_gateway, "mt5_client", client)
    monkeypatch.setattr(api_gateway, "socketio", server)

    with pytest.raises(RuntimeError, match="IPC stop failed"):
        api_gateway.run_gateway()

    client.shutdown.assert_called_once_with()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_history_shutdown_signal_unwinds(signum):
    from service.history import worker as history_service

    with pytest.raises(SystemExit) as exc:
        history_service._handle_shutdown_signal(signum, None)
    assert exc.value.code == 128 + signum


def test_history_close_releases_executor_and_mt5_once():
    service = HistoryService.__new__(HistoryService)
    service._closed = False
    service._executor = Mock()
    service.mt5_client = Mock()

    service.close()
    service.close()

    service._executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    service.mt5_client.shutdown.assert_called_once_with()


def test_history_close_releases_mt5_if_executor_shutdown_fails():
    service = HistoryService.__new__(HistoryService)
    service._closed = False
    service._executor = Mock()
    service._executor.shutdown.side_effect = RuntimeError("executor stop failed")
    service.mt5_client = Mock()

    with pytest.raises(RuntimeError, match="executor stop failed"):
        service.close()

    service.mt5_client.shutdown.assert_called_once_with()
