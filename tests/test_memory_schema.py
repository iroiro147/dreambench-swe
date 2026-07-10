"""Tests for the DreamForge memory schema.

Runs with plain python3 (no pytest required):  python3 tests/test_memory_schema.py
Also pytest-compatible:                          pytest tests/test_memory_schema.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dream_memory.schemas import (  # noqa: E402
    MemoryItem, MemoryType, MemoryStatus, Provenance, new_memory,
)


def test_construct_and_defaults():
    m = new_memory("API handlers live in src/server/routes.",
                   MemoryType.SEMANTIC_PROJECT,
                   write_reason="observed during task_001")
    assert m.id.startswith("mem_")
    assert m.status == MemoryStatus.ACTIVE
    assert m.is_active()
    assert 0.0 <= m.confidence <= 1.0
    assert m.created_at and m.updated_at


def test_unit_interval_validation():
    for bad in (-0.1, 1.1, 2.0):
        try:
            new_memory("x", MemoryType.FAILURE, write_reason="r", confidence=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"confidence={bad} should have raised")


def test_requires_content_and_reason():
    for kwargs in ({"content": "", "write_reason": "r"},
                   {"content": "c", "write_reason": "  "}):
        try:
            new_memory(type=MemoryType.PROCEDURAL, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("empty content/write_reason should raise")


def test_supersede_keeps_evidence():
    old = new_memory("This repo uses Jest.", MemoryType.SEMANTIC_PROJECT,
                     write_reason="task_003",
                     provenance=Provenance(file_paths=["package.json"]))
    new = new_memory("This repo uses Vitest.", MemoryType.SEMANTIC_PROJECT,
                     write_reason="package.json now shows vitest",
                     provenance=Provenance(file_paths=["package.json"]))
    old.supersede_with(new.id)
    assert old.status == MemoryStatus.SUPERSEDED
    assert new.id in old.superseded_by
    # evidence is preserved, not deleted
    assert old.provenance.file_paths == ["package.json"]
    assert old.content == "This repo uses Jest."


def test_grounded_provenance():
    grounded = Provenance(trajectory_ids=["traj_014"])
    assert grounded.is_grounded()
    assert not Provenance().is_grounded()


def test_touch_and_stale():
    m = new_memory("flaky test diagnostic", MemoryType.PROCEDURAL, write_reason="r")
    m.touch(); m.touch()
    assert m.use_count == 2 and m.last_used_at
    m.mark_stale()
    assert m.status == MemoryStatus.STALE and m.staleness_score == 1.0


def test_roundtrip_to_dict():
    m = new_memory("c", MemoryType.HUMAN_FEEDBACK, write_reason="reviewer said X")
    d = m.to_dict()
    assert d["type"] == "human_feedback" and d["status"] == "active"
    assert isinstance(d["provenance"], dict)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} schema tests passed.")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
