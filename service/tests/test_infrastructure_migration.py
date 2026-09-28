"""Contracts for the shared infrastructure implementation boundaries."""

import ast
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_modules_are_thin_compatibility_layers():
    limits = {
        "core/mt5_client.py": 40,
        "core/symbol_resolver.py": 15,
        "core/tick_ipc.py": 30,
        "core/component_status.py": 25,
        "metrics.py": 10,
    }
    for relative_path, max_lines in limits.items():
        source = (SERVICE_ROOT / relative_path).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= max_lines, relative_path


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
