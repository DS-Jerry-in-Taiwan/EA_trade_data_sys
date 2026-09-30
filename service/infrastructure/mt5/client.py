"""Thread-safe, process-local lifecycle for an MT5 connection.

An ``MT5Client`` instance must never be shared between operating-system
processes. Each worker owns its own instance while sharing these lifecycle
and reconnect rules.
"""

import threading

from core.connection_manager import MT5Connector, close_mt5_connection
from service.infrastructure.mt5.session import (
    ACCOUNT_SYMBOLS_UNAVAILABLE,
    AccountSessionGuard,
    AccountSessionTransition,
)


class MT5Client:
    """統一的 MT5 連線管理器。

    功能：
    - ensure_connected(): 檢查連線，斷線自動重連
    - call(func): thread-safe 執行 MT5 方法（附 lock 保護）
    - reset(): 錯誤時重置連線
    - shutdown(): 優雅關閉

    使用方式：
        mt5_client = MT5Client()
        if mt5_client.ensure_connected():
            tick = mt5_client.call(lambda m: m.symbol_info_tick("XAUUSDm"))
    """

    def __init__(
        self,
        connector_factory=MT5Connector,
        resolver_factory=None,
        session_guard=None,
    ):
        self._lock = threading.RLock()
        self._connector_factory = connector_factory
        self._resolver_factory = resolver_factory
        self._session_guard = session_guard or AccountSessionGuard()
        self._session_supported = False
        self._last_transition_detected = False
        self._session_snapshot_lock = threading.Lock()
        self._session_monitor_lock = threading.Lock()
        self._session_monitor_stop = threading.Event()
        self._session_monitor_thread = None
        self._session_snapshot = self._session_guard.status()
        self._connector = None
        self._mt5 = None
        self._resolver = None
        self._resolver_symbols = None

    def ensure_connected(self):
        """檢查連線狀態，斷線時自動重連。多 thread 安全。"""
        with self._lock:
            result = self._ensure_connected_unsafe()
            self._publish_session_status_unsafe()
            return result

    def call(self, func):
        """Thread-safe 執行 MT5 方法調用。

        Args:
            func: 接收 mt5 物件並呼叫方法的 lambda，
                  例如 lambda m: m.symbol_info_tick("XAUUSDm")

        Returns:
            MT5 方法回傳值

        Raises:
            ConnectionError: 若 MT5 未連線
        """
        with self._lock:
            if not self._ensure_connected_unsafe():
                self._publish_session_status_unsafe()
                if self._session_guard.state == "switch_detected" or self._last_transition_detected:
                    raise AccountSessionTransition(
                        "MT5 account session transition requires reconciliation"
                    )
                raise ConnectionError("MT5 not connected")
            generation = self._session_guard.generation
            try:
                result = func(self._mt5)
            except Exception:
                self._reset_unsafe()
                raise
            if self._session_supported:
                status = self._session_guard.status()
                if not self._observe_session_unsafe(self._mt5):
                    self._publish_session_status_unsafe()
                    if status["state"] == "switch_detected" or self._session_guard.state == "switch_detected":
                        self._reconcile_session_unsafe()
                        raise AccountSessionTransition(
                            "MT5 account session transition requires reconciliation"
                        )
                    raise ConnectionError("MT5 account session is not ready")
                if self._session_guard.generation != generation:
                    raise AccountSessionTransition(
                        "MT5 account session changed during read"
                    )
            self._publish_session_status_unsafe()
            return result

    def _ensure_connected_unsafe(self):
        """不帶 lock 的連線檢查（caller 需已持有 _lock）"""
        self._last_transition_detected = False
        if self._mt5 is not None:
            observed = self._observe_session_unsafe(self._mt5)
            if not observed and self._session_guard.state == "switch_detected":
                # A switch is reconciled in this same controlled lifecycle,
                # but this invocation remains fail-closed. A later read can
                # use the rebuilt state.
                self._last_transition_detected = True
                self._reconcile_session_unsafe()
                return False
            return observed
        connector = None
        try:
            connector = self._connector_factory()
            mt5 = connector.connect()
            if mt5 is None:
                self._close_connection(None, connector)
                return False
            if not self._observe_session_unsafe(mt5):
                if self._session_guard.state == "switch_detected":
                    # Keep the newly attached terminal available for the
                    # reconciliation step. Closing it here would make a
                    # post-reset account switch impossible to reconcile.
                    self._connector = connector
                    self._mt5 = mt5
                    self._last_transition_detected = True
                    self._reconcile_session_unsafe()
                    return False
                self._close_connection(mt5, connector)
                return False
            self._connector = connector
            self._mt5 = mt5
            self._refresh_resolver_unsafe()
            return True
        except ConnectionError:
            self._close_connection(None, connector)
            self._connector = None
            self._mt5 = None
            return False
        except Exception:
            self._close_connection(None, connector)
            self._connector = None
            self._mt5 = None
            raise

    @staticmethod
    def _close_connection(mt5, connector):
        close_mt5_connection(mt5)
        close = getattr(connector, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    @property
    def mt5(self):
        """直接存取 mt5 物件。僅用於屬性讀取（如 TIMEFRAME_M5），不做 RPyC 調用。"""
        with self._lock:
            return self._mt5

    @property
    def session_guard(self):
        """The process-local account-session guard used by this client."""
        return self._session_guard

    def session_status(self, *, refresh=False):
        """Return non-secret account-session readiness facts.

        Health probes may request a refresh. The refresh is read-only and
        observes the current terminal account through ``account_info``; it
        never supplies credentials or changes the selected account.
        """
        if refresh:
            # Health endpoints must never wait on a potentially stalled RPyC
            # account_info() call. Start one daemon monitor and return the
            # last snapshot immediately; the monitor is single-flight.
            self._start_session_monitor()
        if not refresh and self._lock.acquire(blocking=False):
            try:
                self._publish_session_status_unsafe()
            finally:
                self._lock.release()
        return self._session_snapshot_copy()

    def _session_snapshot_copy(self):
        with self._session_snapshot_lock:
            return dict(self._session_snapshot)

    def _publish_session_status_unsafe(self):
        with self._session_snapshot_lock:
            self._session_snapshot = self._session_guard.status()

    def _mark_session_refreshing(self):
        with self._session_snapshot_lock:
            current = dict(self._session_snapshot)
            current.update({
                "state": "refreshing",
                "ready": False,
                "error": "session_refresh_in_progress",
            })
            self._session_snapshot = current

    def _start_session_monitor(self):
        with self._session_monitor_lock:
            if (
                self._session_monitor_thread is not None
                and self._session_monitor_thread.is_alive()
            ):
                return
            self._session_monitor_stop.clear()
            self._session_monitor_thread = threading.Thread(
                target=self._session_monitor_loop,
                name="mt5-session-monitor",
                daemon=True,
            )
            self._session_monitor_thread.start()

    def _session_monitor_loop(self):
        # A short interval keeps readiness current while ensuring there is
        # never more than one potentially blocking account_info call per
        # MT5Client process. Health callers only observe snapshots.
        while not self._session_monitor_stop.is_set():
            self._mark_session_refreshing()
            try:
                with self._lock:
                    try:
                        self._ensure_connected_unsafe()
                    except Exception:
                        self._session_guard.mark_disconnected()
                    self._publish_session_status_unsafe()
            finally:
                self._session_monitor_stop.wait(1.0)

    def transition_detected(self):
        """Whether the most recent ensure attempt observed a session switch."""
        with self._lock:
            return self._last_transition_detected

    def reconcile_session(self):
        """Rebuild account-scoped state and acknowledge an observed account.

        Production reads invoke the same controlled path automatically after a
        switch is detected; this explicit method remains useful to operators
        and tests that want to request another reconciliation attempt.
        """
        with self._lock:
            return self._reconcile_session_unsafe()

    def _reconcile_session_unsafe(self):
        """Rebuild account-scoped state while the client lock is held."""
        if self._mt5 is None:
            return self._session_guard.status()
        if self._session_guard.state != "switch_detected":
            return self._session_guard.status()

        # Build account-scoped state against the newly observed terminal
        # before making the guard ready. The old resolver was invalidated as
        # soon as the switch was detected. No client.ensure/call recursion is
        # possible because initialization uses the current MT5 object directly.
        if self._resolver_symbols:
            resolver_factory = self._resolver_factory
            if resolver_factory is None:
                from service.infrastructure.mt5.symbol_resolver import SymbolResolver

                resolver_factory = SymbolResolver
            try:
                candidate = resolver_factory()
                candidate.initialize(self._mt5, self._resolver_symbols)
            except Exception:
                self._session_guard.mark_reconciliation_failed()
                return self._session_guard.status()
            if candidate.unresolved:
                self._session_guard.mark_reconciliation_failed(
                    ACCOUNT_SYMBOLS_UNAVAILABLE
                )
                return self._session_guard.status()
            self._resolver = candidate
        return self._session_guard.reconcile()

    def _observe_session_unsafe(self, mt5):
        """Observe account identity when the transport exposes account_info."""
        try:
            account_info = getattr(mt5, "account_info", None)
            # Lightweight test doubles and alternate adapters may not expose
            # the MT5 account method. The real pymt5linux adapter always does;
            # retain compatibility for those injected clients without
            # inventing identity.
            if not callable(account_info):
                self._session_supported = False
                return True
            self._session_supported = True
            info = account_info()
            demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
            real_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)
        except Exception:
            self._session_guard.mark_disconnected()
            return False
        status = self._session_guard.observe_account_info(
            info, demo_trade_mode=demo_mode, real_trade_mode=real_mode
        )
        if status["state"] in {"switch_detected", "unknown"}:
            # Resolver mappings and any future account-scoped state must never
            # be reused for a different terminal account.
            self._resolver = None
        return bool(status["ready"])

    def _reset_unsafe(self):
        mt5, connector = self._mt5, self._connector
        self._mt5 = None
        self._connector = None
        self._session_guard.mark_disconnected()
        self._close_connection(mt5, connector)

    def reset(self):
        """錯誤發生時重置連線，下次 ensure_connected 會重新建立。"""
        with self._lock:
            self._reset_unsafe()

    def init_resolver(self, configured_symbols):
        """初始化 SymbolResolver，建立 symbol 對照表。

        必須在 ensure_connected() 之後呼叫（需已取得 _mt5）。
        """
        from service.infrastructure.mt5.symbol_resolver import SymbolResolver
        with self._lock:
            if self._mt5 is None:
                raise RuntimeError("MT5 not connected — call ensure_connected() first")
            if self._session_supported and not self._session_guard.ready:
                if self._session_guard.state == "switch_detected":
                    raise AccountSessionTransition(
                        "MT5 account session transition requires reconciliation"
                    )
                raise ConnectionError("MT5 account session is not ready")
            self._resolver_symbols = tuple(configured_symbols)
            resolver_factory = self._resolver_factory or SymbolResolver
            self._resolver = resolver_factory()
            self._resolver.initialize(self._mt5, self._resolver_symbols)

    def resolve(self, logical_name):
        """將 logical name 解析為 broker 實際名稱。

        若 resolver 未初始化，回傳原名稱（graceful fallback）。
        """
        with self._lock:
            if self._resolver is None:
                return logical_name
            return self._resolver.resolve(logical_name)

    def _refresh_resolver_unsafe(self):
        if self._resolver is not None and self._resolver_symbols is not None:
            self._resolver.refresh(self._mt5, self._resolver_symbols)

    def is_resolved(self, logical_name):
        """Return whether resolver initialization produced an explicit mapping."""
        with self._lock:
            return (
                self._resolver is not None
                and logical_name in self._resolver.mapping
            )

    def refresh_resolver(self, configured_symbols=None):
        """在 reset() 重連後重新初始化 resolver。"""
        with self._lock:
            if configured_symbols is not None:
                self._resolver_symbols = tuple(configured_symbols)
            if self._mt5 is not None:
                self._refresh_resolver_unsafe()

    def shutdown(self):
        """優雅關閉 MT5 連線。"""
        self._session_monitor_stop.set()
        # A stalled RPyC refresh may still own the lifecycle lock.  Shutdown
        # must not turn that bounded health design into an unbounded process
        # exit; the monitor is daemonized and the container supervisor will
        # reclaim the transport with the process.
        if self._lock.acquire(timeout=1.0):
            try:
                self._reset_unsafe()
            finally:
                self._lock.release()
