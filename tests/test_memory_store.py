"""Stdlib tests for src/dream_memory/memory_store.py."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dream_memory.memory_store import MemoryStore  # noqa: E402
from dream_memory.schemas import MemoryStatus, MemoryType, Provenance, new_memory  # noqa: E402


def _memory(content, memory_type=MemoryType.SEMANTIC_PROJECT, **kwargs):
    return new_memory(
        content,
        memory_type,
        write_reason=kwargs.pop("write_reason", "test evidence"),
        provenance=kwargs.pop("provenance", Provenance(trajectory_ids=["traj_1"])),
        **kwargs,
    )


def test_add_get_update_and_no_physical_delete():
    store = MemoryStore(namespace="condition-a")
    memory = store.add(_memory("API handlers live in src/server/routes.", confidence=0.6))

    assert len(store) == 1
    assert store.get(memory.id) is memory

    store.update(memory.id, {"confidence": 0.9}, retrieval_tags=["routes"])
    assert store.get(memory.id).confidence == 0.9
    assert store.get(memory.id).retrieval_tags == ["routes"]

    store.update(memory.id, status="deleted")
    assert store.get(memory.id).status == MemoryStatus.DELETED
    assert store.get(memory.id).content == "API handlers live in src/server/routes."
    assert store.get_all(include_inactive=False) == []

    assert not hasattr(store, "delete")


def test_soft_delete_preserves_record_and_provenance():
    store = MemoryStore()
    memory = store.add(_memory(
        "Retired reviewer preference.",
        provenance=Provenance(trajectory_ids=["traj_delete"], file_paths=["review.md"]),
        file_scope=["review.md"],
    ))
    original_provenance = memory.provenance

    deleted = store.soft_delete(memory.id)

    assert deleted.status == MemoryStatus.DELETED
    assert store.get(memory.id) is deleted
    assert store.get(memory.id).content == "Retired reviewer preference."
    assert store.get(memory.id).provenance is original_provenance
    assert store.get_all(status=MemoryStatus.DELETED) == [deleted]
    assert store.get_all(include_inactive=True) == [deleted]
    assert store.get_all(include_inactive=False) == []


def test_supersede_preserves_evidence():
    store = MemoryStore()
    old = store.add(_memory(
        "This repo uses Jest.",
        provenance=Provenance(trajectory_ids=["traj_old"], file_paths=["package.json"]),
        file_scope=["package.json"],
    ))
    new = _memory(
        "This repo uses Vitest.",
        provenance=Provenance(trajectory_ids=["traj_new"], file_paths=["package.json"]),
        file_scope=["package.json"],
    )

    old_after, new_after = store.supersede(old.id, new)

    assert old_after.status == MemoryStatus.SUPERSEDED
    assert old_after.content == "This repo uses Jest."
    assert old_after.provenance.trajectory_ids == ["traj_old"]
    assert new_after.id in old_after.superseded_by
    assert old_after.id in new_after.supersedes
    assert len(store.get_all(include_inactive=True)) == 2


def test_mark_stale_preserves_item():
    store = MemoryStore([_memory("Generated files are source of truth.")])
    memory = store.get_all()[0]
    store.mark_stale(memory.id, score=0.8)

    assert store.get(memory.id).status == MemoryStatus.STALE
    assert store.get(memory.id).staleness_score == 0.8
    assert store.get(memory.id).content == "Generated files are source of truth."


def test_search_keyword_tag_and_scope_scoring():
    store = MemoryStore()
    vitest = store.add(_memory(
        "Use Vitest for browser component tests.",
        MemoryType.PROCEDURAL,
        retrieval_tags=["testing", "vitest"],
        repo_scope="repo-a",
        file_scope=["package.json"],
        utility_score=0.8,
    ))
    store.add(_memory(
        "Use mypy for Python type checks.",
        MemoryType.PROCEDURAL,
        retrieval_tags=["typing"],
        repo_scope="repo-a",
        file_scope=["pyproject.toml"],
    ))

    results = store.search(
        "vitest tests",
        tags=["testing"],
        scope={"repo_scope": "repo-a", "files": ["package.json"]},
        with_scores=True,
    )

    assert results
    assert results[0][1].id == vitest.id
    assert results[0][0] > 0.0


def test_json_roundtrip():
    store = MemoryStore(namespace="condition-json")
    first = store.add(_memory(
        "Reviewer prefers small, scoped patches.",
        MemoryType.HUMAN_FEEDBACK,
        retrieval_tags=["reviewer"],
        human_verified=True,
    ))
    second = store.add(_memory("Old build command.", MemoryType.PROCEDURAL))
    store.supersede(second.id, _memory("New build command.", MemoryType.PROCEDURAL))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "memory.json"
        store.save(path)
        loaded = MemoryStore.load(path)

    assert loaded.namespace == "condition-json"
    assert len(loaded.get_all(include_inactive=True)) == 3
    assert loaded.get(first.id).human_verified is True
    assert loaded.get(second.id).status == MemoryStatus.SUPERSEDED


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} memory_store tests passed.")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
