import threading

from core.connection_manager import MT5Connector, close_mt5_connection


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

    def __init__(self, connector_factory=MT5Connector):
        self._lock = threading.RLock()
        self._connector_factory = connector_factory
        self._connector = None
        self._mt5 = None
        self._resolver = None
        self._resolver_symbols = None

    def ensure_connected(self):
        """檢查連線狀態，斷線時自動重連。多 thread 安全。"""
        with self._lock:
            return self._ensure_connected_unsafe()

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
                raise ConnectionError("MT5 not connected")
            try:
                return func(self._mt5)
            except Exception:
                self._reset_unsafe()
                raise

    def _ensure_connected_unsafe(self):
        """不帶 lock 的連線檢查（caller 需已持有 _lock）"""
        if self._mt5 is not None:
            return True
        connector = None
        try:
            connector = self._connector_factory()
            mt5 = connector.connect()
            if mt5 is None:
                self._close_connection(None, connector)
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

    def _reset_unsafe(self):
        mt5, connector = self._mt5, self._connector
        self._mt5 = None
        self._connector = None
        self._close_connection(mt5, connector)

    def reset(self):
        """錯誤發生時重置連線，下次 ensure_connected 會重新建立。"""
        with self._lock:
            self._reset_unsafe()

    def init_resolver(self, configured_symbols):
        """初始化 SymbolResolver，建立 symbol 對照表。

        必須在 ensure_connected() 之後呼叫（需已取得 _mt5）。
        """
        from service.core.symbol_resolver import SymbolResolver
        with self._lock:
            if self._mt5 is None:
                raise RuntimeError("MT5 not connected — call ensure_connected() first")
            self._resolver_symbols = tuple(configured_symbols)
            self._resolver = SymbolResolver()
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
        self.reset()
