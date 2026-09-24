import ast
import sys
import types
from pathlib import Path

import pytest
import yaml

# Unit tests must not import the real pymt5linux transport.
connection_manager = types.ModuleType('core.connection_manager')
connection_manager.MT5Connector = object
connection_manager.close_mt5_connection = lambda mt5: None
sys.modules.setdefault('core.connection_manager', connection_manager)

from service.account_service import AccountService
from service.history_service import HistoryService
from service.tick_service import TickService


REPO_ROOT = Path(__file__).resolve().parents[2]


class StubMT5Client:
    pass


@pytest.fixture
def service_configs(tmp_path):
    tick_output = tmp_path / 'ticks'
    history_output = tmp_path / 'history'
    config = {
        'tick_service': {
            'symbols': ['XAUUSDm'],
            'output_dir': str(tick_output),
        },
        'history_service': {
            'symbols': [],
            'data_path': str(history_output),
        },
    }
    config_path = tmp_path / 'settings.yaml'
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return config_path


@pytest.mark.parametrize('service_class', [AccountService, TickService, HistoryService])
def test_service_uses_injected_process_client(service_class, service_configs):
    client = StubMT5Client()

    service = service_class(config_path=str(service_configs), mt5_client=client)

    assert service.mt5_client is client


def test_gateway_has_one_mt5_client_construction_at_composition_root():
    source = (REPO_ROOT / 'service' / 'api_gateway.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent
    constructions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'MT5Client'
    ]

    assert len(constructions) == 1
    assert isinstance(constructions[0].parent, ast.Assign)


def test_query_route_does_not_construct_an_mt5_client():
    source = (REPO_ROOT / 'service' / 'api_gateway.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    query_handler = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'query_rates_by_range'
    )

    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'MT5Client'
        for node in ast.walk(query_handler)
    )
