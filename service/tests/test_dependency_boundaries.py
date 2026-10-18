"""Static contracts for the modular trade-data package dependency graph."""

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "config",
    "domain",
    "etl",
    "gateway",
    "realtime",
    "history",
    "trade_query",
    "infrastructure",
    "runtime",
    "entrypoints",
}

PRODUCTION_PACKAGES = PACKAGES
REMOVED_LEGACY_IMPORTS = {
    "service.metrics",
    "service.api_gateway",
    "service.account_service",
    "service.history_service",
    "service.history_repository",
    "service.history_query_service",
    "service.tick_service",
}

# A package may always import itself. Entrypoints are composition roots, while
# runtime is deliberately limited to launching those entrypoints.
ALLOWED_DEPENDENCIES = {
    "config": set(),
    "domain": {"domain"},
    "etl": {"domain", "etl"},
    "gateway": {"realtime", "history", "trade_query", "domain"},
    "realtime": {"config", "infrastructure"},
    "history": {"config", "infrastructure"},
    "trade_query": {"config", "infrastructure", "domain"},
    "infrastructure": set(),
    "runtime": {"config", "entrypoints"},
    "entrypoints": {
        "config",
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

FRAMEWORK_OR_STORAGE_ROOTS = {
    "flask",
    "flask_cors",
    "flask_socketio",
    "matplotlib",
    "numpy",
    "pandas",
    "prometheus_client",
    "psycopg2",
    "requests",
    "yaml",
    "MetaTrader5",
    "pymt5linux",
    "rpyc",
    "sqlite3",
}
GATEWAY_FORBIDDEN_CALLS = {
    "copy_rates_from_pos",
    "copy_rates_range",
    "read_csv",
    "to_csv",
    "to_parquet",
    "symbol_info_tick",
}
ETL_FORBIDDEN_IMPORTS = {
    "service.infrastructure.mt5",
    "service.core",
    "MetaTrader5",
    "pymt5linux",
    "rpyc",
}
ETL_FORBIDDEN_NAMES = {
    "MT5Client",
    "MT5Connector",
    "copy_rates_from_pos",
    "copy_rates_range",
    "history_deals_get",
    "initialize",
    "symbol_info_tick",
}


def _python_files(package: str):
    return sorted((SERVICE_ROOT / package).rglob("*.py"))


def _absolute_imports(tree: ast.AST) -> list[str]:
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append(node.module)
    return imports


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _boundary_violations(owner: str, tree: ast.AST) -> list[str]:
    """Return deterministic boundary violations for one package AST."""
    imports = _absolute_imports(tree)
    calls = _called_names(tree)
    violations = []
    if owner == "domain":
        for imported in imports:
            root = imported.split(".", 1)[0]
            if root in FRAMEWORK_OR_STORAGE_ROOTS:
                violations.append(f"domain imports framework/storage {imported}")
            if imported.startswith("service.") and not imported.startswith("service.domain"):
                violations.append(f"domain imports feature {imported}")
    elif owner == "gateway":
        forbidden_imports = {
            name for name in imports
            if name.startswith("service.infrastructure.mt5")
            or name in {"MetaTrader5", "pymt5linux", "rpyc"}
            or name in {"service.history.worker", "service.history.repository"}
            or name == "service.realtime.worker"
        }
        violations.extend(f"gateway imports {name}" for name in sorted(forbidden_imports))
        violations.extend(
            f"gateway calls {name}" for name in sorted(calls & GATEWAY_FORBIDDEN_CALLS)
        )
    elif owner == "etl":
        for imported in imports:
            if imported in ETL_FORBIDDEN_IMPORTS or any(
                imported.startswith(f"{prefix}.") for prefix in ETL_FORBIDDEN_IMPORTS
            ):
                violations.append(f"etl imports {imported}")
        violations.extend(
            f"etl calls {name}" for name in sorted(calls & ETL_FORBIDDEN_NAMES)
        )
    elif owner == "feature":
        if any(name.startswith("service.trade_query.models") for name in imports):
            violations.append("feature imports legacy trade models")
        if any(name.startswith("service.trade_query.errors") for name in imports):
            violations.append("feature imports legacy trade errors")
        if any(name.startswith("service.core") for name in imports):
            violations.append("feature imports removed service.core")
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "yaml"
            and node.func.attr == "safe_load"
            for node in ast.walk(tree)
        ):
            violations.append("feature parses YAML directly")
    return violations


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


def test_production_packages_do_not_import_legacy_flat_module_shims():
    violations = []
    for owner in sorted(PRODUCTION_PACKAGES):
        for path in _python_files(owner):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    imported = [node.module] if node.module else []
                for name in imported:
                    if name in REMOVED_LEGACY_IMPORTS or name.startswith("service.core"):
                        violations.append(
                            f"{path.relative_to(SERVICE_ROOT)}:{node.lineno}: "
                            f"production code imports legacy module {name}"
                        )
    assert not violations, "\n".join(violations)


def test_backend_tests_do_not_import_removed_legacy_shims():
    violations = []
    tests_root = SERVICE_ROOT / "tests"
    for path in sorted(tests_root.rglob("*.py")):
        relative = str(path.relative_to(SERVICE_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = [node.module] if node.module else []
            for name in imported:
                if name in REMOVED_LEGACY_IMPORTS or name.startswith("service.core"):
                    violations.append(
                        f"{relative}:{node.lineno}: removed legacy import {name}"
                    )
    assert not violations, "\n".join(violations)


def test_domain_is_independent_of_features_infrastructure_storage_and_frameworks():
    violations = []
    for path in _python_files("domain"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.name}: {violation}"
            for violation in _boundary_violations("domain", tree)
        )
    assert not violations, "\n".join(violations)


def test_gateway_has_no_tick_polling_history_persistence_or_mt5_access():
    violations = []
    for path in _python_files("gateway"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.relative_to(SERVICE_ROOT)}: {violation}"
            for violation in _boundary_violations("gateway", tree)
        )
    assert not violations, "\n".join(violations)


def test_etl_is_readonly_api_consumer_and_cannot_use_mt5_directly():
    violations = []
    for path in _python_files("etl"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.name}: {violation}"
            for violation in _boundary_violations("etl", tree)
        )
    assert not violations, "\n".join(violations)


def test_feature_packages_use_canonical_shared_paths():
    violations = []
    for package in ("realtime", "history", "trade_query", "etl"):
        for path in _python_files(package):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations.extend(
                f"{path.name}: {violation}"
                for violation in _boundary_violations("feature", tree)
            )
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("owner", "source"),
    (
        (
            "domain",
            "from flask import Flask\nfrom service.gateway import app\n",
        ),
        (
            "gateway",
            "from service.infrastructure.mt5.client import MT5Client\n"
            "client.symbol_info_tick('BTC')\nframe.to_csv('history.csv')\n",
        ),
        (
            "etl",
            "from service.infrastructure.mt5.client import MT5Client\n"
            "client = MT5Client()\nclient.history_deals_get()\n",
        ),
        (
            "trade_query",
            "from service.trade_query.models import DealRecord\n"
            "import yaml\nyaml.safe_load('{}')\n",
        ),
    ),
)
def test_synthetic_boundary_fixtures_exercise_forbidden_rules(owner, source):
    tree = ast.parse(source)
    imports = _absolute_imports(tree)
    calls = _called_names(tree)
    rule_owner = "feature" if owner == "trade_query" else owner
    assert _boundary_violations(rule_owner, tree)

    if owner == "domain":
        assert "flask" in {name.split(".", 1)[0] for name in imports}
        assert "service.gateway" in imports
    elif owner == "gateway":
        assert "service.infrastructure.mt5.client" in imports
        assert {"symbol_info_tick", "to_csv"} <= calls
    elif owner == "etl":
        assert "service.infrastructure.mt5.client" in imports
        assert {"MT5Client", "history_deals_get"} <= calls
    else:
        assert "service.trade_query.models" in imports
        assert "yaml" in imports
        assert "safe_load" in calls


def test_legacy_chart_service_is_removed_when_no_gateway_caller_exists():
    assert not (SERVICE_ROOT / "chart_service.py").exists()


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
                node.module == "service.infrastructure.mt5"
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
                alias.name.startswith("service.infrastructure.mt5")
                or alias.name in {"MetaTrader5", "pymt5linux"}
                for alias in node.names
            ):
                violations.append(
                    f"{path.name}:{node.lineno}: directly imports MT5 infrastructure"
                )
    assert not violations, "\n".join(violations)


def test_explicit_reverse_dependencies_are_forbidden_by_policy():
    assert ALLOWED_DEPENDENCIES["gateway"] == {
        "domain",
        "realtime",
        "history",
        "trade_query",
    }
    assert "gateway" not in ALLOWED_DEPENDENCIES["realtime"]
    assert "gateway" not in ALLOWED_DEPENDENCIES["history"]
    assert "gateway" not in ALLOWED_DEPENDENCIES["trade_query"]
    assert ALLOWED_DEPENDENCIES["infrastructure"] == set()
    assert ALLOWED_DEPENDENCIES["runtime"] == {"config", "entrypoints"}


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
