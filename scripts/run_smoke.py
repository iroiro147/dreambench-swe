#!/usr/bin/env python3
"""Executable offline smoke harness for DreamForge.

This script proves the fixture -> memory pipeline -> metric path runs end to
end with a deterministic rule-based stub. It deliberately does not run a model,
call a network service, or claim experimental validity.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TASKS_DIR = ROOT / "experiments" / "data" / "tasks"
RESULTS_DIR = ROOT / "experiments" / "results" / "smoke"
RESULTS_JSON = RESULTS_DIR / "results.json"
SMOKE_README = RESULTS_DIR / "SMOKE_README.md"
WARNING = "SYNTHETIC SMOKE TEST — DETERMINISTIC STUB AGENT, NOT EXPERIMENTAL RESULTS, DO NOT CITE."

sys.path.insert(0, str(SRC))

from dream_memory import evaluation as metrics  # noqa: E402
from dream_memory.consolidation import sleep_pipeline  # noqa: E402
from dream_memory.memory_store import MemoryStore  # noqa: E402
from dream_memory.retrieval import RetrievalGate  # noqa: E402
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

CONDITIONS = [
    {
        "id": "B0-no-memory-stub",
        "label": "B0 no-memory stub",
        "uses_memory": False,
        "description": "Deterministic stub sees only the current fixture task; no external memory is read or written.",
    },
    {
        "id": "DF-dreamforge-stub",
        "label": "DreamForge stub",
        "uses_memory": True,
        "description": "Deterministic stub uses the real MemoryStore, RetrievalGate, sleep_pipeline, and repair path.",
    },
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


def _tokens(text: str) -> List[str]:
    return str(text or "").split()


def _token_count(text: str) -> int:
    return len(_tokens(text))


def _task_file(task: Any) -> str:
    return f"experiments/data/tasks/{task.sequence_id}.json"


def _retrieval_query(task: Any) -> Dict[str, Any]:
    event = task.injected_memory_event
    return {
        "text": "\n".join([
            task.prompt,
            task.expected_behavior,
            str(event.get("content", "")),
        ]),
        "tags": ["consolidation", str(task.seq_type.value)],
        "repo_scope": task.sequence_id,
        "files": [_task_file(task)],
        "phase": "implementation",
        "token_budget_read": 1200,
    }


def _scope_matches(memory: MemoryItem, task: Any) -> bool:
    if memory.repo_scope and memory.repo_scope != task.sequence_id:
        return False
    if memory.file_scope and _task_file(task) not in memory.file_scope:
        return False
    return True


def _is_harmful(memory: MemoryItem) -> bool:
    return (
        memory.status != MemoryStatus.ACTIVE
        or memory.staleness_score >= 0.95
        or memory.risk_score >= 0.95
        or bool(memory.superseded_by)
    )


def _is_useful(memory: MemoryItem, task: Any) -> bool:
    if _is_harmful(memory) or not _scope_matches(memory, task):
        return False
    task_words = set(" ".join([
        task.prompt,
        task.expected_behavior,
        str(task.injected_memory_event.get("content", "")),
    ]).lower().replace("-", " ").split())
    memory_words = set(memory.content.lower().replace("-", " ").split())
    return bool(task_words & memory_words) or memory.type == MemoryType.EPISODIC


def _mark_stale_fixture_memory(store: MemoryStore, task: Any) -> int:
    for memory in store.get_all(status=MemoryStatus.ACTIVE):
        if _scope_matches(memory, task) and memory.type != MemoryType.CONTRADICTION:
            store.mark_stale(memory.id, 1.0)
            return 1
    return 0


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


def _stub_success(task: Any, retrieved: Sequence[MemoryItem], uses_memory: bool) -> bool:
    if not uses_memory:
        evidence_text = " ".join(task.oracle_check.get("evidence_required", [])).lower()
        memory_needed = any(term in evidence_text for term in (
            "retrieved",
            "memory",
            "prior",
            "supersession",
            "staleness",
        ))
        return task.session_index == 1 and not memory_needed
    if task.session_index == 1:
        return True
    return bool(retrieved)


def _stub_action(task: Any, condition_id: str, retrieved: Sequence[MemoryItem], success: bool) -> str:
    status = "satisfy" if success else "miss"
    return (
        f"{condition_id} deterministic stub {status} synthetic checklist for {task.id}; "
        f"retrieved_memories={len(retrieved)}."
    )


def _raw_episode(task: Any, condition_id: str, retrieved: Sequence[MemoryItem], success: bool) -> Dict[str, Any]:
    event = task.injected_memory_event
    observations = [
        str(event.get("content", "")),
        f"Expected behavior fixture text: {task.expected_behavior}",
    ]
    observations.extend(_project_fact_observations(task))
    action = _stub_action(task, condition_id, retrieved, success)
    episode: Dict[str, Any] = {
        "trajectory_id": f"smoke-{condition_id}-{task.id}",
        # Sequence-level task scope lets contradiction repair compare memories
        # across sessions in the same synthetic fixture sequence.
        "task_id": task.sequence_id,
        "benchmark_task_id": task.id,
        "session_id": f"smoke-{condition_id}-{task.id}",
        "condition_id": condition_id,
        "model_id": "deterministic-stub",
        "repo_commit": "synthetic-smoke-fixture",
        "repo_scope": task.sequence_id,
        "file_paths": [_task_file(task)],
        "prompt": task.prompt,
        "actions": [action, "python3 scripts/run_smoke.py"],
        "observations": observations,
        "known_constraints": [str(event.get("content", ""))],
        "memory_reads": [memory.id for memory in retrieved],
        "outcome": "success" if success else "failure",
    }
    if success:
        episode["successful_recovery"] = "Synthetic stub checklist signal marked this fixture task as satisfied."
    if event.get("event_type") == "human-feedback" or event.get("memory_type") == "human_feedback":
        episode["human_feedback"] = str(event.get("content", ""))
        episode["human_feedback_id"] = f"feedback-{task.id}"
    if event.get("memory_type") == "semantic_project":
        facts = _project_fact_observations(task)
        if facts:
            episode["repo_fact"] = facts[-1]
    return episode


def _active_memory_stats(store: MemoryStore) -> Tuple[int, int, int, int]:
    active = store.get_all(status=MemoryStatus.ACTIVE)
    complete = sum(1 for memory in active if memory.provenance.is_grounded())
    total_tokens = sum(_token_count(memory.content) for memory in active)
    useful_tokens = sum(
        _token_count(memory.content)
        for memory in active
        if memory.utility_score >= 0.5 and memory.risk_score < 0.9
    )
    return complete, len(active), total_tokens, useful_tokens


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
        "judge_tokens": 0,
        "wake_latency": 0.0,
        "sleep_latency": 0.0,
        "judge_latency": 0.0,
        "tasks": 0,
        "sleep_writes": 0,
        "retrieval_gate_decisions": 0,
    }


def _compute_metrics(counters: Dict[str, Any], store: MemoryStore) -> Dict[str, float]:
    provenance_complete, active_count, total_memory_tokens, useful_memory_tokens = _active_memory_stats(store)
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
            provenance_complete,
            active_count,
        ),
        "ScopeAccuracy": metrics.ScopeAccuracy(
            counters["scope_matched_retrieved"],
            counters["retrieved"],
        ),
        "ContradictionRepairAccuracy": metrics.ContradictionRepairAccuracy(
            counters["correct_contradiction_repairs"],
            counters["evaluable_contradictions"],
        ),
        "TransferScore": 0.0,
        "RegressionAfterUpdate": metrics.RegressionAfterUpdate(
            counters["memory_update_regressions"],
            counters["memory_updates_with_future_dependency"],
        ),
        "MemoryBloat": metrics.MemoryBloat(total_memory_tokens, useful_memory_tokens),
        "TotalTokens": total_tokens,
        "TotalLatency": total_latency,
        "CostPerSuccessfulTask": metrics.CostPerSuccessfulTask(0.0, sum(counters["successes"])),
        "SleepCostShare": metrics.SleepCostShare(counters["sleep_tokens"], total_tokens),
    }


def _rounded(values: Dict[str, float]) -> Dict[str, float]:
    return {key: round(float(values[key]), 6) for key in METRIC_KEYS}


def _run_condition(condition: Dict[str, Any], sequences: Sequence[Any]) -> Dict[str, Any]:
    store = MemoryStore(namespace=condition["id"])
    gate = RetrievalGate(store)
    logger = TrajectoryLogger()
    counters = _empty_counters()
    task_records: List[Dict[str, Any]] = []

    for sequence in sequences:
        for task in sequence.tasks:
            counters["tasks"] += 1
            uses_memory = bool(condition["uses_memory"])
            stale_available = 0
            if uses_memory and task.injected_memory_event.get("event_type") == "staleness":
                stale_available = _mark_stale_fixture_memory(store, task)
                counters["stale_available_tasks"] += stale_available

            retrieved: List[MemoryItem] = []
            if uses_memory:
                retrieved = gate.retrieve(_retrieval_query(task), k=12)
                counters["retrieval_gate_decisions"] += len(gate.last_decisions)

            success = _stub_success(task, retrieved, uses_memory)
            pass_at_1 = success and not any(_is_harmful(memory) for memory in retrieved)
            useful = sum(1 for memory in retrieved if _is_useful(memory, task))
            harmful = sum(1 for memory in retrieved if _is_harmful(memory))
            scoped = sum(1 for memory in retrieved if _scope_matches(memory, task))
            stale_used = sum(1 for memory in retrieved if _is_harmful(memory))

            counters["successes"].append(1 if success else 0)
            counters["pass_at_1"].append(1 if pass_at_1 else 0)
            counters["retrieved"] += len(retrieved)
            counters["useful_retrieved"] += useful
            counters["harmful_retrieved"] += harmful
            counters["scope_matched_retrieved"] += scoped
            counters["stale_used"] += stale_used
            if uses_memory and task.session_index > 1 and len(store) > 0:
                counters["repeated_error_opportunities"] += 1
                if not success:
                    counters["repeated_errors"] += 1
            if uses_memory and task.injected_memory_event.get("event_type") == "contradiction":
                counters["evaluable_contradictions"] += 1
            if uses_memory and task.injected_memory_event.get("event_type") in {"memory_update", "human-feedback"}:
                counters["memory_updates_with_future_dependency"] += 1

            action = _stub_action(task, condition["id"], retrieved, success)
            episode = _raw_episode(task, condition["id"], retrieved, success)
            trajectory = logger.record(
                task_id=task.id,
                session_id=f"{condition['id']}-{task.id}",
                model_id="deterministic-stub",
                condition_id=condition["id"],
                repo_commit="synthetic-smoke-fixture",
                file_diffs=[_task_file(task)],
                memory_reads=[memory.id for memory in retrieved],
                final_outcome=episode["outcome"],
                raw_episode=episode,
                steps=[{
                    "action": action,
                    "observation": "synthetic smoke fixture observation",
                    "memory_reads": [memory.id for memory in retrieved],
                }],
            )

            sleep_outputs: List[MemoryItem] = []
            if uses_memory:
                sleep_outputs = sleep_pipeline([trajectory.raw_episode_view()], store, write=True)
                counters["sleep_writes"] += len(sleep_outputs)
                counters["correct_contradiction_repairs"] += sum(
                    1
                    for memory in sleep_outputs
                    if memory.type == MemoryType.CONTRADICTION and memory.status == MemoryStatus.ACTIVE
                )

            read_tokens = sum(_token_count(memory.content) for memory in retrieved)
            counters["wake_tokens"] += _token_count(task.prompt) + read_tokens + _token_count(action)
            counters["sleep_tokens"] += sum(_token_count(memory.content) for memory in sleep_outputs)
            counters["wake_latency"] += 0.001 * (1 + len(retrieved))
            counters["sleep_latency"] += 0.0005 * len(sleep_outputs)

            task_records.append({
                "task_id": task.id,
                "sequence_id": task.sequence_id,
                "sequence_type": task.seq_type.value,
                "session_index": task.session_index,
                "retrieved_count": len(retrieved),
                "sleep_write_count": len(sleep_outputs),
                "stub_success_signal": success,
                "stale_fixture_memory_available": bool(stale_available),
            })

    metrics_payload = _compute_metrics(counters, store)
    return {
        "description": condition["description"],
        "metrics": _rounded(metrics_payload),
        "counts": {
            "tasks": counters["tasks"],
            "success_signals": int(sum(counters["successes"])),
            "pass_at_1_signals": int(sum(counters["pass_at_1"])),
            "retrieved_memories": counters["retrieved"],
            "sleep_writes": counters["sleep_writes"],
            "active_memories": len(store.get_all(status=MemoryStatus.ACTIVE)),
            "all_memories": len(store),
            "retrieval_gate_decisions": counters["retrieval_gate_decisions"],
        },
        "task_success_signals": list(counters["successes"]),
        "task_records": task_records,
    }


def _apply_transfer_score(results: Dict[str, Any]) -> None:
    b0 = results["conditions"]["B0-no-memory-stub"]["task_success_signals"]
    df = results["conditions"]["DF-dreamforge-stub"]["task_success_signals"]
    score = round(float(metrics.TransferScore(df, b0)), 6)
    results["conditions"]["DF-dreamforge-stub"]["metrics"]["TransferScore"] = score


def _markdown_table(results: Dict[str, Any]) -> str:
    columns = ["condition"] + METRIC_KEYS
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] + ["---:" for _ in METRIC_KEYS]) + " |"
    rows = [header, divider]
    for condition in CONDITIONS:
        payload = results["conditions"][condition["id"]]
        values = [condition["label"]]
        values.extend(f"{payload['metrics'][key]:.6f}" for key in METRIC_KEYS)
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([
        WARNING,
        "",
        "# DreamForge Smoke Harness",
        "",
        "This table is an executable plumbing check over toy fixture behavior. It is not an experiment table.",
        "",
        *rows,
        "",
    ])


def run() -> Dict[str, Any]:
    manifest, sequences = _load_sequences()
    results: Dict[str, Any] = {
        "_warning": WARNING,
        "run_type": "offline_synthetic_smoke",
        "stub_agent": "hard-coded deterministic rule-based agent; no model calls, network, or API keys",
        "interpretation": "Metric values are toy smoke-plumbing signals only and must not be cited as findings.",
        "fixture_version": manifest["fixture_version"],
        "fixture_task_count": manifest["task_count"],
        "fixture_sequence_count": manifest["sequence_count"],
        "conditions": {},
    }
    for condition in CONDITIONS:
        results["conditions"][condition["id"]] = _run_condition(condition, sequences)
    _apply_transfer_score(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    SMOKE_README.write_text(_markdown_table(results), encoding="utf-8")
    return results


def main() -> None:
    results = run()
    print(WARNING)
    print(f"Loaded {results['fixture_task_count']} fixture tasks.")
    print(f"Wrote {RESULTS_JSON}")
    print(f"Wrote {SMOKE_README}")


if __name__ == "__main__":
    main()
