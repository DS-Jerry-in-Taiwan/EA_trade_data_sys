"""Static acceptance checks for the documented deployment boundaries."""

import ast
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gateway_is_only_published_trade_data_endpoint():
    compose = yaml.safe_load(
        (REPO_ROOT / "mt5docker" / "compose.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    assert "8001" not in " ".join(map(str, services["mt5-server"].get("ports", [])))
    runner_ports = " ".join(map(str, services["trade-data-service"]["ports"]))
    assert "8090:8090" in runner_ports
    assert "8001" not in runner_ports


def test_runner_execs_supervisor_after_installing_bind_mounted_requirements():
    runner = (REPO_ROOT / "mt5docker" / "start_runner.sh").read_text(encoding="utf-8")

    assert "/app/mt5docker/requirements.txt" in runner
    assert "exec python3 -u -m service.runtime.supervisor" in runner
    assert "nohup" not in runner
    assert "tail -f /dev/null" not in runner


def test_image_installs_pinned_pymt5linux_into_wine_python():
    dockerfile = (REPO_ROOT / "mt5docker" / "Dockerfile").read_text(encoding="utf-8")
    requirements = (REPO_ROOT / "mt5docker" / "wine-requirements.txt").read_text(
        encoding="utf-8"
    )

    assert "COPY wine-requirements.txt /tmp/wine-requirements.txt" in dockerfile
    assert "wine C:/Python/python.exe -m pip install --no-cache-dir -r" in dockerfile
    assert 'import MetaTrader5, pymt5linux, rpyc' in dockerfile
    assert "pymt5linux==1.0" in requirements
    assert "MetaTrader5==5.0.6231" in requirements
    assert "rpyc==6.0.2" in requirements
    assert "numpy==2.5.3" in requirements


def test_supervisor_owns_exactly_three_application_processes():
    source = (REPO_ROOT / "service" / "runtime" / "supervisor.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    assignment = next(
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SERVICE_ENTRYPOINTS"
                for target in node.targets)
    )
    scripts = [item.elts[1].value for item in assignment.value.elts]

    assert scripts == [
        "service.entrypoints.tick_worker",
        "service.entrypoints.history_worker",
        "service.entrypoints.api_gateway",
    ]


def test_gateway_consumes_tick_ipc_and_has_no_legacy_tick_fetcher():
    source = (REPO_ROOT / "service" / "entrypoints" / "api_gateway.py").read_text(encoding="utf-8")

    assert "TickConsumer" in source
    assert "TickFetcher" not in source
    assert "symbol_info_tick" not in source


def test_socketio_unsubscribe_leaves_the_symbol_room():
    source = (REPO_ROOT / "service" / "gateway" / "websocket.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    handler = next(
        node for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "handle_unsubscribe"
    )
    calls = [
        node for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "leave_room"
    ]

    assert len(calls) == 1, "unsubscribe must remove the client from its symbol room"


def test_history_query_contract_identifies_persisted_storage():
    source = (REPO_ROOT / "service" / "gateway" / "routes" / "market_data.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    handler = next(
        node for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "query_rates_by_range"
    )
    handler_source = ast.get_source_segment(source, handler)

    assert "history_query_service.get_rates" in handler_source
    assert '"source": "history_storage"' in handler_source
    assert "copy_rates" not in handler_source
