"""Contracts for the shared infrastructure implementation boundaries."""

import ast
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_removed_legacy_modules_are_absent():
    removed = (
        "api_gateway.py",
        "tick_service.py",
        "history_service.py",
        "account_service.py",
        "history_repository.py",
        "history_query_service.py",
        "metrics.py",
        "chart_service.py",
        "trade_query/models.py",
        "trade_query/errors.py",
    )
    for relative_path in removed:
        assert not (SERVICE_ROOT / relative_path).exists(), relative_path
    assert not list((SERVICE_ROOT / "core").glob("*.py"))


def test_tests_do_not_reference_removed_legacy_file_paths():
    removed_tokens = (
        "service/api_gateway.py",
        "service/tick_service.py",
        "service/history_service.py",
        "service/account_service.py",
        "service/history_repository.py",
        "service/history_query_service.py",
        "service/metrics.py",
        "service/chart_service.py",
        "service/core/",
        "service/trade_query/models.py",
        "service/trade_query/errors.py",
    )
    violations = []
    for path in (SERVICE_ROOT / "tests").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        source = path.read_text(encoding="utf-8")
        for token in removed_tokens:
            if token in source:
                violations.append(f"{path.relative_to(SERVICE_ROOT)}: {token}")
    assert not violations, "\n".join(violations)


def test_real_implementations_live_under_infrastructure():
    expected_definitions = {
        "infrastructure/mt5/client.py": {"MT5Client"},
        "infrastructure/mt5/symbol_resolver.py": {"SymbolResolver"},
        "infrastructure/ipc/tick_protocol.py": {"TickPublisher", "_ClientChannel"},
        "infrastructure/status/component_status.py": {
            "atomic_write_status",
            "read_status",
        },
    }
    for relative_path, expected in expected_definitions.items():
        tree = ast.parse(
            (SERVICE_ROOT / relative_path).read_text(encoding="utf-8")
        )
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        assert expected <= definitions, relative_path


def test_mt5_client_documents_process_local_ownership():
    source = (SERVICE_ROOT / "infrastructure/mt5/client.py").read_text(
        encoding="utf-8"
    )
    assert "process-local" in source
    assert "must never be shared between operating-system\nprocesses" in source
