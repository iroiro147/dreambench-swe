"""Stdlib tests for src/benchmarks/baselines.py."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from benchmarks.baselines import (  # noqa: E402
    BASELINE_REGISTRY,
    DreamForgePolicy,
    InstancePolicy,
    SummaryPolicy,
    available_conditions,
    create_policy,
)
from dream_memory.schemas import MemoryType, Provenance, new_memory  # noqa: E402
from dream_memory.trajectory_logger import TrajectoryLogger  # noqa: E402


def _trajectory(sequence_id="convention-learning-01", task_id="convention-learning-01-s01"):
    logger = TrajectoryLogger()
    return logger.record(
        task_id=task_id,
        session_id=f"test-{task_id}",
        model_id="deterministic-stub-agent",
        condition_id="test",
        repo_commit="synthetic-fixture",
        file_diffs=[f"experiments/data/tasks/{sequence_id}.json"],
        final_outcome="success",
        raw_episode={
            "trajectory_id": f"traj-{task_id}",
            "task_id": sequence_id,
            "benchmark_task_id": task_id,
            "session_id": f"test-{task_id}",
            "repo_scope": sequence_id,
            "sequence_id": sequence_id,
            "file_paths": [f"experiments/data/tasks/{sequence_id}.json"],
            "prompt": "Use dataclasses with explicit to_dict methods.",
            "actions": ["deterministic patch"],
            "observations": ["Project code favors dataclasses plus explicit to_dict serialization."],
            "known_constraints": ["Project code favors dataclasses plus explicit to_dict serialization."],
            "injected_memory_event": {
                "event_type": "observation",
                "memory_type": "procedural",
                "content": "Project code favors dataclasses plus explicit to_dict serialization.",
                "confidence": 0.8,
            },
            "outcome": "success",
            "successful_recovery": "Synthetic success.",
        },
    )


def _scoped_memory(content, memory_type):
    return new_memory(
        content=content,
        type=memory_type,
        write_reason="test retrieval fixture",
        provenance=Provenance(
            trajectory_ids=["traj-retrieval"],
            task_ids=["retrieval-seq-s1"],
            file_paths=["src/parser.py"],
        ),
        repo_scope="retrieval-seq",
        file_scope=["src/parser.py"],
        task_scope=["retrieval-seq"],
        confidence=1.0,
        utility_score=1.0,
        risk_score=0.0,
        retrieval_tags=["retrieval-seq", "consolidation", "parser", "pytest"],
    )


def test_registry_has_requested_conditions() -> None:
    assert available_conditions() == ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]
    assert set(BASELINE_REGISTRY) == set(available_conditions())
    assert BASELINE_REGISTRY["B2"].label == "Vector"
    assert BASELINE_REGISTRY["B4"].label == "Summary"
    assert BASELINE_REGISTRY["B7"].label == "TaskTracker"


def test_b0_never_reads_or_writes_memory() -> None:
    policy = create_policy("B0")
    assert policy.read({"id": "task-1", "session_index": 1, "prompt": "Do work."}) == []
    policy.write(_trajectory())
    assert policy.snapshot()["memory_count"] == 0
    assert policy.last_write_count == 0


def test_all_policies_construct_and_expose_contract() -> None:
    for condition in available_conditions():
        policy = create_policy(condition)
        assert hasattr(policy, "read")
        assert hasattr(policy, "write")
        assert policy.name == condition
        assert policy.describe()["policy_class"].endswith("Policy")


def test_summary_policy_uses_injectable_summarizer() -> None:
    policy = SummaryPolicy(
        name="B4",
        label="Summary",
        description="test summary",
        summarizer=lambda episode: "Injected summary marker for dataclasses.",
    )
    policy.write(_trajectory())
    context = policy.read({
        "id": "convention-learning-01-s02",
        "sequence_id": "convention-learning-01",
        "seq_type": "convention-learning",
        "session_index": 2,
        "prompt": "Reuse the dataclasses marker.",
        "files": ["experiments/data/tasks/convention-learning-01.json"],
    })

    assert len(policy.memory_items()) == 1
    assert context
    assert "Injected summary marker" in context[0]["content"]


def test_summary_policy_retrieval_is_sequence_isolated() -> None:
    policy = SummaryPolicy(
        name="B4-isolation",
        label="Summary",
        description="test summary isolation",
        summarizer=lambda episode: "Shared reviewer token EXPORT-vQ7M2-L9Z belongs only to seq-a.",
    )
    policy.write(_trajectory(sequence_id="seq-a", task_id="seq-a-s01"))

    seq_b_context = policy.read({
        "id": "seq-b-s02",
        "sequence_id": "seq-b",
        "session_index": 2,
        "prompt": "Need the shared reviewer token EXPORT-vQ7M2-L9Z for export work.",
        "files": ["experiments/data/tasks/seq-b.json"],
    })
    seq_a_context = policy.read({
        "id": "seq-a-s02",
        "sequence_id": "seq-a",
        "session_index": 2,
        "prompt": "Need the shared reviewer token EXPORT-vQ7M2-L9Z for export work.",
        "files": ["experiments/data/tasks/seq-a.json"],
    })

    assert seq_b_context == []
    assert seq_a_context
    assert seq_a_context[0]["repo_scope"] == "seq-a"


def test_instance_policy_retrieval_score_beats_insert_order() -> None:
    policy = InstancePolicy(
        name="B5-order",
        label="B5-Instance",
        description="score order fixture",
    )
    low_score = policy.store.add(_scoped_memory(
        "Parser CSV note mentions parser only.",
        MemoryType.PROCEDURAL,
    ))
    high_score = policy.store.add(_scoped_memory(
        "Parser CSV row width reviewer marker target exact active strict review.",
        MemoryType.PROCEDURAL,
    ))

    context = policy.read({
        "id": "retrieval-seq-s2",
        "sequence_id": "retrieval-seq",
        "seq_type": "retrieval",
        "prompt": "Parser CSV row width reviewer marker target exact active strict review.",
        "files": ["src/parser.py"],
    })

    assert [item["id"] for item in context[:2]] == [high_score.id, low_score.id]


def test_instance_policy_enforces_read_budget_event_target_with_top_scores() -> None:
    policy = InstancePolicy(
        name="B5-budget",
        label="B5-Instance",
        description="read budget fixture",
    )
    policy.store.add(_scoped_memory(
        "Export CSV marker distractor alpha.",
        MemoryType.PROCEDURAL,
    ))
    second = policy.store.add(_scoped_memory(
        "Export CSV marker active reviewer row width.",
        MemoryType.PROCEDURAL,
    ))
    policy.store.add(_scoped_memory(
        "Export CSV marker distractor beta.",
        MemoryType.PROCEDURAL,
    ))
    first = policy.store.add(_scoped_memory(
        "Export CSV marker active reviewer row width strict review target.",
        MemoryType.PROCEDURAL,
    ))
    policy.store.add(_scoped_memory(
        "Export CSV marker distractor gamma.",
        MemoryType.PROCEDURAL,
    ))

    context = policy.read({
        "id": "retrieval-seq-s2",
        "sequence_id": "retrieval-seq",
        "seq_type": "retrieval",
        "prompt": "Export CSV marker active reviewer row width strict review target.",
        "files": ["src/parser.py"],
        "validation_metadata": {"read_budget_event_target": 2},
    })

    assert [item["id"] for item in context] == [first.id, second.id]


def test_instance_policy_write_prefers_injected_event_scope() -> None:
    policy = InstancePolicy(
        name="B5-event-scope",
        label="B5-Instance",
        description="event scope fixture",
    )

    policy.write({
        "trajectory_id": "traj-event-scope-s01",
        "task_id": "event-scope-s01",
        "benchmark_task_id": "event-scope-s01",
        "sequence_id": "event-scope",
        "repo_scope": "event-scope",
        "file_paths": ["src/touched.py"],
        "symbols": ["TouchedSymbol"],
        "prompt": "Implement the touched-file task.",
        "outcome": "success",
        "injected_memory_event": {
            "event_type": "failure_diagnosis",
            "memory_type": "procedural",
            "content": "Future work belongs to the event-declared scope.",
            "scope": {
                "files": ["src/future.py", "src/shared.py"],
                "symbols": ["FutureSymbol"],
            },
        },
    })

    [memory] = policy.memory_items()
    assert memory.file_scope == ["src/future.py", "src/shared.py"]
    assert memory.symbol_scope == ["FutureSymbol"]


def test_df_dreamforge_writes_real_typed_memories_and_reads_context() -> None:
    # DreamForge-full is condition DF (B7 is now the task-tracker baseline; see B7 registry fix).
    policy = DreamForgePolicy(name="DF", label="DreamForge", description="DreamForge full")
    assert isinstance(policy, DreamForgePolicy)
    policy.write(_trajectory())

    items = policy.memory_items()
    assert items
    assert any(item.type == MemoryType.EPISODIC for item in items)

    context = policy.read({
        "id": "convention-learning-01-s02",
        "sequence_id": "convention-learning-01",
        "seq_type": "convention-learning",
        "session_index": 2,
        "prompt": "Add a matching serializer using the previous dataclasses convention.",
        "files": ["experiments/data/tasks/convention-learning-01.json"],
    })

    assert context
    assert context[0]["kind"] == "memory"
    assert context[0]["repo_scope"] == "convention-learning-01"
    assert policy.last_retrieval_decisions


def test_df_raw_evidence_flag_controls_policy_write_verbatim_event_retention() -> None:
    trajectory = _trajectory(
        sequence_id="raw-evidence-policy",
        task_id="raw-evidence-policy-s01",
    )
    marker = "Project code favors dataclasses plus explicit to_dict serialization."

    typed_only = DreamForgePolicy(name="DF", label="DreamForge", description="typed only")
    typed_only.write(trajectory)
    assert not any(
        "raw-evidence" in item.retrieval_tags and marker in item.content
        for item in typed_only.memory_items()
    )

    hybrid = DreamForgePolicy(
        name="DF-hybrid",
        label="DreamForgeHybrid",
        description="hybrid test",
        enable_raw_evidence=True,
    )
    hybrid.write(trajectory)

    raw = [
        item for item in hybrid.memory_items()
        if item.type == MemoryType.EPISODIC and "raw-evidence" in item.retrieval_tags
    ]
    assert len(raw) == 1
    assert marker in raw[0].content


def test_df_excludes_contradiction_records_from_all_read_paths() -> None:
    task = {
        "id": "retrieval-seq-s2",
        "sequence_id": "retrieval-seq",
        "seq_type": "retrieval",
        "prompt": "Use parser pytest retrieval memory.",
        "files": ["src/parser.py"],
    }

    for flags in (
        {},
        {"enable_retrieval_gate": False},
        {"enable_stale_suppression": False},
    ):
        policy = DreamForgePolicy(
            name="DF-filter",
            label="DreamForge",
            description="contradiction read filter",
            exclude_contradiction_from_read=True,
            **flags,
        )
        good = policy.store.add(_scoped_memory(
            "Use parser pytest retrieval memory.",
            MemoryType.PROCEDURAL,
        ))
        contradiction = policy.store.add(_scoped_memory(
            "Contradiction repair linked old and new parser pytest memories.",
            MemoryType.CONTRADICTION,
        ))

        context = policy.read(task)
        context_types = {item["type"] for item in context}
        decisions = {
            item["memory_id"]: item
            for item in policy.last_retrieval_decisions
        }

        assert good.id in {item["id"] for item in context}
        assert MemoryType.CONTRADICTION.value not in context_types
        assert contradiction.id not in {item["id"] for item in context}
        assert decisions[contradiction.id]["admitted"] is False
        assert decisions[contradiction.id]["reason"] == "type_policy"


def _run_all() -> bool:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} baseline tests passed.")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
