import hashlib
import json
from pathlib import Path


FIXTURE = Path(__file__).parents[2] / "contracts" / "archive_dataset_sample.json"
SCHEMA = Path(__file__).parents[2] / "contracts" / "archive_dataset.schema.json"


def _digest(bars):
    rows = []
    for bar in sorted(bars, key=lambda item: item["time"]):
        rows.append([
            int(__import__("pandas").Timestamp(bar["time"]).timestamp()),
            bar["open"], bar["high"], bar["low"], bar["close"],
            bar.get("tick_volume", 0), bar.get("spread", 0), bar.get("real_volume", 0),
        ])
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_sanitized_archive_contract_fixture_is_reproducible():
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["contract_revision"]["const"] == document["contract_revision"]
    assert schema["properties"]["digest_algorithm"]["const"] == document["digest_algorithm"]
    assert document["contract_revision"] == "archive.dataset.v1"
    assert document["logical_symbol"] == "BTC"
    assert document["source_symbol"] == "BTCUSD"
    assert document["coverage"]["closed_bars"] is True
    assert document["bars"] == sorted(document["bars"], key=lambda item: item["time"])
    assert document["archive_digest"].startswith("sha256:")
    assert document["session_evidence"]["session_generation"] >= 1
    assert len(document["provenance"]["source_commit"]) == 40


def test_digest_algorithm_is_defined_by_contract():
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    digest = _digest(document["bars"])
    assert digest == document["archive_digest"]
