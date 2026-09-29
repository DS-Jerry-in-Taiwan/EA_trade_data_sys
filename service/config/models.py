"""Typed, non-secret settings models for the service configuration file."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name, {})
    return value if isinstance(value, Mapping) else {}


def _str(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _strings(value: Any, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return default
    return tuple(item for item in value if isinstance(item, str))


@dataclass(frozen=True)
class ConnectionSettings:
    default_host: str = "mt5-server"
    port: int = 8001
    timeout: float = 10.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ConnectionSettings":
        timeout = raw.get("timeout", cls.timeout)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("connection.timeout must be a positive number") from exc
        if timeout <= 0:
            raise ValueError("connection.timeout must be a positive number")
        return cls(
            default_host=_str(raw.get("default_host"), cls.default_host),
            port=_int(raw.get("port"), cls.port),
            timeout=timeout,
        )


@dataclass(frozen=True)
class TickServiceSettings:
    enabled: bool = True
    symbols: tuple[str, ...] = ("XAUUSDm",)
    update_interval_seconds: float = 60.0
    socket_path: str = "/run/trade-data/ticks.sock"
    status_path: str = "/run/trade-data/tick-status.json"
    max_age_seconds: float = 120.0
    status_max_age_seconds: float | None = None
    output_dir: str = "/app/service/data/ticks"
    max_retry_seconds: float = 30.0

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], *, environ: Mapping[str, str]
    ) -> "TickServiceSettings":
        return cls(
            enabled=_bool(raw.get("enabled"), cls.enabled),
            symbols=_strings(raw.get("symbols"), cls.symbols),
            update_interval_seconds=_float(
                raw.get("update_interval_seconds"), cls.update_interval_seconds
            ),
            socket_path=environ.get(
                "TICK_SOCKET_PATH", _str(raw.get("socket_path"), cls.socket_path)
            ),
            status_path=environ.get(
                "TICK_STATUS_PATH", _str(raw.get("status_path"), cls.status_path)
            ),
            max_age_seconds=_float(raw.get("max_age_seconds"), cls.max_age_seconds),
            status_max_age_seconds=(
                _float(raw.get("status_max_age_seconds"), 0.0)
                if raw.get("status_max_age_seconds") is not None
                else None
            ),
            output_dir=_str(raw.get("output_dir"), cls.output_dir),
            max_retry_seconds=_float(
                raw.get("max_retry_seconds"), cls.max_retry_seconds
            ),
        )


@dataclass(frozen=True)
class HistorySymbolSettings:
    name: str
    timeframes: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> "HistorySymbolSettings | None":
        if not isinstance(value, Mapping):
            return None
        name = value.get("name")
        if not isinstance(name, str) or not name:
            return None
        return cls(name=name, timeframes=_strings(value.get("timeframes")))

    def as_legacy_dict(self) -> dict[str, Any]:
        return {"name": self.name, "timeframes": list(self.timeframes)}


@dataclass(frozen=True)
class HistoryServiceSettings:
    enabled: bool = True
    status_path: str = "/run/trade-data/history-status.json"
    minimum_bars: dict[str, int] = field(
        default_factory=lambda: {"M5": 500, "M15": 200, "H1": 500, "D1": 200}
    )
    fetch_margin_bars: int = 20
    retention_margin_bars: int | None = None
    fetch_page_size: int = 500
    fetch_timeout_seconds: float = 10.0
    symbols: tuple[HistorySymbolSettings, ...] = ()
    update_interval_seconds: float = 60.0
    data_path: str = "/app/service/data/history"

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], *, environ: Mapping[str, str]
    ) -> "HistoryServiceSettings":
        default_minimums = cls().minimum_bars
        configured_minimums = raw.get("minimum_bars")
        minimum_bars = dict(default_minimums)
        if isinstance(configured_minimums, Mapping):
            for timeframe in minimum_bars:
                minimum_bars[timeframe] = _int(
                    configured_minimums.get(timeframe), minimum_bars[timeframe]
                )
        raw_symbols = raw.get("symbols", [])
        if not isinstance(raw_symbols, (list, tuple)):
            raw_symbols = []
        symbols = tuple(
            item
            for value in raw_symbols
            if (item := HistorySymbolSettings.from_value(value)) is not None
        )
        retention = raw.get("retention_margin_bars")
        return cls(
            enabled=_bool(raw.get("enabled"), cls.enabled),
            status_path=environ.get(
                "HISTORY_STATUS_PATH", _str(raw.get("status_path"), cls.status_path)
            ),
            minimum_bars=minimum_bars,
            fetch_margin_bars=_int(raw.get("fetch_margin_bars"), cls.fetch_margin_bars),
            retention_margin_bars=(
                _int(retention, 0) if retention is not None else None
            ),
            fetch_page_size=_int(raw.get("fetch_page_size"), cls.fetch_page_size),
            fetch_timeout_seconds=_float(
                raw.get("fetch_timeout_seconds"), cls.fetch_timeout_seconds
            ),
            symbols=symbols,
            update_interval_seconds=_float(
                raw.get("update_interval_seconds"), cls.update_interval_seconds
            ),
            data_path=_str(raw.get("data_path"), cls.data_path),
        )


@dataclass(frozen=True)
class ApiGatewaySettings:
    host: str = "0.0.0.0"
    port: int = 8090
    readonly_api_key_env: str = "READONLY_API_KEY"

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], *, environ: Mapping[str, str]
    ) -> "ApiGatewaySettings":
        return cls(
            host=environ.get("API_GATEWAY_HOST", _str(raw.get("host"), cls.host)),
            port=_int(environ.get("API_GATEWAY_PORT"), _int(raw.get("port"), cls.port)),
            readonly_api_key_env=_str(
                raw.get("readonly_api_key_env"), cls.readonly_api_key_env
            ),
        )


@dataclass(frozen=True)
class TradeQuerySettings:
    default_days: int = 7
    max_days: int = 90

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TradeQuerySettings":
        return cls(
            default_days=_int(raw.get("default_days"), cls.default_days),
            max_days=_int(raw.get("max_days"), cls.max_days),
        )


@dataclass(frozen=True)
class RuntimeSettings:
    """Environment-only settings for the process supervisor."""

    app_root: str = "/app"
    shutdown_timeout: float = 10.0


@dataclass(frozen=True)
class Settings:
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    tick_service: TickServiceSettings = field(default_factory=TickServiceSettings)
    history_service: HistoryServiceSettings = field(default_factory=HistoryServiceSettings)
    api_gateway: ApiGatewaySettings = field(default_factory=ApiGatewaySettings)
    trade_query: TradeQuerySettings = field(default_factory=TradeQuerySettings)

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], *, environ: Mapping[str, str]
    ) -> "Settings":
        return cls(
            connection=ConnectionSettings.from_mapping(_section(raw, "connection")),
            tick_service=TickServiceSettings.from_mapping(
                _section(raw, "tick_service"), environ=environ
            ),
            history_service=HistoryServiceSettings.from_mapping(
                _section(raw, "history_service"), environ=environ
            ),
            api_gateway=ApiGatewaySettings.from_mapping(
                _section(raw, "api_gateway"), environ=environ
            ),
            trade_query=TradeQuerySettings.from_mapping(
                _section(raw, "trade_query")
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a compatibility mapping without exposing secret values."""
        result = asdict(self)
        result["tick_service"]["symbols"] = list(self.tick_service.symbols)
        result["history_service"]["symbols"] = [
            item.as_legacy_dict() for item in self.history_service.symbols
        ]
        return result

    def get(self, name: str, default: Any = None) -> Any:
        """Provide the old mapping-style section lookup during migration."""
        return self.as_dict().get(name, default)
