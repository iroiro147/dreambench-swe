"""Stdlib tests for DreamForge consolidation and sleep pipeline."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dream_memory.consolidation import extract, sleep_pipeline  # noqa: E402
from dream_memory.memory_store import MemoryStore  # noqa: E402
from dream_memory.schemas import MemoryStatus, MemoryType, Provenance, new_memory  # noqa: E402


def test_default_extractor_creates_grounded_failure_memory():
    raw_episode = {
        "trajectory_id": "traj_fail",
        "task_id": "task_1",
        "repo_commit": "abc123",
        "file_paths": ["tests/test_example.py"],
        "prompt": "Fix the failing test.",
        "actions": ["python3 tests/test_example.py"],
        "observations": ["AssertionError: expected 1 got 2"],
        "successful_recovery": "Changed parser branch and reran python3 tests/test_example.py successfully.",
        "outcome": "failure",
    }

    memories = extract([raw_episode])
    by_type = {memory.type for memory in memories}

    assert MemoryType.EPISODIC in by_type
    assert MemoryType.FAILURE in by_type
    failure = [memory for memory in memories if memory.type == MemoryType.FAILURE][0]
    assert failure.status == MemoryStatus.ACTIVE
    assert failure.provenance.trajectory_ids == ["traj_fail"]
    assert failure.provenance.task_ids == ["task_1"]
    assert "AssertionError" in failure.content
    assert raw_episode["observations"] == ["AssertionError: expected 1 got 2"]


def test_sleep_pipeline_repairs_contradiction_and_writes():
    store = MemoryStore()
    old = store.add(new_memory(
        "This repo uses Jest.",
        MemoryType.SEMANTIC_PROJECT,
        write_reason="old package.json observation",
        provenance=Provenance(trajectory_ids=["traj_old"], file_paths=["package.json"]),
        repo_scope="repo-a",
        file_scope=["package.json"],
        confidence=0.6,
        utility_score=0.6,
        retrieval_tags=["testing"],
    ))
    raw_episode = {
        "trajectory_id": "traj_new",
        "task_id": "task_2",
        "repo_scope": "repo-a",
        "file_paths": ["package.json"],
        "observations": ["package.json now shows test runner is vitest"],
        "actions": ["python3 tests/test_memory_schema.py"],
        "outcome": "success",
    }

    written = sleep_pipeline([raw_episode], store, write=True)

    semantic = [
        memory for memory in written
        if memory.type == MemoryType.SEMANTIC_PROJECT and "vitest" in memory.content.lower()
    ]
    assert semantic
    assert store.get(old.id).status == MemoryStatus.SUPERSEDED
    assert semantic[0].id in store.get(old.id).superseded_by
    assert old.content == "This repo uses Jest."
    assert any(memory.type == MemoryType.CONTRADICTION for memory in written)


def test_injectable_extractor_runs_offline():
    def fake_extractor(payload):
        assert payload["raw_episodes"][0]["trajectory_id"] == "traj_custom"
        return [{
            "content": "Custom offline extraction.",
            "type": "constraint",
            "status": "active",
            "provenance": {"trajectory_ids": ["traj_custom"]},
            "write_reason": "injected deterministic extractor",
            "confidence": 0.9,
            "utility_score": 0.4,
            "risk_score": 0.1,
            "staleness_score": 0.0,
        }]

    memories = extract([{"trajectory_id": "traj_custom", "task_id": "task_custom"}], extractor=fake_extractor)

    assert len(memories) == 1
    assert memories[0].type == MemoryType.CONSTRAINT
    assert memories[0].provenance.trajectory_ids == ["traj_custom"]


def test_default_extractor_uses_sequence_scope_when_repo_scope_missing():
    raw_episode = {
        "trajectory_id": "traj_sequence_scope",
        "task_id": "config-stale-merge-s1",
        "sequence_id": "config-stale-merge",
        "file_paths": ["configly/parser.py"],
        "prompt": "Record the current merge_configs contract.",
        "observations": ["Project fact: merge_configs returns Config."],
        "outcome": "success",
    }

    memories = extract([raw_episode])

    assert memories
    assert all(memory.repo_scope == "config-stale-merge" for memory in memories)


def test_counterfactual_replay_uses_only_trajectory_evidence():
    store = MemoryStore()
    raw_episode = {
        "trajectory_id": "traj_replay",
        "task_id": "task_3",
        "file_paths": ["src/parser.py"],
        "actions": ["edited parser without checking traceback"],
        "observations": ["Traceback: KeyError in parse_config"],
        "outcome": "failure",
    }

    written = sleep_pipeline([raw_episode], store, write=True)
    replay = [memory for memory in written if memory.type == MemoryType.DREAM_ARTIFACT]

    assert replay
    assert replay[0].status == MemoryStatus.REQUIRES_REVIEW
    assert "Traceback: KeyError in parse_config" in replay[0].content
    assert replay[0].provenance.trajectory_ids == ["traj_replay"]


def test_sleep_pipeline_raw_evidence_flag_controls_verbatim_event_retention():
    raw_episode = {
        "trajectory_id": "traj_raw_evidence",
        "task_id": "raw-evidence-seq-s1",
        "sequence_id": "raw-evidence-seq",
        "repo_scope": "raw-evidence-seq",
        "repo_commit": "abc123",
        "file_paths": ["src/parser.py"],
        "prompt": "Record reviewer contract.",
        "observations": ["The public prompt does not contain the private event text."],
        "injected_memory_event": {
            "content": "Use the reviewer supplied literal capsule in follow-up work.",
            "event_type": "human_feedback",
        },
        "outcome": "success",
    }

    disabled_store = MemoryStore()
    disabled = sleep_pipeline(
        [raw_episode],
        disabled_store,
        write=True,
        replay_judge=None,
        repair_judge=None,
        enable_raw_evidence=False,
    )
    assert not any("literal capsule" in memory.content for memory in disabled)

    enabled_store = MemoryStore()
    enabled = sleep_pipeline(
        [raw_episode],
        enabled_store,
        write=True,
        replay_judge=None,
        repair_judge=None,
        enable_raw_evidence=True,
    )

    raw = [
        memory for memory in enabled
        if memory.type == MemoryType.EPISODIC and "raw-evidence" in memory.retrieval_tags
    ]
    assert len(raw) == 1
    assert "Use the reviewer supplied literal capsule in follow-up work." in raw[0].content
    assert raw[0].utility_score == 0.6
    assert raw[0].confidence == 1.0
    assert raw[0].risk_score == 0.1
    assert raw[0].repo_scope == "raw-evidence-seq"
    assert raw[0].file_scope == ["src/parser.py"]
    assert raw[0].task_scope == ["raw-evidence-seq-s1"]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} consolidation tests passed.")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
