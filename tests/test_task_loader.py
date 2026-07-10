"""Stdlib tests for src/benchmarks/task_loader.py."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = ROOT / "src" / "benchmarks" / "task_loader.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("task_loader", LOADER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generate_sequences_structure() -> None:
    loader = _load_module()
    sequences = loader.generate_task_sequences(sequences_per_type=3)
    assert len(sequences) == 15

    seq_types = [seq_type.value for seq_type in loader.SequenceType]
    counts = {seq_type: 0 for seq_type in seq_types}
    all_task_ids = set()

    for sequence in sequences:
        assert sequence.seq_type.value in counts
        counts[sequence.seq_type.value] += 1
        assert len(sequence.tasks) == 4
        assert [task.session_index for task in sequence.tasks] == [1, 2, 3, 4]

        for task in sequence.tasks:
            assert task.id not in all_task_ids
            all_task_ids.add(task.id)
            assert task.sequence_id == sequence.id
            assert task.seq_type == sequence.seq_type
            assert task.prompt.strip()
            assert task.injected_memory_event["event_type"]
            assert task.injected_memory_event["memory_type"]
            assert task.expected_behavior.strip()
            assert task.oracle_check["type"] == "synthetic_binary_checklist"
            assert task.oracle_check["must_do"]
            assert task.oracle_check["must_avoid"]
            assert task.oracle_check["evidence_required"]

    assert counts == {seq_type: 3 for seq_type in seq_types}
    assert len(loader.flatten_tasks(sequences)) == 60


def test_task_dict_contract() -> None:
    loader = _load_module()
    sequences = loader.generate_task_sequences(sequences_per_type=1)
    required = {
        "id",
        "sequence_id",
        "seq_type",
        "session_index",
        "prompt",
        "injected_memory_event",
        "expected_behavior",
        "oracle_check",
    }

    event_types = set()
    for task in loader.flatten_tasks(sequences):
        payload = task.to_dict()
        assert set(payload) == required
        assert isinstance(payload["seq_type"], str)
        event_types.add(payload["injected_memory_event"]["event_type"])

    assert "contradiction" in event_types
    assert "staleness" in event_types
    assert "human-feedback" in event_types


def test_write_json_fixtures() -> None:
    loader = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        paths = loader.write_json_fixtures(tmp, sequences_per_type=2)
        assert len(paths) == 11

        manifest_path = Path(tmp) / "manifest.json"
        assert manifest_path in paths
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["fixture_version"] == "dreambench-swe-v0"
        assert manifest["sequence_count"] == 10
        assert manifest["task_count"] == 40
        assert len(manifest["files"]) == 10

        for name in manifest["files"]:
            fixture = json.loads((Path(tmp) / name).read_text(encoding="utf-8"))
            assert fixture["fixture_version"] == "dreambench-swe-v0"
            assert fixture["sequence"]["id"]
            assert fixture["sequence"]["seq_type"] in manifest["sequence_types"]
            assert len(fixture["tasks"]) == 4
            for task in fixture["tasks"]:
                assert task["id"]
                assert task["sequence_id"] == fixture["sequence"]["id"]
                assert task["seq_type"] == fixture["sequence"]["seq_type"]
                assert isinstance(task["session_index"], int)


def main() -> None:
    test_generate_sequences_structure()
    test_task_dict_contract()
    test_write_json_fixtures()
    print("test_task_loader.py: all tests passed")


if __name__ == "__main__":
    main()
