import threading
from core.connection_manager import MT5Connector


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

    def __init__(self):
        self._lock = threading.Lock()
        self._connector = None
        self._mt5 = None

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
            return func(self._mt5)

    def _ensure_connected_unsafe(self):
        """不帶 lock 的連線檢查（caller 需已持有 _lock）"""
        if self._mt5 is None:
            self._connector = MT5Connector()
            self._mt5 = self._connector.connect()
        return self._mt5 is not None

    @property
    def mt5(self):
        """直接存取 mt5 物件。僅用於屬性讀取（如 TIMEFRAME_M5），不做 RPyC 調用。"""
        return self._mt5

    def reset(self):
        """錯誤發生時重置連線，下次 ensure_connected 會重新建立。"""
        with self._lock:
            if self._mt5 is not None:
                try:
                    self._mt5.shutdown()
                except Exception:
                    pass
                self._mt5 = None
                self._connector = None

    def shutdown(self):
        """優雅關閉 MT5 連線。"""
        self.reset()
