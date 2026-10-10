import ast
import json
from pathlib import Path

from service.realtime.snapshot_store import TickSnapshotStore


SERVICE_ROOT = Path(__file__).resolve().parents[2]


def test_tick_entrypoint_delegates_to_realtime_worker():
    entrypoint_source = (
        SERVICE_ROOT / "entrypoints" / "tick_worker.py"
    ).read_text(encoding="utf-8")
    assert "from service.realtime.worker import TickService" in entrypoint_source


def test_realtime_worker_is_only_symbol_info_tick_owner():
    owners = []
    excluded = {SERVICE_ROOT / "tests"}
    for path in SERVICE_ROOT.rglob("*.py"):
        if any(root in path.parents for root in excluded):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Attribute) and node.attr == "symbol_info_tick"
            for node in ast.walk(tree)
        ):
            owners.append(path.relative_to(SERVICE_ROOT).as_posix())
    # Only the realtime worker owns streaming collection. Execution's
    # on-demand read-only quote is a separate session-bound risk boundary,
    # never a second gateway TickFetcher or stream publisher.
    assert owners == ["execution/mt5_adapter.py", "realtime/worker.py"]


def test_snapshot_store_atomically_replaces_latest_file(tmp_path):
    store = TickSnapshotStore(str(tmp_path))
    store.save({"XAUUSDm": {"bid": 1}})
    store.save({"XAUUSDm": {"bid": 2}})

    assert json.loads((tmp_path / "latest.json").read_text()) == {
        "XAUUSDm": {"bid": 2}
    }
    assert list(tmp_path.glob(".latest-*.json")) == []
