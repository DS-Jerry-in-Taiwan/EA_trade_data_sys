"""
Phase 12: SymbolResolver — 動態 Symbol 解析層。

功能：
- initialize(): 連上 MT5 broker 查詢可用 symbol 列表，建立對照表
- resolve(): 精確比對優先 → 模糊比對（去 m、加 m、大小寫）→ 原名稱 fallback
- refresh(): 重新查詢（重連後呼叫）
"""
import threading


class SymbolResolver:
    """動態 Symbol 解析器。精確比對優先，模糊比對作為 fallback。"""

    def __init__(self):
        self._mapping = {}
        self._unresolved = []
        self._broker_symbols = set()
        self._initialized = False
        self._lock = threading.Lock()

    def initialize(self, mt5, configured_symbols):
        """查詢 broker 可用 symbol，建立 logical → broker 對照表。

        Args:
            mt5: MT5 connector 物件（需已連線）
            configured_symbols: settings.yaml 中的 symbol 名稱列表
        """
        with self._lock:
            all_symbols = mt5.symbols_get()
            self._broker_symbols = {s.name for s in all_symbols}
            for logical in configured_symbols:
                resolved = self._resolve_one(logical)
                if resolved:
                    self._mapping[logical] = resolved
                else:
                    self._unresolved.append(logical)
            self._initialized = True
            print(f'[SymbolResolver] Resolved {len(self._mapping)}/{len(configured_symbols)}.'
                  f' Unresolved: {self._unresolved}')

    def resolve(self, logical_name):
        """將 logical name 解析為 broker 實際名稱。

        若未初始化或找不到對應，回傳 logical_name 本身（graceful fallback）。
        """
        if not self._initialized:
            return logical_name
        return self._mapping.get(logical_name, logical_name)

    def _resolve_one(self, logical):
        """單一 symbol 解析流程。"""
        # ① 精確比對
        if logical in self._broker_symbols:
            return logical
        # ② 去 m 後綴（XAUUSDm → XAUUSD）
        if logical.endswith('m') and logical[:-1] in self._broker_symbols:
            print(f'[SymbolResolver] Fuzzy match: "{logical}" → "{logical[:-1]}"')
            return logical[:-1]
        # ③ 加 m 後綴（XAUUSD → XAUUSDm）
        if logical + 'm' in self._broker_symbols:
            print(f'[SymbolResolver] Fuzzy match: "{logical}" → "{logical + "m"}"')
            return logical + 'm'
        # ④ 大小寫不敏感
        for bs in self._broker_symbols:
            if bs.lower() == logical.lower():
                print(f'[SymbolResolver] Case-insensitive match: "{logical}" → "{bs}"')
                return bs
        return None

    @property
    def mapping(self):
        return dict(self._mapping) if self._initialized else {}

    @property
    def unresolved(self):
        return list(self._unresolved) if self._initialized else []

    def refresh(self, mt5, configured_symbols):
        """重新查詢 broker symbol 列表（重連後呼叫）。"""
        with self._lock:
            self._mapping = {}
            self._unresolved = []
            self._initialized = False
        self.initialize(mt5, configured_symbols)
