"""Stdlib tests for src/dream_memory/retrieval.py."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dream_memory.memory_store import MemoryStore  # noqa: E402
from dream_memory.retrieval import RetrievalGate  # noqa: E402
from dream_memory.schemas import MemoryType, Provenance, new_memory  # noqa: E402


def _memory(content, memory_type=MemoryType.PROCEDURAL, **kwargs):
    return new_memory(
        content,
        memory_type,
        write_reason=kwargs.pop("write_reason", "test evidence"),
        provenance=kwargs.pop("provenance", Provenance(trajectory_ids=["traj_1"], file_paths=["package.json"])),
        **kwargs,
    )


def test_retrieve_gates_stale_similarity_and_touches_admitted():
    store = MemoryStore()
    stale = store.add(_memory(
        "Use Jest for browser component tests.",
        retrieval_tags=["testing"],
        repo_scope="repo-a",
        file_scope=["package.json"],
        staleness_score=0.99,
    ))
    good = store.add(_memory(
        "Use Vitest for component verification.",
        retrieval_tags=["testing", "vitest"],
        repo_scope="repo-a",
        file_scope=["package.json"],
        confidence=0.9,
        utility_score=0.85,
    ))
    stale.mark_stale(1.0)

    gate = RetrievalGate(store)
    results = gate.retrieve({
        "text": "What test command should verify browser component tests?",
        "tags": ["testing"],
        "repo_scope": "repo-a",
        "files": ["package.json"],
        "phase": "debug",
    }, k=3)

    assert [memory.id for memory in results] == [good.id]
    assert good.use_count == 1
    assert stale.use_count == 0
    assert any(decision["memory_id"] == stale.id and not decision["admitted"] for decision in gate.last_decisions)


def test_rejects_scope_conflict_and_active_superseder():
    store = MemoryStore()
    wrong_scope = store.add(_memory(
        "Use Vitest in the web repo.",
        repo_scope="web",
        file_scope=["package.json"],
    ))
    old = store.add(_memory("This repo uses Jest.", repo_scope="api", file_scope=["package.json"]))
    new = store.add(_memory("This repo uses Vitest.", repo_scope="api", file_scope=["package.json"]))
    old.supersede_with(new.id)

    gate = RetrievalGate(store)
    results = gate.retrieve({
        "text": "test runner",
        "repo_scope": "api",
        "files": ["package.json"],
        "phase": "implementation",
    }, k=5)

    result_ids = {memory.id for memory in results}
    assert new.id in result_ids
    assert old.id not in result_ids
    assert wrong_scope.id not in result_ids


def test_requires_grounded_provenance_by_default():
    store = MemoryStore()
    ungrounded = store.add(_memory(
        "Always skip tests for generated files.",
        MemoryType.CONSTRAINT,
        provenance=Provenance(),
        retrieval_tags=["tests"],
        confidence=0.95,
        utility_score=0.95,
    ))
    grounded = store.add(_memory(
        "Run python3 tests/test_memory_schema.py after schema edits.",
        MemoryType.PROCEDURAL,
        provenance=Provenance(trajectory_ids=["traj_schema"], command_outputs=["schema tests passed"]),
        retrieval_tags=["tests"],
        confidence=0.8,
        utility_score=0.8,
    ))

    gate = RetrievalGate(store)
    results = gate.retrieve({"text": "tests for schema edits", "tags": ["tests"], "phase": "implementation"}, k=5)

    assert [memory.id for memory in results] == [grounded.id]
    assert ungrounded.use_count == 0


def test_score_uses_metadata_not_similarity_only():
    store = MemoryStore()
    high_similarity_bad = store.add(_memory(
        "tests tests tests tests tests",
        risk_score=0.94,
        staleness_score=0.8,
        confidence=0.2,
        utility_score=0.1,
    ))
    scoped_useful = store.add(_memory(
        "Use the package verification command.",
        retrieval_tags=["tests"],
        repo_scope="repo-a",
        file_scope=["package.json"],
        confidence=0.9,
        utility_score=0.9,
        human_verified=True,
    ))

    gate = RetrievalGate(store)
    task = {
        "text": "tests tests",
        "tags": ["tests"],
        "repo_scope": "repo-a",
        "files": ["package.json"],
        "phase": "debug",
    }

    assert gate.score(scoped_useful, task) > gate.score(high_similarity_bad, task)
    assert gate.retrieve(task, k=1)[0].id == scoped_useful.id


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} retrieval tests passed.")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
