"""Stdlib tests for src/agents/coding_agent_adapter.py."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agents.coding_agent_adapter import (  # noqa: E402
    CallableAgentAdapter,
    DeterministicStubAgent,
    TaskResult,
)


def test_stub_passes_first_session_without_memory() -> None:
    agent = DeterministicStubAgent()
    result = agent.run_task({
        "id": "convention-learning-01-s01",
        "sequence_id": "convention-learning-01",
        "session_index": 1,
        "prompt": "Use dataclasses with explicit to_dict methods.",
    }, [])

    assert isinstance(result, TaskResult)
    assert result.passed is True
    assert result.error_type is None
    assert "dataclass-style" in result.patch
    assert result.steps >= 1
    assert result.tokens > 0


def test_stub_requires_memory_after_first_session() -> None:
    agent = DeterministicStubAgent()
    result = agent.run_task({
        "id": "convention-learning-01-s02",
        "sequence_id": "convention-learning-01",
        "session_index": 2,
        "prompt": "Reuse the previous convention.",
    }, [])

    assert result.passed is False
    assert result.error_type == "missing_memory"
    assert "FAIL" in result.patch


def test_stub_rejects_harmful_memory_context() -> None:
    agent = DeterministicStubAgent()
    result = agent.run_task({
        "id": "generated-files-01-s04",
        "sequence_id": "generated-files-01",
        "session_index": 4,
        "prompt": "A stale memory is present.",
    }, [{
        "id": "mem_stale",
        "content": "Temporary hotfix permits direct generated file edits.",
        "status": "stale",
    }])

    assert result.passed is False
    assert result.error_type == "missing_memory"


def test_stub_uses_healthy_memory_after_first_session() -> None:
    agent = DeterministicStubAgent()
    result = agent.run_task({
        "id": "generated-files-01-s02",
        "sequence_id": "generated-files-01",
        "session_index": 2,
        "prompt": "Fix generated/schema.py again.",
    }, [{
        "id": "mem_generated",
        "content": "Generated files should be fixed through the source generator.",
        "status": "active",
        "risk_score": 0.1,
    }])

    assert result.passed is True
    assert result.error_type is None
    assert "generated artifacts" in result.patch


def test_callable_adapter_delegates_to_injected_runner() -> None:
    def runner(task, context):
        assert task["id"] == "task-1"
        assert context[0]["id"] == "mem-1"
        return TaskResult("patched", True, 3, 9, None)

    adapter = CallableAgentAdapter(runner)
    result = adapter.run_task({"id": "task-1", "prompt": "Do work."}, [{"id": "mem-1", "content": "Use memory."}])

    assert result.patch == "patched"
    assert result.steps == 3
    assert result.tokens == 9


def _run_all() -> bool:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} agent adapter tests passed.")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
