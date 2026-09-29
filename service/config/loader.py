"""Load the service YAML configuration into typed, non-secret settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

import yaml

from service.config.models import RuntimeSettings, Settings


DEFAULT_CONFIG_PATH = Path("/app/service/config/settings.yaml")
_REPOSITORY_CONFIG_PATH = Path(__file__).with_name("settings.yaml")


def resolve_config_path(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    if path is not None:
        return Path(path)
    configured = env.get("TRADE_DATA_CONFIG")
    if configured:
        return Path(configured)
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return _REPOSITORY_CONFIG_PATH


def load_settings(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Read YAML once and return validated typed settings.

    The loader only reads the configured API-key environment variable name;
    secret values themselves are intentionally never loaded or serialized.
    """
    env = os.environ if environ is None else environ
    config_path = resolve_config_path(path, environ=env)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, Mapping):
        raw = {}
    return Settings.from_mapping(raw, environ=env)


def load_runtime_settings(
    *, environ: Mapping[str, str] | None = None
) -> RuntimeSettings:
    """Parse supervisor environment without reading service YAML or secrets."""
    env = os.environ if environ is None else environ
    raw_timeout = env.get("SUPERVISOR_SHUTDOWN_TIMEOUT", "10")
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("SUPERVISOR_SHUTDOWN_TIMEOUT must be numeric") from exc
    if timeout < 0:
        raise ValueError("SUPERVISOR_SHUTDOWN_TIMEOUT must be non-negative")
    return RuntimeSettings(
        app_root=env.get("TRADE_DATA_APP_ROOT", "/app"),
        shutdown_timeout=timeout,
    )


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "load_runtime_settings",
    "load_settings",
    "resolve_config_path",
]
