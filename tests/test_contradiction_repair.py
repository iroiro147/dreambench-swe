"""Stdlib tests for src/dream_memory/contradiction_repair.py.

Runs with plain python3 (no pytest required):
    python3 tests/test_contradiction_repair.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dream_memory import contradiction_repair  # noqa: E402
from dream_memory.contradiction_repair import ContradictionRepair, default_judge  # noqa: E402
from dream_memory.memory_store import MemoryStore  # noqa: E402
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType, Provenance, new_memory  # noqa: E402


def _memory(content, memory_type=MemoryType.SEMANTIC_PROJECT, **kwargs):
    return new_memory(
        content,
        memory_type,
        write_reason=kwargs.pop("write_reason", "test observation"),
        provenance=kwargs.pop("provenance", Provenance(trajectory_ids=["traj_test"])),
        **kwargs,
    )


def test_contradiction_supersedes_older_memory_without_deleting_evidence():
    store = MemoryStore()
    old = store.add(_memory(
        "This repo uses Jest.",
        provenance=Provenance(trajectory_ids=["traj_old"], file_paths=["package.json"]),
        repo_scope="repo-a",
        file_scope=["package.json"],
        confidence=0.6,
        retrieval_tags=["testing"],
    ))
    candidate = _memory(
        "This repo uses Vitest.",
        provenance=Provenance(trajectory_ids=["traj_new"], file_paths=["package.json"]),
        repo_scope="repo-a",
        file_scope=["package.json"],
        confidence=0.8,
        retrieval_tags=["testing"],
    )
    original_provenance = old.provenance
    original_content = old.content

    repaired = contradiction_repair.apply([candidate], store)

    contradiction_records = [
        memory for memory in repaired
        if memory.type == MemoryType.CONTRADICTION
    ]
    assert repaired[0] is candidate
    assert len(contradiction_records) == 1

    record = contradiction_records[0]
    assert store.get(old.id) is old
    assert old in store.get_all(include_inactive=True)
    assert old not in store.get_all(include_inactive=False)
    assert old.status == MemoryStatus.SUPERSEDED
    assert old.content == original_content
    assert old.provenance is original_provenance
    assert old.provenance.trajectory_ids == ["traj_old"]
    assert old.provenance.file_paths == ["package.json"]

    assert candidate.status == MemoryStatus.ACTIVE
    assert candidate.id in old.superseded_by
    assert old.id in candidate.supersedes
    assert candidate.id in old.contradicts
    assert old.id in candidate.contradicts

    assert record.status == MemoryStatus.ACTIVE
    assert record.contradicts == [old.id, candidate.id]
    assert record.provenance.trajectory_ids == ["traj_old", "traj_new"]
    assert record.provenance.file_paths == ["package.json"]
    assert "conflicting uses: jest vs vitest" in record.content.lower()


def test_non_conflicting_candidate_produces_no_repair_record():
    store = MemoryStore()
    old = store.add(_memory(
        "This repo uses Vitest.",
        provenance=Provenance(trajectory_ids=["traj_existing"], file_paths=["package.json"]),
        repo_scope="repo-a",
        file_scope=["package.json"],
        confidence=0.8,
    ))
    candidate = _memory(
        "API handlers live in src/server/routes.",
        provenance=Provenance(trajectory_ids=["traj_routes"], file_paths=["src/server/routes.py"]),
        repo_scope="repo-a",
        file_scope=["src/server/routes.py"],
        confidence=0.9,
    )

    repaired = ContradictionRepair().apply([candidate], store)

    assert repaired == [candidate]
    assert old.status == MemoryStatus.ACTIVE
    assert candidate.status == MemoryStatus.ACTIVE
    assert old.contradicts == []
    assert candidate.contradicts == []
    assert old.superseded_by == []
    assert candidate.supersedes == []


def test_episodic_candidate_is_returned_unchanged_without_repair():
    store = MemoryStore()
    old = store.add(_memory(
        "This repo requires python.",
        provenance=Provenance(trajectory_ids=["traj_old"]),
        repo_scope="repo-a",
        confidence=0.9,
    ))
    candidate = _memory(
        "This repo requires node.",
        MemoryType.EPISODIC,
        provenance=Provenance(trajectory_ids=["traj_raw"]),
        repo_scope="repo-a",
        confidence=1.0,
    )
    original_status = candidate.status
    original_confidence = candidate.confidence
    original_risk = candidate.risk_score

    def adversarial_judge(existing, proposed):
        return {
            "contradicts": True,
            "reason": "test judge should not see episodic evidence",
            "newer_supersedes": True,
            "requires_review": False,
        }

    repaired = ContradictionRepair(adversarial_judge).apply([candidate], store)

    assert repaired == [candidate]
    assert repaired[0] is candidate
    assert candidate.status == original_status == MemoryStatus.ACTIVE
    assert candidate.confidence == original_confidence
    assert candidate.risk_score == original_risk
    assert candidate.supersedes == []
    assert candidate.contradicts == []
    assert old.status == MemoryStatus.ACTIVE
    assert old.superseded_by == []
    assert old.contradicts == []


def test_public_entrypoints_return_documented_shapes():
    existing = _memory(
        "This repo requires python.",
        provenance=Provenance(trajectory_ids=["traj_python"]),
        repo_scope="repo-a",
        confidence=0.7,
    )
    candidate = _memory(
        "This repo requires node.",
        provenance=Provenance(trajectory_ids=["traj_node"]),
        repo_scope="repo-a",
        confidence=0.9,
    )

    decision = default_judge(existing, candidate)
    assert isinstance(decision, dict)
    assert decision["contradicts"] is True
    assert decision["reason"] == "conflicting requires: python vs node"
    assert decision["newer_supersedes"] is True
    assert decision["requires_review"] is False

    store_for_function = MemoryStore([existing])
    function_result = contradiction_repair.apply([candidate], store_for_function)
    assert isinstance(function_result, list)
    assert all(isinstance(memory, MemoryItem) for memory in function_result)
    assert [memory.type for memory in function_result] == [
        MemoryType.SEMANTIC_PROJECT,
        MemoryType.CONTRADICTION,
    ]

    class_candidate = _memory(
        "Use stdlib assertions for tests.",
        MemoryType.PROCEDURAL,
        provenance=Provenance(trajectory_ids=["traj_stdlib"]),
    )
    class_result = ContradictionRepair().apply([class_candidate], MemoryStore())
    assert isinstance(class_result, list)
    assert class_result == [class_candidate]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\033[32m{passed}/{len(fns)} passed\033[0m")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
