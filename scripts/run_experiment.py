#!/usr/bin/env python3
"""Single-condition offline experiment harness for DreamForge fixtures.

This is the synthetic/stub runner requested by the experiment runbook.  It
loads fixture tasks, runs one B0-B7 memory condition with the deterministic
adapter by default, computes smoke metrics, and writes a bannered JSON file.
It does not call a network service, an LLM, or an external SWE-agent process.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TASKS_DIR = ROOT / "experiments" / "data" / "tasks"
RESULTS_ROOT = ROOT / "experiments" / "results"
BANNER = "SYNTHETIC SMOKE/STUB RUN — NOT EXPERIMENTAL RESULTS, DO NOT CITE."

sys.path.insert(0, str(SRC))

from agents.coding_agent_adapter import AgentAdapter, TaskResult, default_agent  # noqa: E402
from benchmarks.baselines import available_conditions, create_policy  # noqa: E402
from dream_memory import evaluation as metrics  # noqa: E402
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType  # noqa: E402
from dream_memory.trajectory_logger import TrajectoryLogger  # noqa: E402


METRIC_KEYS = [
    "TaskSuccess",
    "Pass@1",
    "RepeatedErrorRate",
    "StaleMemoryActivationRate",
    "HarmfulMemoryRate",
    "UsefulMemoryPrecision",
    "ProvenanceCompleteness",
    "ScopeAccuracy",
    "ContradictionRepairAccuracy",
    "TransferScore",
    "RegressionAfterUpdate",
    "MemoryBloat",
    "TotalTokens",
    "TotalLatency",
    "CostPerSuccessfulTask",
    "SleepCostShare",
]


def _load_task_loader() -> Any:
    path = SRC / "benchmarks" / "task_loader.py"
    spec = importlib.util.spec_from_file_location("task_loader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load task_loader from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TASK_LOADER = _load_task_loader()


def _load_sequences(tasks_dir: Path = TASKS_DIR) -> Tuple[Dict[str, Any], List[Any]]:
    manifest_path = tasks_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sequences: List[Any] = []
    for name in manifest["files"]:
        fixture = json.loads((tasks_dir / name).read_text(encoding="utf-8"))
        sequence_payload = fixture["sequence"]
        tasks = [
            TASK_LOADER.SyntheticTask(
                id=task["id"],
                sequence_id=task["sequence_id"],
                seq_type=TASK_LOADER.SequenceType(task["seq_type"]),
                session_index=int(task["session_index"]),
                prompt=task["prompt"],
                injected_memory_event=dict(task["injected_memory_event"]),
                expected_behavior=task["expected_behavior"],
                oracle_check=dict(task["oracle_check"]),
            )
            for task in fixture["tasks"]
        ]
        sequences.append(TASK_LOADER.TaskSequence(
            id=sequence_payload["id"],
            seq_type=TASK_LOADER.SequenceType(sequence_payload["seq_type"]),
            sequence_index=int(sequence_payload["sequence_index"]),
            tasks=tasks,
        ))
    return manifest, sequences


def _iter_tasks(sequences: Sequence[Any], limit: Optional[int]) -> List[Any]:
    tasks: List[Any] = []
    for sequence in sequences:
        for task in sequence.tasks:
            tasks.append(task)
            if limit is not None and len(tasks) >= limit:
                return tasks
    return tasks


def _public_task(task: Any) -> Dict[str, Any]:
    return {
        "id": task.id,
        "sequence_id": task.sequence_id,
        "seq_type": task.seq_type.value,
        "session_index": task.session_index,
        "prompt": task.prompt,
        "files": [_task_file(task)],
    }


def _task_file(task: Any) -> str:
    return f"experiments/data/tasks/{task.sequence_id}.json"


def _raw_episode(
    task: Any,
    condition: str,
    result: TaskResult,
    memory_context: Sequence[Mapping[str, Any]],
    task_success: bool,
) -> Dict[str, Any]:
    event = dict(task.injected_memory_event)
    observations = [
        str(event.get("content", "")),
        result.patch,
    ]
    observations.extend(_project_fact_observations(task))
    episode: Dict[str, Any] = {
        "trajectory_id": f"{condition}-{task.id}",
        "task_id": task.sequence_id,
        "benchmark_task_id": task.id,
        "session_id": f"{condition}-{task.id}",
        "condition_id": condition,
        "model_id": "deterministic-stub-agent",
        "repo_commit": "synthetic-fixture",
        "repo_scope": task.sequence_id,
        "sequence_id": task.sequence_id,
        "file_paths": [_task_file(task)],
        "prompt": task.prompt,
        "actions": [result.patch],
        "observations": observations,
        "known_constraints": [str(event.get("content", ""))],
        "memory_reads": [str(item.get("id", "")) for item in memory_context],
        "injected_memory_event": event,
        "outcome": "success" if task_success else "failure",
    }
    if task_success:
        episode["successful_recovery"] = "Deterministic stub satisfied the synthetic smoke pass signal."
    if event.get("event_type") == "human-feedback" or event.get("memory_type") == "human_feedback":
        episode["human_feedback"] = str(event.get("content", ""))
        episode["human_feedback_id"] = f"feedback-{task.id}"
    repo_fact = _repo_fact(task)
    if repo_fact:
        episode["repo_fact"] = repo_fact
    return episode


def _project_fact_observations(task: Any) -> List[str]:
    text = f"{task.prompt}\n{task.injected_memory_event.get('content', '')}"
    facts: List[str] = []
    if "EventBusClient" in text:
        facts.append("framework is EventBusClient")
    elif "QueueClient" in text:
        facts.append("framework is QueueClient")
    if "dataclasses" in text and "to_dict" in text:
        facts.append("uses dataclasses with explicit to_dict serialization")
    if "generated/" in text or "generated files" in text.lower():
        facts.append("generated files located in generated/")
    return facts


def _repo_fact(task: Any) -> str:
    facts = _project_fact_observations(task)
    return facts[-1] if facts else ""


def _score_task(task: Any, result: TaskResult) -> bool:
    """Fixture scorer.

    The adapter does not receive oracle fields.  The scorer may inspect the
    synthetic oracle checklist after the patch is produced; for this stub run,
    the adapter pass signal is the executable-test stand-in and oracle
    ``must_avoid`` phrases veto obviously bad patches.
    """

    patch = result.patch.lower()
    for forbidden in task.oracle_check.get("must_avoid", []):
        if str(forbidden).lower() in patch:
            return False
    return bool(result.passed)


def _empty_counters() -> Dict[str, Any]:
    return {
        "successes": [],
        "pass_at_1": [],
        "retrieved": 0,
        "useful_retrieved": 0,
        "harmful_retrieved": 0,
        "scope_matched_retrieved": 0,
        "stale_used": 0,
        "stale_available_tasks": 0,
        "repeated_errors": 0,
        "repeated_error_opportunities": 0,
        "correct_contradiction_repairs": 0,
        "evaluable_contradictions": 0,
        "memory_update_regressions": 0,
        "memory_updates_with_future_dependency": 0,
        "wake_tokens": 0,
        "sleep_tokens": 0,
        "wake_latency": 0.0,
        "sleep_latency": 0.0,
        "judge_tokens": 0,
        "judge_latency": 0.0,
        "steps": 0,
        "tasks": 0,
        "errors": {},
    }


def _update_context_counters(counters: Dict[str, Any], task: Any, context: Sequence[Mapping[str, Any]]) -> None:
    harmful = sum(1 for item in context if _context_is_harmful(item))
    useful = sum(1 for item in context if _context_is_useful(item, task))
    scoped = sum(1 for item in context if _context_scope_matches(item, task))
    counters["retrieved"] += len(context)
    counters["harmful_retrieved"] += harmful
    counters["useful_retrieved"] += useful
    counters["scope_matched_retrieved"] += scoped
    counters["stale_used"] += harmful
    if harmful or ("stale" in task.prompt.lower() and context):
        counters["stale_available_tasks"] += 1


def _context_is_harmful(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status", "active")).lower()
    return (
        status in {"stale", "superseded", "deleted"}
        or float(item.get("staleness_score") or 0.0) >= 0.95
        or float(item.get("risk_score") or 0.0) >= 0.95
        or bool(item.get("superseded_by"))
    )


def _context_is_useful(item: Mapping[str, Any], task: Any) -> bool:
    if _context_is_harmful(item) or not _context_scope_matches(item, task):
        return False
    task_words = _tokens(" ".join([
        task.prompt,
        str(task.injected_memory_event.get("content", "")),
    ]))
    memory_words = _tokens(str(item.get("content", "")))
    return bool(task_words & memory_words) or str(item.get("type")) == "episodic"


def _context_scope_matches(item: Mapping[str, Any], task: Any) -> bool:
    repo_scope = item.get("repo_scope")
    if repo_scope and repo_scope != task.sequence_id:
        return False
    file_scope = item.get("file_scope") or []
    if file_scope and _task_file(task) not in file_scope:
        return False
    return True


def _memory_stats(items: Sequence[MemoryItem]) -> Dict[str, int]:
    active = [item for item in items if item.status == MemoryStatus.ACTIVE]
    provenance_complete = sum(1 for item in active if item.provenance.is_grounded())
    active_memory_tokens = sum(_token_count(item.content) for item in active)
    useful_active_memory_tokens = sum(
        _token_count(item.content)
        for item in active
        if item.utility_score >= 0.5 and item.risk_score < 0.9
    )
    return {
        "active_count": len(active),
        "all_count": len(items),
        "provenance_complete": provenance_complete,
        "active_memory_tokens": active_memory_tokens,
        "useful_active_memory_tokens": useful_active_memory_tokens,
    }


def _compute_metrics(counters: Dict[str, Any], policy: Any) -> Dict[str, float]:
    stats = _memory_stats(policy.memory_items())
    total_tokens = metrics.TotalTokens(
        counters["wake_tokens"],
        counters["sleep_tokens"],
        counters["judge_tokens"],
    )
    total_latency = metrics.TotalLatency(
        counters["wake_latency"],
        counters["sleep_latency"],
        counters["judge_latency"],
    )
    return {
        "TaskSuccess": metrics.TaskSuccess(counters["successes"]),
        "Pass@1": metrics.PassAt1(counters["pass_at_1"]),
        "RepeatedErrorRate": metrics.RepeatedErrorRate(
            counters["repeated_errors"],
            counters["repeated_error_opportunities"],
        ),
        "StaleMemoryActivationRate": metrics.StaleMemoryActivationRate(
            counters["stale_used"],
            counters["stale_available_tasks"],
        ),
        "HarmfulMemoryRate": metrics.HarmfulMemoryRate(
            counters["harmful_retrieved"],
            counters["retrieved"],
        ),
        "UsefulMemoryPrecision": metrics.UsefulMemoryPrecision(
            counters["useful_retrieved"],
            counters["retrieved"],
        ),
        "ProvenanceCompleteness": metrics.ProvenanceCompleteness(
            stats["provenance_complete"],
            stats["active_count"],
        ),
        "ScopeAccuracy": metrics.ScopeAccuracy(
            counters["scope_matched_retrieved"],
            counters["retrieved"],
        ),
        "ContradictionRepairAccuracy": metrics.ContradictionRepairAccuracy(
            counters["correct_contradiction_repairs"],
            counters["evaluable_contradictions"],
        ),
        "TransferScore": float("nan"),
        "RegressionAfterUpdate": metrics.RegressionAfterUpdate(
            counters["memory_update_regressions"],
            counters["memory_updates_with_future_dependency"],
        ),
        "MemoryBloat": metrics.MemoryBloat(stats["active_memory_tokens"], stats["useful_active_memory_tokens"]),
        "TotalTokens": total_tokens,
        "TotalLatency": total_latency,
        "CostPerSuccessfulTask": metrics.CostPerSuccessfulTask(total_tokens, sum(counters["successes"])),
        "SleepCostShare": metrics.SleepCostShare(counters["sleep_tokens"], total_tokens),
    }


def _json_metrics(values: Dict[str, float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in METRIC_KEYS:
        value = float(values[key])
        out[key] = "NA" if math.isnan(value) else round(value, 6)
    return out


def run(condition: str, limit: Optional[int] = None, agent: Optional[AgentAdapter] = None) -> Dict[str, Any]:
    condition = condition.upper()
    manifest, sequences = _load_sequences()
    tasks = _iter_tasks(sequences, limit)
    policy = create_policy(condition)
    adapter = agent or default_agent()
    logger = TrajectoryLogger()
    counters = _empty_counters()
    task_records: List[Dict[str, Any]] = []

    for task in tasks:
        counters["tasks"] += 1
        public_task = _public_task(task)
        context = policy.read(public_task)
        result = adapter.run_task(public_task, context)
        task_success = _score_task(task, result)
        pass_at_1 = task_success and result.error_type is None

        _update_context_counters(counters, task, context)
        counters["successes"].append(1 if task_success else 0)
        counters["pass_at_1"].append(1 if pass_at_1 else 0)
        counters["wake_tokens"] += result.tokens
        counters["wake_latency"] += 0.001 * max(1, result.steps)
        counters["steps"] += result.steps
        if result.error_type:
            counters["errors"][result.error_type] = counters["errors"].get(result.error_type, 0) + 1

        if task.session_index > 1 and policy.snapshot()["memory_count"] > 0:
            counters["repeated_error_opportunities"] += 1
            if not task_success:
                counters["repeated_errors"] += 1
        event_type = str(task.injected_memory_event.get("event_type", ""))
        if event_type == "contradiction":
            counters["evaluable_contradictions"] += 1
        if event_type in {"memory_update", "human-feedback"}:
            counters["memory_updates_with_future_dependency"] += 1

        episode = _raw_episode(task, condition, result, context, task_success)
        trajectory = logger.record(
            task_id=task.id,
            session_id=f"{condition}-{task.id}",
            model_id="deterministic-stub-agent",
            condition_id=condition,
            repo_commit="synthetic-fixture",
            file_diffs=[_task_file(task)],
            memory_reads=[str(item.get("id", "")) for item in context],
            final_outcome=episode["outcome"],
            raw_episode=episode,
            steps=[{
                "action": result.patch,
                "observation": "synthetic fixture observation",
                "memory_reads": [str(item.get("id", "")) for item in context],
            }],
        )
        policy.write(trajectory)
        counters["sleep_tokens"] += policy.last_sleep_tokens
        counters["sleep_latency"] += 0.0005 * policy.last_write_count
        if event_type == "contradiction":
            counters["correct_contradiction_repairs"] += sum(
                1
                for item in policy.last_write_items
                if item.type == MemoryType.CONTRADICTION and item.status == MemoryStatus.ACTIVE
            )

        task_records.append({
            "task_id": task.id,
            "sequence_id": task.sequence_id,
            "sequence_type": task.seq_type.value,
            "session_index": task.session_index,
            "memory_read_count": len(context),
            "memory_write_count": policy.last_write_count,
            "agent_passed": result.passed,
            "task_success": task_success,
            "pass_at_1": pass_at_1,
            "steps": result.steps,
            "tokens": result.tokens,
            "error_type": result.error_type,
            "retrieval_decisions": list(policy.last_retrieval_decisions),
        })

    metric_values = _compute_metrics(counters, policy)
    memory_stats = _memory_stats(policy.memory_items())
    output = {
        "_banner": BANNER,
        "run_type": "offline_synthetic_stub_run",
        "results_are_citable": False,
        "interpretation": "Synthetic fixture plumbing only; not experimental evidence.",
        "condition": condition,
        "limit": limit,
        "fixture_version": manifest["fixture_version"],
        "fixture_task_count": len(tasks),
        "fixture_total_task_count": manifest["task_count"],
        "agent": {
            "adapter_class": type(adapter).__name__,
            "model_calls": 0,
            "network_calls": 0,
        },
        "policy": policy.describe(),
        "metrics": _json_metrics(metric_values),
        "counts": {
            "tasks": counters["tasks"],
            "successes": int(sum(counters["successes"])),
            "pass_at_1": int(sum(counters["pass_at_1"])),
            "retrieved_memories": counters["retrieved"],
            "useful_retrieved": counters["useful_retrieved"],
            "harmful_retrieved": counters["harmful_retrieved"],
            "sleep_writes": sum(record["memory_write_count"] for record in task_records),
            "steps": counters["steps"],
            "errors": counters["errors"],
            **memory_stats,
        },
        "task_records": task_records,
    }

    results_dir = RESULTS_ROOT / condition
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / "results.json"
    results_path.write_text(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./-]+", str(text or "").lower()))


def _token_count(text: str) -> int:
    return len(str(text or "").split())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=available_conditions(), required=True)
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of fixture tasks to run.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive when provided")
    output = run(args.condition, args.limit)
    path = RESULTS_ROOT / args.condition.upper() / "results.json"
    print(BANNER)
    print(f"condition={output['condition']} tasks={output['fixture_task_count']} successes={output['counts']['successes']}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
