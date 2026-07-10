"""Regression tests for the B7 task-tracker baseline."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from benchmarks.baselines import (  # noqa: E402
    BASELINE_REGISTRY,
    DreamForgePolicy,
    TaskTrackerPolicy,
    create_policy,
)
from dream_memory.schemas import MemoryType  # noqa: E402


def _episode(task_id: str, *, sequence_id: str = "tracker-seq") -> dict:
    return {
        "trajectory_id": f"traj-{task_id}",
        "task_id": task_id,
        "benchmark_task_id": task_id,
        "sequence_id": sequence_id,
        "repo_scope": sequence_id,
        "file_paths": [f"experiments/data/tasks/{sequence_id}.json"],
        "repo_commit": "test-commit",
        "prompt": "Update the tracker.\nTODO: keep migration notes visible\nNEXT: run the focused check",
        "actions": ["patched parser", "added regression test"],
        "observations": [
            "DONE: parser now preserves explicit TODO rows",
            "Decision: store tracker state as one active sequence memory",
        ],
        "todo": ["document B7 behavior"],
        "done": ["updated implementation"],
        "next": ["run full pytest"],
        "outcome": "success",
    }


def test_b7_registry_uses_task_tracker_not_dreamforge() -> None:
    assert BASELINE_REGISTRY["B7"].policy_cls is TaskTrackerPolicy
    assert BASELINE_REGISTRY["B7"].policy_cls is not DreamForgePolicy

    policy = create_policy("B7")

    assert isinstance(policy, TaskTrackerPolicy)
    assert not isinstance(policy, DreamForgePolicy)
    assert policy.label == "TaskTracker"


def test_task_tracker_records_and_reads_sequence_scoped_state() -> None:
    policy = create_policy("B7")
    policy.write(_episode("tracker-seq-s01"))
    policy.write(_episode("tracker-seq-s02"))

    items = policy.memory_items()
    assert len(items) == 1
    assert items[0].type == MemoryType.EPISODIC
    assert items[0].repo_scope == "tracker-seq"
    assert items[0].task_scope == ["tracker-seq"]
    assert "task_tracker" in items[0].retrieval_tags

    context = policy.read({
        "id": "tracker-seq-s03",
        "prompt": "Continue with the remaining tracker work.",
        "files": ["experiments/data/tasks/tracker-seq.json"],
    })

    assert len(context) == 1
    content = context[0]["content"]
    assert "Completed tasks: tracker-seq-s01; tracker-seq-s02." in content
    assert "Completed subtasks: patched parser; added regression test." in content
    assert "TODO: document B7 behavior; keep migration notes visible." in content
    assert "Done: updated implementation; parser now preserves explicit TODO rows." in content
    assert "Next: run full pytest; run the focused check." in content
    assert "Decisions: store tracker state as one active sequence memory." in content
    assert context[0]["repo_scope"] == "tracker-seq"
    assert policy.last_retrieval_decisions[0]["reason"] == "task_tracker_scope"

    assert policy.read({"id": "other-seq-s01", "prompt": "Unrelated task."}) == []
