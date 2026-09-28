"""Static contracts for the modular trade-data package dependency graph."""

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "gateway",
    "realtime",
    "history",
    "trade_query",
    "infrastructure",
    "runtime",
    "entrypoints",
}

# A package may always import itself. Entrypoints are composition roots, while
# runtime is deliberately limited to launching those entrypoints.
ALLOWED_DEPENDENCIES = {
    "gateway": {"realtime", "history", "trade_query"},
    "realtime": {"infrastructure"},
    "history": {"infrastructure"},
    "trade_query": {"infrastructure"},
    "infrastructure": set(),
    "runtime": {"entrypoints"},
    "entrypoints": {
        "gateway",
        "realtime",
        "history",
        "trade_query",
        "infrastructure",
    },
}

IMPORTABLE_MODULES = (
    "service.gateway.app",
    "service.gateway.auth",
    "service.gateway.websocket",
    "service.gateway.routes.market_data",
    "service.gateway.routes.trade_query",
    "service.gateway.routes.health",
    "service.gateway.routes.metrics",
    "service.realtime.worker",
    "service.realtime.publisher",
    "service.realtime.consumer",
    "service.realtime.snapshot_store",
    "service.history.worker",
    "service.history.repository",
    "service.history.query_service",
    "service.trade_query.account_service",
    "service.infrastructure.mt5.client",
    "service.infrastructure.mt5.symbol_resolver",
    "service.infrastructure.ipc.tick_protocol",
    "service.infrastructure.status.component_status",
    "service.infrastructure.observability.metrics",
    "service.runtime.supervisor",
    "service.entrypoints.api_gateway",
    "service.entrypoints.tick_worker",
    "service.entrypoints.history_worker",
)


def _python_files(package: str):
    return sorted((SERVICE_ROOT / package).rglob("*.py"))


def _service_dependencies(node: ast.AST, module_name: str) -> set[str]:
    """Return modular package targets for absolute and relative imports."""
    targets = set()
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0:
        names = [node.module] if node.module else []
        if node.module == "service":
            names.extend(f"service.{alias.name}" for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        package = module_name.rpartition(".")[0]
        relative_name = "." * node.level + (node.module or "")
        resolved = importlib.util.resolve_name(relative_name, package)
        names = [resolved]
        if not node.module:
            names.extend(f"{resolved}.{alias.name}" for alias in node.names)
    else:
        return targets

    for name in names:
        parts = name.split(".")
        if len(parts) >= 2 and parts[0] == "service" and parts[1] in PACKAGES:
            targets.add(parts[1])
    return targets


@pytest.mark.parametrize("module_name", IMPORTABLE_MODULES)
def test_package_skeleton_is_importable(module_name):
    assert importlib.import_module(module_name)


def test_modular_packages_follow_declared_dependency_direction():
    violations = []
    for owner in sorted(PACKAGES):
        allowed = ALLOWED_DEPENDENCIES[owner] | {owner}
        for path in _python_files(owner):
            relative_module = path.relative_to(SERVICE_ROOT).with_suffix("")
            module_parts = relative_module.parts
            if module_parts[-1] == "__init__":
                module_parts = module_parts[:-1]
            module_name = ".".join(("service", *module_parts))
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for dependency in _service_dependencies(node, module_name):
                    if dependency not in allowed:
                        violations.append(
                            f"{path.relative_to(SERVICE_ROOT)}:{node.lineno}: "
                            f"{owner} must not depend on {dependency}"
                        )
    assert not violations, "\n".join(violations)


def test_gateway_does_not_own_an_mt5_tick_poller():
    violations = []
    for path in _python_files("gateway"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name) and node.id == "TickFetcher"
                or isinstance(node, ast.Attribute) and node.attr == "TickFetcher"
                or isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "TickFetcher"
            ):
                violations.append(f"{path.name}:{node.lineno}: uses TickFetcher")
            if isinstance(node, ast.Attribute) and node.attr == "symbol_info_tick":
                violations.append(
                    f"{path.name}:{node.lineno}: directly polls symbol_info_tick"
                )
            if isinstance(node, ast.ImportFrom) and node.module and (
                node.module in {"service.infrastructure.mt5", "service.core.mt5_client"}
                or node.module.startswith("service.infrastructure.mt5.")
                or node.module in {"MetaTrader5", "pymt5linux"}
                or (node.module == "service.infrastructure" and any(
                    alias.name == "mt5" for alias in node.names
                ))
            ):
                violations.append(
                    f"{path.name}:{node.lineno}: directly imports MT5 infrastructure"
                )
            if isinstance(node, ast.Import) and any(
                alias.name == "service.core.mt5_client"
                or alias.name.startswith("service.infrastructure.mt5")
                or alias.name in {"MetaTrader5", "pymt5linux"}
                for alias in node.names
            ):
                violations.append(
                    f"{path.name}:{node.lineno}: directly imports MT5 infrastructure"
                )
    assert not violations, "\n".join(violations)


def test_explicit_reverse_dependencies_are_forbidden_by_policy():
    assert ALLOWED_DEPENDENCIES["gateway"] == {
        "realtime",
        "history",
        "trade_query",
    }
    assert "gateway" not in ALLOWED_DEPENDENCIES["realtime"]
    assert "gateway" not in ALLOWED_DEPENDENCIES["history"]
    assert "gateway" not in ALLOWED_DEPENDENCIES["trade_query"]
    assert ALLOWED_DEPENDENCIES["infrastructure"] == set()
    assert ALLOWED_DEPENDENCIES["runtime"] == {"entrypoints"}


@pytest.mark.parametrize(
    ("source", "module_name", "expected"),
    (
        ("from . import auth", "service.gateway.app", {"gateway"}),
        ("from .. import auth", "service.gateway.routes.health", {"gateway"}),
        ("from ...realtime import consumer", "service.gateway.routes.health", {"realtime"}),
        ("from service import history", "service.gateway.app", {"history"}),
    ),
)
def test_dependency_parser_resolves_nested_relative_imports(
    source, module_name, expected
):
    node = ast.parse(source).body[0]
    assert _service_dependencies(node, module_name) == expected
