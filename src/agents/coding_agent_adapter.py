"""Coding-agent adapter interfaces for the DreamForge experiment harness.

This module is intentionally stdlib-only and offline by default.  The
``DeterministicStubAgent`` below is the harness default: it never calls a model,
opens a network connection, or shells out to an external SWE-agent process.

To run a real experiment, plug a model-backed agent or SWE-agent runner in at
the ``AgentAdapter.run_task`` boundary.  That runner should receive only the
public task prompt and the admitted memory context, and should return the same
``TaskResult`` shape with its final patch, verification outcome, step count,
token count, and any categorized terminal error.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class TaskResult:
    """Minimal wake-agent result consumed by the experiment runner."""

    patch: str
    passed: bool
    steps: int
    tokens: int
    error_type: Optional[str] = None
    error_detail: Optional[str] = None


class AgentAdapter(ABC):
    """Stable boundary between the benchmark harness and a coding agent."""

    @abstractmethod
    def run_task(self, task: Any, memory_context: Any) -> TaskResult:
        """Run one task with the admitted memory context."""


RunnerCallable = Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], TaskResult]


class CallableAgentAdapter(AgentAdapter):
    """Adapter shim for an injected real agent runner.

    A production SWE-agent or LLM implementation belongs behind the injected
    ``runner`` callable.  The harness remains offline unless the caller supplies
    such a callable explicitly.
    """

    def __init__(self, runner: RunnerCallable) -> None:
        self.runner = runner

    def run_task(self, task: Any, memory_context: Any) -> TaskResult:
        public_task = _task_mapping(task)
        context = _context_items(memory_context)
        result = self.runner(public_task, context)
        if not isinstance(result, TaskResult):
            raise TypeError("runner must return TaskResult")
        return result


class DeterministicStubAgent(AgentAdapter):
    """Offline, rule-based adapter used for synthetic smoke runs.

    The rule is deliberately simple: first sessions can pass from the current
    prompt alone; later sessions require at least one non-harmful admitted
    memory.  This makes the harness exercise read/write behavior without
    smuggling oracle labels into the agent.
    """

    def run_task(self, task: Any, memory_context: Any) -> TaskResult:
        public_task = _task_mapping(task)
        context = _context_items(memory_context)
        prompt = str(public_task.get("prompt", ""))
        session_index = _as_int(public_task.get("session_index"), 1)

        healthy_context = [item for item in context if not _context_is_harmful(item)]
        passed = session_index <= 1 or bool(healthy_context)
        error_type = None if passed else "missing_memory"
        patch = self._patch(public_task, healthy_context, passed)
        tokens = _token_count(prompt) + _token_count(_context_text(context)) + _token_count(patch)
        steps = max(1, 2 + len(context))
        return TaskResult(
            patch=patch,
            passed=passed,
            steps=steps,
            tokens=tokens,
            error_type=error_type,
        )

    def _patch(
        self,
        task: Mapping[str, Any],
        memory_context: Sequence[Mapping[str, Any]],
        passed: bool,
    ) -> str:
        task_id = str(task.get("id", "unknown-task"))
        prompt = str(task.get("prompt", ""))
        context_text = _context_text(memory_context)
        cues = _domain_cues(prompt + "\n" + context_text)
        status = "PASS" if passed else "FAIL"
        memory_lines = [
            f"- admitted memory {item.get('id', 'unknown')}: {item.get('content', '')}"
            for item in memory_context[:3]
        ]
        if not memory_lines:
            memory_lines = ["- no admitted memory"]
        cue_lines = [f"- {cue}" for cue in cues] or ["- apply only the current prompt"]
        return "\n".join([
            f"deterministic_stub_patch: {task_id}",
            f"status: {status}",
            "actions:",
            *cue_lines,
            "memory_context:",
            *memory_lines,
        ])


def default_agent() -> AgentAdapter:
    """Return the offline default adapter used by scripts/run_experiment.py."""

    return DeterministicStubAgent()


def _task_mapping(task: Any) -> Dict[str, Any]:
    if isinstance(task, Mapping):
        data = dict(task)
    elif hasattr(task, "to_dict"):
        data = dict(task.to_dict())
    else:
        data = {
            key: getattr(task, key)
            for key in ("id", "sequence_id", "seq_type", "session_index", "prompt")
            if hasattr(task, key)
        }
    return {
        "id": data.get("id"),
        "sequence_id": data.get("sequence_id"),
        "seq_type": _enum_value(data.get("seq_type")),
        "session_index": data.get("session_index"),
        "prompt": data.get("prompt", ""),
        "files": list(data.get("files") or data.get("file_scope") or []),
    }


def _context_items(memory_context: Any) -> List[Dict[str, Any]]:
    if memory_context is None:
        return []
    if isinstance(memory_context, Mapping):
        if "memories" in memory_context:
            values = memory_context.get("memories") or []
        else:
            values = [memory_context]
    elif isinstance(memory_context, str):
        values = [{"id": "text-context", "content": memory_context}]
    elif isinstance(memory_context, Iterable):
        values = list(memory_context)
    else:
        values = [memory_context]

    out: List[Dict[str, Any]] = []
    for item in values:
        if isinstance(item, Mapping):
            out.append(dict(item))
        elif hasattr(item, "to_dict"):
            out.append(dict(item.to_dict()))
        else:
            out.append({"id": repr(type(item)), "content": str(item)})
    return out


def _context_is_harmful(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status", "active")).lower()
    if status in {"stale", "superseded", "deleted"}:
        return True
    if float(item.get("risk_score") or 0.0) >= 0.95:
        return True
    if float(item.get("staleness_score") or 0.0) >= 0.95:
        return True
    if item.get("superseded_by"):
        return True
    return False


def _context_text(items: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(str(item.get("content", "")) for item in items)


def _domain_cues(text: str) -> List[str]:
    lowered = text.lower()
    cues: List[str] = []
    if "dataclass" in lowered or "to_dict" in lowered:
        cues.append("use dataclass-style structure and explicit serialization only where scoped")
    if "generated/" in lowered or "generated file" in lowered:
        cues.append("edit the generator or source of truth before generated artifacts")
    if "queueclient" in lowered:
        cues.append("respect the QueueClient/EventBusClient architecture scope")
    if "eventbusclient" in lowered:
        cues.append("prefer the EventBusClient architecture when current")
    if "reviewer" in lowered or "drive-by" in lowered:
        cues.append("keep bug-fix diffs narrow unless feedback scopes an adjacent cleanup")
    if "flaky" in lowered or "timeout" in lowered:
        cues.append("separate flaky timeout evidence from real regression evidence")
    return cues


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _token_count(text: str) -> int:
    return len(re.findall(r"\S+", str(text or "")))


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


__all__ = [
    "AgentAdapter",
    "CallableAgentAdapter",
    "DeterministicStubAgent",
    "TaskResult",
    "default_agent",
]
