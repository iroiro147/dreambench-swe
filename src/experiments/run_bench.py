"""DreamBench-SWE experiment runner.

Importing this module is offline-safe.  Full benchmark runs shell out through
``experiments.env.load_env`` and model agents at runtime only.  The ``--selftest``
path is intentionally in-process and constructs only ``StubAgent``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.coding_agent_adapter import TaskResult  # noqa: E402
from agents.llm_agents import CodexAgent, GridAgent, StubAgent  # noqa: E402
from benchmarks.baselines import BASELINE_REGISTRY, DreamForgePolicy, Mem0LiteralPolicy, Mem0Policy, MemoryPolicy, create_policy  # noqa: E402
from dream_memory import evaluation  # noqa: E402
from dream_memory.llm_judge import LLMJudge  # noqa: E402
from dream_memory.schemas import MemoryStatus, MemoryType  # noqa: E402
from dream_memory.slice_scorer import HEADLINE_SLICE_METRICS, headline_metric_values, score_memory_trap_slice  # noqa: E402
from dream_memory.trajectory_logger import Step, Trajectory  # noqa: E402
from experiments.contamination_scan import scan_record  # noqa: E402
from experiments.env import load_env  # noqa: E402


DEFAULT_RESULTS_ROOT = ROOT / "experiments" / "results"


def _path_from_env(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else fallback


def default_results_root() -> Path:
    return _path_from_env("DREAMBENCH_RESULTS_ROOT", DEFAULT_RESULTS_ROOT)


RESULTS_ROOT = default_results_root()
DEFAULT_SEED = 1729
DEFAULT_MAX_MODEL_CALLS = 1
ORACLE_TIMEOUT_SECONDS = 120.0
DEFAULT_JUDGE_MODEL = "glm-latest"
CC2_JUDGE_MODELS = {
    "cc2-sonnet-5": "claude-sonnet-5",
    "cc2-opus-4-8": "claude-opus-4-8",
}
GRID_JUDGE_MODELS = {"glm-latest", "kimi-latest"}
CODEX_JUDGE_MODELS = {"gpt-5.5": "gpt-5.5", "codex-gpt-5.5": "gpt-5.5"}
JUDGE_MODELS = GRID_JUDGE_MODELS | set(CODEX_JUDGE_MODELS) | set(CC2_JUDGE_MODELS)
DF_CONDITION = "DF"
DF_STRICT_CONDITION = "DF-strict"
DF_HYBRID_CONDITION = "DF-hybrid"
DF_RAW_ONLY_CONDITION = "DF-raw-only"
DF_STRICT_HYBRID_CONDITION = "DF-strict-hybrid"
MEM0_CONDITION = "B5-MEM0"
MEM0_LITERAL_CONDITION = "B5-MEM0-LIT"
LIVE_BASELINE_CONDITIONS = {MEM0_CONDITION, MEM0_LITERAL_CONDITION}
DF_VARIANT_CONDITIONS = {
    DF_HYBRID_CONDITION,
    DF_RAW_ONLY_CONDITION,
    DF_STRICT_HYBRID_CONDITION,
}
DF_ABLATIONS: Dict[str, Dict[str, bool]] = {
    "A0": {"enable_consolidation": False},
    "A2": {"enable_repair": False},
    "A4": {"enable_replay": False},
    "A5": {"enable_stale_suppression": False},
    "A6": {"enable_retrieval_gate": False},
    "A11": {"forced_consolidation": True},
}
DF_ABLATION_DESCRIPTIONS: Dict[str, str] = {
    "A0": "episodic-only raw episode writes without typed consolidation, repair, or replay",
    "A2": "contradiction repair disabled",
    "A4": "counterfactual replay disabled",
    "A5": "read-side stale and superseded suppression disabled while repair remains enabled",
    "A6": "retrieval hard gate disabled with score-ranked memory admission",
    "A11": "global maintenance scope forced during consolidation",
}
DF_STRICT_RETRIEVAL_CONFIG: Dict[str, Any] = {
    "theta_admit": 1.5,
    "read_limit": 3,
    "token_budget_read": 600,
}
DF_STRICT_DESCRIPTION = (
    "pre-registered strict retrieval admission variant: theta_admit=1.5, "
    "read_limit=3, token_budget_read=600"
)
SCORER_ONLY_KEYS = {
    "reference_patch",
    "expected_pass",
    "injected_memory_event",
    "injected_memory_events",
    "error_taxonomy_hint",
    "oracle_cmd",
    "events",
    "oracle_labels",
    "oracles",
    "oracle_cmds",
    "initial_commit",
    "state_repo",
}
TOKEN_PRICE_PER_MILLION = {
    "glm-latest": {"input": 0.0, "output": 0.0},
    "kimi-latest": {"input": 0.0, "output": 0.0},
    "grid:glm-latest": {"input": 0.0, "output": 0.0},
    "grid:kimi-latest": {"input": 0.0, "output": 0.0},
    "gpt-5.5": {"input": 5.0, "output": 30.0},
    "codex": {"input": 5.0, "output": 30.0},
    "stub": {"input": 0.0, "output": 0.0},
}
HIDDEN_LOG_MARKERS = (
    "experiments/env/oracles",
    "experiments/env/refsol",
    "experiments/env/sequences.jsonl",
    "experiments/secrets.env",
    "oracle_cmd",
    "refsol",
    "Exact reviewer",
    "hidden oracle",
    "EXPORT-v",
)
TEMP_HIDDEN_LOG_GLOBS = (
    "expr-*-validate.*/repo/logs/codex/*.log",
    "expr-*-validate.*/logs/codex/*.log",
    "dreambench-*/repo/logs/codex/*.log",
    "dreambench-*/*/logs/codex/*.log",
    "*/repo/logs/codex/b2b-*.log",
)


@dataclass
class SequenceState:
    """Private continuation repo state for one (condition, seq_id)."""

    condition: str
    seq_id: str
    repo_id: str
    state_repo: Path
    current_commit: str
    session_index: int = 0
    last_end_commit: Optional[str] = None


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.selftest:
        run_selftest(limit=args.limit or 2, results_root=Path(args.results_root), seed=args.seed)
        return 0

    if args.conditions or args.dry_run:
        if not args.sequence_records:
            raise SystemExit("--sequence-records is required with --conditions or --dry-run")
        try:
            conditions = _parse_conditions(args.conditions or "B0,DF", include_live_baselines=args.include_live_baselines)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        run_sequence_conditions(
            conditions=conditions,
            model=args.model,
            judge_model=args.judge_model,
            sequence_records_path=Path(args.sequence_records),
            limit=args.limit,
            seq=args.seq,
            results_root=Path(args.results_root),
            seed=args.seed,
            dry_run=bool(args.dry_run),
            include_live_baselines=args.include_live_baselines,
        )
        return 0

    if not args.condition:
        raise SystemExit("--condition is required unless --selftest, --dry-run, or --conditions is used")
    run_benchmark(
        condition=args.condition,
        model=args.model,
        judge_model=args.judge_model,
        limit=args.limit,
        seq=args.seq,
        sequence_records_path=Path(args.sequence_records) if args.sequence_records else None,
        results_root=Path(args.results_root),
        seed=args.seed,
        include_live_baselines=args.include_live_baselines,
    )
    return 0


def run_selftest(
    *,
    limit: int = 2,
    results_root: Path = RESULTS_ROOT,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    tasks = _select_tasks(load_env.load_tasks(), limit=limit, seq=None)
    policy = create_policy("B7")
    agent = StubAgent()
    run_id = _run_id("SELFTEST", "stub")
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = _execute_sequence(
        tasks=tasks,
        condition="B7",
        model="stub",
        policy=policy,
        agent=agent,
        run_dir=run_dir,
        seed=seed,
        materialize=False,
        oracle_mode="stub",
    )
    payload["run_type"] = "selftest"
    payload["selftest"] = True
    _write_outputs(payload, run_dir)
    return payload


def run_benchmark(
    *,
    condition: str,
    model: str,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    limit: Optional[int] = None,
    seq: Optional[str] = None,
    sequence_records_path: Optional[Path] = None,
    results_root: Path = RESULTS_ROOT,
    seed: int = DEFAULT_SEED,
    include_live_baselines: bool = False,
) -> Dict[str, Any]:
    _scrub_agent_readable_hidden_logs()
    raw_tasks = load_env.load_sequence_records(sequence_records_path) if sequence_records_path else load_env.load_tasks()
    tasks = _select_tasks(raw_tasks, limit=limit, seq=seq)
    normalized = _normalize_condition(condition, include_live_baselines=include_live_baselines)
    run_id = _run_id(normalized, model)
    judge_client = _judge_client_for_condition(normalized, judge_model=judge_model, dry_run=False, include_live_baselines=include_live_baselines)
    policy = _policy_for_condition(
        normalized,
        judge_client=judge_client,
        include_live_baselines=include_live_baselines,
        run_id=run_id,
        seed=seed,
    )
    agent = _agent_for_model(model)
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = _execute_sequence(
        tasks=tasks,
        condition=normalized,
        model=model,
        policy=policy,
        agent=agent,
        run_dir=run_dir,
        seed=seed,
        materialize=True,
        oracle_mode="real",
        judge_usage_source=judge_client,
    )
    payload["run_type"] = "benchmark"
    payload["selftest"] = False
    payload["judge"] = _judge_usage_snapshot(judge_client)
    _write_outputs(payload, run_dir)
    return payload


def run_sequence_conditions(
    *,
    conditions: Sequence[str],
    model: str,
    judge_model: str,
    sequence_records_path: Path,
    limit: Optional[int] = None,
    seq: Optional[str] = None,
    results_root: Path = RESULTS_ROOT,
    seed: int = DEFAULT_SEED,
    dry_run: bool = False,
    include_live_baselines: bool = False,
    cleanup_worktrees: bool = True,
) -> Dict[str, Any]:
    if not dry_run:
        _scrub_agent_readable_hidden_logs()
    raw_sequences = load_env.load_sequence_records(sequence_records_path)
    sequences = _select_tasks(raw_sequences, limit=limit, seq=seq)
    if not _has_sequence_records(sequences):
        raise ValueError("--conditions requires sequence records with sessions[]")

    run_model = "stub" if dry_run else model
    run_kind = "memory_trap_dry_run" if dry_run else "memory_trap_slice"
    parent_run_id = _run_id("SLICE-DRYRUN" if dry_run else "SLICE", run_model)
    parent_dir = results_root / parent_run_id
    parent_dir.mkdir(parents=True, exist_ok=True)

    condition_payloads: Dict[str, Dict[str, Any]] = {}
    condition_dirs: Dict[str, Path] = {}
    for condition in conditions:
        normalized = _normalize_condition(condition, include_live_baselines=include_live_baselines)
        judge_client = _judge_client_for_condition(
            normalized,
            judge_model=judge_model,
            dry_run=dry_run,
            include_live_baselines=include_live_baselines,
        )
        policy = _policy_for_condition(
            normalized,
            judge_client=judge_client,
            include_live_baselines=include_live_baselines,
            run_id=parent_run_id,
            seed=seed,
        )
        agent = StubAgent() if dry_run else _agent_for_model(model)
        run_dir = parent_dir / normalized
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = _execute_sequence(
            tasks=sequences,
            condition=normalized,
            model=run_model,
            policy=policy,
            agent=agent,
            run_dir=run_dir,
            seed=seed,
            materialize=True,
            oracle_mode="stub" if dry_run else "real",
            cleanup_worktrees=cleanup_worktrees,
            judge_usage_source=judge_client,
        )
        payload["run_type"] = run_kind
        payload["selftest"] = False
        payload["dry_run"] = bool(dry_run)
        payload["judge"] = _judge_usage_snapshot(judge_client)
        _write_outputs(payload, run_dir)
        condition_payloads[normalized] = payload
        condition_dirs[normalized] = run_dir

    gate_report = _build_gate_report(
        parent_run_id=parent_run_id,
        run_type=run_kind,
        dry_run=dry_run,
        sequence_records_path=sequence_records_path,
        sequences=sequences,
        condition_payloads=condition_payloads,
        condition_dirs=condition_dirs,
        model=run_model,
        judge_model=judge_model,
        seed=seed,
    )
    _write_gate_report(gate_report, parent_dir)
    return {
        "run_dir": str(parent_dir),
        "run_type": run_kind,
        "dry_run": bool(dry_run),
        "conditions": condition_payloads,
        "gate_report": gate_report,
    }


def _condition_choices(
    *,
    include_live_baselines: bool = False,
    include_ablations: bool = False,
) -> List[str]:
    choices = set(BASELINE_REGISTRY) | {DF_CONDITION} | DF_VARIANT_CONDITIONS
    if include_ablations:
        choices.update(DF_ABLATIONS)
        choices.add(DF_STRICT_CONDITION)
    if include_live_baselines:
        choices.update(LIVE_BASELINE_CONDITIONS)
    return sorted(choices)


def _normalize_condition(condition: str, *, include_live_baselines: bool = False) -> str:
    raw_name = str(condition or "").strip()
    name = raw_name.upper()
    live_choices = {choice.upper(): choice for choice in LIVE_BASELINE_CONDITIONS}
    if name in live_choices and not include_live_baselines:
        raise ValueError(f"{live_choices[name]} requires --include-live-baselines")
    choices = _condition_choices(
        include_live_baselines=include_live_baselines,
        include_ablations=True,
    )
    canonical_choices = {choice.upper(): choice for choice in choices}
    if name not in canonical_choices:
        known = ", ".join(choices)
        raise ValueError(f"unknown condition {condition!r}; expected one of {known}")
    return canonical_choices[name]


def _parse_conditions(value: str, *, include_live_baselines: bool = False) -> List[str]:
    conditions = [
        _normalize_condition(part, include_live_baselines=include_live_baselines)
        for part in str(value or "").split(",")
        if part.strip()
    ]
    if not conditions:
        raise ValueError("--conditions must include at least one condition")
    seen: set[str] = set()
    out: List[str] = []
    for condition in conditions:
        if condition not in seen:
            out.append(condition)
            seen.add(condition)
    return out


def _scrub_agent_readable_hidden_logs() -> Dict[str, Any]:
    """Remove stale temp Codex/validation logs that contain hidden contracts.

    The wake jail denies host temp reads, but stale answer-key logs should not
    remain in common temp roots for later non-hermetic probes or failed runs.
    This scrub is intentionally narrow: it only unlinks temp log files matching
    known validation/codex log shapes and containing hidden benchmark markers.
    """

    roots = _temp_roots_for_scrub()
    removed: List[str] = []
    errors: List[str] = []
    for root in roots:
        for pattern in TEMP_HIDDEN_LOG_GLOBS:
            for path in root.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    errors.append(f"{path}: read failed: {exc}")
                    continue
                if not any(marker in text for marker in HIDDEN_LOG_MARKERS):
                    continue
                try:
                    path.unlink()
                    removed.append(str(path))
                except OSError as exc:
                    errors.append(f"{path}: unlink failed: {exc}")
    return {"removed": removed, "errors": errors}


def _temp_roots_for_scrub() -> List[Path]:
    roots: List[Path] = []
    for value in {tempfile.gettempdir(), os.environ.get("TMPDIR") or ""}:
        if not value:
            continue
        try:
            path = Path(value).resolve()
        except OSError:
            continue
        if path.exists() and path not in roots:
            roots.append(path)
    return roots


def _policy_for_condition(
    condition: str,
    *,
    judge_client: Optional[Any] = None,
    include_live_baselines: bool = False,
    run_id: Optional[str] = None,
    seed: Optional[int] = None,
) -> MemoryPolicy:
    normalized = _normalize_condition(condition, include_live_baselines=include_live_baselines)
    if normalized == DF_CONDITION:
        if judge_client is None:
            raise ValueError("DF requires an LLMJudge complete() client")
        return DreamForgePolicy(
            name=DF_CONDITION,
            label="DreamForgeFull",
            description=(
                "DreamForge full with typed consolidation, contradiction repair, "
                "counterfactual replay, local maintenance, and retrieval gating."
            ),
            llm_judge=LLMJudge(complete=judge_client),
            maintenance_scope="local",
        )
    if normalized == DF_STRICT_CONDITION:
        if judge_client is None:
            raise ValueError(f"{DF_STRICT_CONDITION} requires an LLMJudge complete() client")
        return DreamForgePolicy(
            name=DF_STRICT_CONDITION,
            label="DreamForgeStrict",
            description=f"DreamForge full with {DF_STRICT_DESCRIPTION}.",
            llm_judge=LLMJudge(complete=judge_client),
            maintenance_scope="local",
            **DF_STRICT_RETRIEVAL_CONFIG,
        )
    if normalized == DF_HYBRID_CONDITION:
        if judge_client is None:
            raise ValueError(f"{DF_HYBRID_CONDITION} requires an LLMJudge complete() client")
        return DreamForgePolicy(
            name=DF_HYBRID_CONDITION,
            label="DreamForgeHybrid",
            description=(
                "DreamForge full with typed consolidation plus pipeline-level raw evidence "
                "and contradiction records excluded from implementation reads."
            ),
            llm_judge=LLMJudge(complete=judge_client),
            maintenance_scope="local",
            enable_raw_evidence=True,
            exclude_contradiction_from_read=True,
        )
    if normalized == DF_RAW_ONLY_CONDITION:
        if judge_client is None:
            raise ValueError(f"{DF_RAW_ONLY_CONDITION} requires an LLMJudge complete() client")
        return DreamForgePolicy(
            name=DF_RAW_ONLY_CONDITION,
            label="DreamForgeRawOnly",
            description=(
                "DreamForge raw-evidence-only honesty control with typed consolidation disabled "
                "and contradiction records excluded from implementation reads."
            ),
            llm_judge=LLMJudge(complete=judge_client),
            maintenance_scope="local",
            enable_consolidation=False,
            enable_raw_evidence=True,
            exclude_contradiction_from_read=True,
        )
    if normalized == DF_STRICT_HYBRID_CONDITION:
        if judge_client is None:
            raise ValueError(f"{DF_STRICT_HYBRID_CONDITION} requires an LLMJudge complete() client")
        return DreamForgePolicy(
            name=DF_STRICT_HYBRID_CONDITION,
            label="DreamForgeStrictHybrid",
            description=(
                f"DreamForge hybrid with {DF_STRICT_DESCRIPTION}."
            ),
            llm_judge=LLMJudge(complete=judge_client),
            maintenance_scope="local",
            enable_raw_evidence=True,
            exclude_contradiction_from_read=True,
            **DF_STRICT_RETRIEVAL_CONFIG,
        )
    if normalized in DF_ABLATIONS:
        if judge_client is None:
            raise ValueError(f"{normalized} requires an LLMJudge complete() client")
        return DreamForgePolicy(
            name=normalized,
            label=f"DF-{normalized}",
            description=f"DreamForge ablation {normalized}: {DF_ABLATION_DESCRIPTIONS[normalized]}.",
            llm_judge=LLMJudge(complete=judge_client),
            maintenance_scope="local",
            **DF_ABLATIONS[normalized],
        )
    if normalized == MEM0_CONDITION:
        return Mem0Policy(
            name=MEM0_CONDITION,
            label="Mem0",
            description="Real mem0ai memory baseline with offline-safe no-op fallback.",
            run_id=run_id,
            condition_id=MEM0_CONDITION,
            seed=seed,
        )
    if normalized == MEM0_LITERAL_CONDITION:
        return Mem0LiteralPolicy(
            name=MEM0_LITERAL_CONDITION,
            label="Mem0Literal",
            description=(
                "Real mem0ai baseline configured for literal/raw-preserving storage: "
                "LLM fact extraction is bypassed via infer=False and the sanitized raw "
                "episode text is stored as the memory."
            ),
            run_id=run_id,
            condition_id=MEM0_LITERAL_CONDITION,
            seed=seed,
        )
    return create_policy(normalized)


def _judge_client_for_condition(
    condition: str,
    *,
    judge_model: str,
    dry_run: bool,
    include_live_baselines: bool = False,
) -> Optional[Any]:
    normalized = _normalize_condition(condition, include_live_baselines=include_live_baselines)
    if (
        normalized != DF_CONDITION
        and normalized != DF_STRICT_CONDITION
        and normalized not in DF_VARIANT_CONDITIONS
        and normalized not in DF_ABLATIONS
    ):
        return None
    if dry_run:
        return StubJudgeClient()
    if judge_model.startswith("cc2-"):
        from experiments.cc2_judge_client import Cc2JudgeClient

        return Cc2JudgeClient(model=_cc2_judge_model(judge_model))
    if judge_model in CODEX_JUDGE_MODELS:
        from experiments.codex_judge_client import CodexJudgeClient

        return CodexJudgeClient(model=CODEX_JUDGE_MODELS[judge_model])
    from experiments.grid_judge_client import GridJudgeClient

    return GridJudgeClient(model=judge_model)


def _cc2_judge_model(judge_model: str) -> str:
    return CC2_JUDGE_MODELS.get(judge_model, judge_model.replace("cc2-", "claude-", 1))


class StubJudgeClient:
    """Deterministic offline complete(prompt)->str client for DF dry-runs."""

    def __init__(self) -> None:
        self.model = "stub-judge"
        self.calls = 0
        self.calls_by_tag = {"consolidation": 0, "contradiction": 0, "replay": 0}
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        tag = self._tag(prompt)
        self.calls_by_tag[tag] = int(self.calls_by_tag.get(tag, 0)) + 1
        if tag == "consolidation":
            response = self._consolidation_response(prompt)
        elif tag == "contradiction":
            response = json.dumps(
                {
                    "relation": "supersedes",
                    "old_status": "superseded",
                    "new_status": "active",
                    "scope_delta": "local sequence scope",
                    "rationale": "Deterministic dry-run repair links the newer event to the older scoped memory.",
                },
                sort_keys=True,
            )
        elif tag == "replay":
            response = self._replay_response(prompt)
        else:
            response = "[]"
        prompt_tokens = _token_count(prompt)
        completion_tokens = _token_count(response)
        self.total_usage["prompt_tokens"] += prompt_tokens
        self.total_usage["completion_tokens"] += completion_tokens
        self.total_usage["total_tokens"] += prompt_tokens + completion_tokens
        return response

    @staticmethod
    def _tag(prompt: str) -> str:
        if "CONSOLIDATION_JUDGE_REQUEST" in prompt:
            return "consolidation"
        if "CONTRADICTION_JUDGE_REQUEST" in prompt:
            return "contradiction"
        if "REPLAY_JUDGE_REQUEST" in prompt:
            return "replay"
        return "unknown"

    def _consolidation_response(self, prompt: str) -> str:
        episode = _extract_prompt_json(prompt, "RAW EPISODE:")
        events = list(episode.get("injected_memory_events") or [])
        if not events and isinstance(episode.get("injected_memory_event"), Mapping):
            events = [dict(episode["injected_memory_event"])]
        memories: List[Dict[str, Any]] = []
        for index, event in enumerate(events or [{}], start=1):
            scope = dict(event.get("scope") or {}) if isinstance(event, Mapping) else {}
            content = str(event.get("content") or episode.get("prompt") or "Dry-run consolidation memory.")
            memory_type = str(event.get("memory_type") or event.get("kind") or "human_feedback")
            if memory_type not in {
                "episodic",
                "semantic_project",
                "procedural",
                "failure",
                "human_feedback",
                "constraint",
                "contradiction",
                "dream_artifact",
            }:
                memory_type = "failure" if memory_type == "failure_diagnosis" else "human_feedback"
            memories.append(
                {
                    "memory_type": memory_type,
                    "content": content,
                    "scope": {
                        "repo": scope.get("sequence_id") or episode.get("repo_scope") or episode.get("sequence_id"),
                        "files": scope.get("files") or episode.get("file_paths") or [],
                        "symbols": scope.get("symbols") or [],
                        "session_validity": "future",
                    },
                    "provenance": {
                        "trajectory_id": episode.get("trajectory_id"),
                        "event_id": event.get("event_id") or f"dry-run-event-{index}",
                        "quote": content[:240],
                    },
                    "confidence": 0.9,
                    "risk_score": 0.1,
                    "staleness_score": 0.0,
                    "retrieval_tags": ["stub-judge", str(episode.get("sequence_id") or ""), memory_type],
                }
            )
        return json.dumps(memories, sort_keys=True)

    def _replay_response(self, prompt: str) -> str:
        episode = _extract_prompt_json(prompt, "EPISODE:")
        event = {}
        events = episode.get("injected_memory_events")
        if isinstance(events, list) and events:
            event = dict(events[0]) if isinstance(events[0], Mapping) else {}
        elif isinstance(episode.get("injected_memory_event"), Mapping):
            event = dict(episode["injected_memory_event"])
        content = str(event.get("content") or episode.get("error_type") or "")
        if not content:
            return "{}"
        scope = dict(event.get("scope") or {}) if isinstance(event.get("scope"), Mapping) else {}
        return json.dumps(
            {
                "bad_action": content[:180],
                "evidence": "Dry-run replay derived from the injected event attached to this session.",
                "correct_alternative": content[:240],
                "future_retrieval_condition": str(episode.get("sequence_id") or episode.get("repo_scope") or ""),
                "scope": {
                    "repo": scope.get("sequence_id") or episode.get("repo_scope") or episode.get("sequence_id"),
                    "files": scope.get("files") or episode.get("file_paths") or [],
                    "symbols": scope.get("symbols") or [],
                },
            },
            sort_keys=True,
        )


def _extract_prompt_json(prompt: str, marker: str) -> Dict[str, Any]:
    if marker not in prompt:
        return {}
    tail = prompt.split(marker, 1)[1].strip()
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(tail)
    except json.JSONDecodeError:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _execute_sequence(
    *,
    tasks: Sequence[Mapping[str, Any]],
    condition: str,
    model: str,
    policy: MemoryPolicy,
    agent: Any,
    run_dir: Path,
    seed: int,
    materialize: bool,
    oracle_mode: str,
    cleanup_worktrees: bool = True,
    judge_usage_source: Optional[Any] = None,
) -> Dict[str, Any]:
    random.seed(seed)
    trajectories_dir = run_dir / "trajectories"
    trajectories_dir.mkdir(parents=True, exist_ok=True)
    agent_worktrees_root: Optional[Path] = None
    scorer_root: Optional[Path] = None
    continuation_root: Optional[Path] = None
    sequence_mode = _has_sequence_records(tasks)
    sequence_states: Dict[str, SequenceState] = {}
    if materialize:
        agent_worktrees_root = Path(tempfile.mkdtemp(prefix=f"dreambench-agent-{run_dir.name}-"))
        scorer_root = Path(tempfile.mkdtemp(prefix=f"dreambench-scorer-{run_dir.name}-"))
        if sequence_mode:
            continuation_root = Path(tempfile.mkdtemp(prefix=f"dreambench-state-{run_dir.name}-"))

    records: List[Dict[str, Any]] = []
    wake_input_tokens = 0
    wake_output_tokens = 0
    sleep_tokens = 0
    judge_tokens = 0
    wake_latency = 0.0
    sleep_latency = 0.0
    judge_latency = 0.0
    admitted_memory_tokens_total = 0
    last_judge_usage_total = _judge_usage_total(judge_usage_source)

    stale_available_count = 0
    stale_activation_count = 0
    scope_correct_count = 0
    retrieved_count = 0
    useful_memory_count = 0
    harmful_memory_count = 0
    repeated_error_events = 0
    repeated_error_opportunities = 0
    seen_errors_by_sequence: Dict[str, set[str]] = {}
    started_at = datetime.now(timezone.utc).isoformat()

    def snapshot() -> Dict[str, Any]:
        memory_items = list(policy.memory_items())
        memory_snapshot = _sanitize_for_agent([_memory_item_dict(item) for item in memory_items])
        wake_tokens = wake_input_tokens + wake_output_tokens
        model_cost = _estimate_cost(model, wake_input_tokens, wake_output_tokens)
        sleep_cost = _estimate_sleep_cost(sleep_tokens)
        total_cost = model_cost + sleep_cost
        metrics = _compute_metrics(
            records=records,
            memory_items=memory_items,
            wake_tokens=wake_tokens,
            sleep_tokens=sleep_tokens,
            judge_tokens=judge_tokens,
            wake_latency=wake_latency,
            sleep_latency=sleep_latency,
            judge_latency=judge_latency,
            total_cost=total_cost,
            repeated_error_events=repeated_error_events,
            repeated_error_opportunities=repeated_error_opportunities,
            stale_activation_count=stale_activation_count,
            stale_available_count=stale_available_count,
            harmful_memory_count=harmful_memory_count,
            useful_memory_count=useful_memory_count,
            retrieved_count=retrieved_count,
            scope_correct_count=scope_correct_count,
            admitted_memory_tokens_total=admitted_memory_tokens_total,
        )
        slice_report = None
        if sequence_mode:
            slice_report = score_memory_trap_slice(
                sequence_records=tasks,
                records=records,
                condition=condition,
                memory_snapshot=memory_snapshot,
            )
            metrics.update(headline_metric_values(slice_report))
        manifest = {
            "run_id": run_dir.name,
            "created_at": started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "condition": condition,
            "policy": policy.describe(),
            "tasks": [record["task"]["id"] for record in records],
            "task_order": [record["task"]["id"] for record in records],
            "task_count": len(records),
            "sequence_mode": sequence_mode,
            "seed": seed,
            "budget": {
                "max_model_calls_per_task": DEFAULT_MAX_MODEL_CALLS,
                "oracle_timeout_seconds": ORACLE_TIMEOUT_SECONDS,
            },
            "tokens": {
                "wake_input": wake_input_tokens,
                "wake_output": wake_output_tokens,
                "wake": wake_tokens,
                "sleep": sleep_tokens,
                "judge": judge_tokens,
                "admitted_memory_total": admitted_memory_tokens_total,
                "admitted_memory_per_task": admitted_memory_tokens_total / len(records) if records else 0.0,
                "total": wake_tokens + sleep_tokens + judge_tokens,
            },
            "latency_seconds": {
                "wake": wake_latency,
                "sleep": sleep_latency,
                "judge": judge_latency,
                "total": wake_latency + sleep_latency + judge_latency,
            },
            "estimated_cost": total_cost,
            "estimated_cost_breakdown": {
                "wake_model": model_cost,
                "sleep": sleep_cost,
            },
            "token_price_per_million": TOKEN_PRICE_PER_MILLION.get(model, {"input": 0.0, "output": 0.0}),
            "worktree_storage": "tempfile-outside-results" if materialize else "not-materialized",
            "slice_scorer": slice_report["scorer"] if slice_report else None,
            "slice_label_judge": slice_report["judge"] if slice_report else None,
            "baseline_registry": {
                name: {"label": spec.label, "description": spec.description}
                for name, spec in BASELINE_REGISTRY.items()
            },
        }
        return {
            "manifest": manifest,
            "metrics": metrics,
            "records": records,
            "memory_snapshot": memory_snapshot,
            "slice_report": slice_report,
            "counts": {
                "tasks": len(records),
                "successes": sum(1 for record in records if record["final_passed"]),
                "pass_at_1": sum(1 for record in records if record.get("pass_at_1")),
                "contaminated": sum(1 for record in records if record.get("contaminated")),
                "retrieved_memories": retrieved_count,
                "harmful_memories": harmful_memory_count,
                "useful_memories": useful_memory_count,
                "sleep_writes": sum(len(record.get("memory_writes", [])) for record in records),
            },
        }

    def flush() -> Dict[str, Any]:
        payload = snapshot()
        _write_outputs(payload, run_dir)
        return payload

    try:
        run_items = _ordered_run_items(tasks)
        for ordinal, task in enumerate(run_items, start=1):
            public_task = _public_task(task)
            task_id = public_task["id"]
            worktree: Optional[Path] = None
            sequence_state: Optional[SequenceState] = None
            continuation_record: Dict[str, Any] = {}
            record: Dict[str, Any]
            try:
                try:
                    if _is_sequence_session_task(task):
                        if not materialize:
                            raise RuntimeError("sequence continuation requires materialized worktrees")
                        if continuation_root is None:
                            raise RuntimeError("missing continuation root")
                        sequence_state = sequence_states.get(str(task["seq_id"]))
                        if sequence_state is None:
                            sequence_state = _initialize_sequence_state(
                                condition=condition,
                                sequence=task["_sequence_record"],
                                root=continuation_root,
                                allow_synthetic=oracle_mode == "stub",
                            )
                            sequence_states[sequence_state.seq_id] = sequence_state
                        start_commit = sequence_state.current_commit
                        previous_end_commit = sequence_state.last_end_commit
                        session_index = int(task.get("session_index") or 0)
                        continuation_record = {
                            "start_commit": start_commit,
                            "end_commit": start_commit,
                            "started_from_previous_session": bool(session_index > 1 and previous_end_commit == start_commit),
                            "previous_session_end_commit": previous_end_commit,
                            "forbidden_fresh_base_ref_used": False,
                            "oracle_id": task.get("oracle_id"),
                            "continuation_advanced": False,
                            "continuation_source": "state_repo",
                        }
                    if materialize:
                        if oracle_mode != "stub":
                            load_env.validate_oracle_cmd(task.get("oracle_cmd"))
                        if agent_worktrees_root is None:
                            raise RuntimeError("missing agent worktree root")
                        worktree = agent_worktrees_root / f"{ordinal:03d}-{_safe_name(task_id)}"
                        if sequence_state is not None:
                            load_env.materialize(sequence_state.state_repo, continuation_record["start_commit"], dest=worktree)
                        else:
                            load_env.materialize(task["repo"], task["base_ref"], dest=worktree)
                        public_task["worktree"] = str(worktree)

                    baseline_task = _baseline_read_task(public_task, task)
                    memory_context = _sanitize_for_agent(policy.read(baseline_task))
                    admitted_tokens = _token_count(json.dumps(_jsonable(memory_context), sort_keys=True))
                    admitted_memory_tokens_total += admitted_tokens
                    retrieved_count += len(memory_context)
                    scope_correct_count += sum(1 for item in memory_context if _scope_matches(item, public_task))
                    harmful_memory_count += sum(1 for item in memory_context if _is_harmful_memory(item))
                    useful_memory_count += sum(1 for item in memory_context if _is_useful_memory(item, public_task))
                    if _has_stale_available(policy):
                        stale_available_count += 1
                    stale_activation_count += sum(1 for item in memory_context if _is_stale_memory(item))

                    wake_start = time.perf_counter()
                    agent_result = agent.run_task(public_task, memory_context)
                    wake_elapsed = time.perf_counter() - wake_start
                    wake_latency += wake_elapsed
                    agent_metadata = _sanitize_for_agent(getattr(agent, "last_run", {}))
                    isolation_mode = str(agent_metadata.get("isolation_mode") or "unknown")
                    input_tokens, output_tokens = _agent_usage_tokens(agent_result, agent_metadata)
                    wake_input_tokens += input_tokens
                    wake_output_tokens += output_tokens

                    if oracle_mode == "stub":
                        oracle_result = _stub_oracle(task, agent_result, condition=condition)
                        if sequence_state is not None:
                            continuation_advanced = _advance_sequence_state_dry_run(sequence_state, task_id=task_id)
                            continuation_record["continuation_advanced"] = continuation_advanced
                            continuation_record["end_commit"] = sequence_state.current_commit
                    else:
                        if scorer_root is None:
                            raise RuntimeError("real oracle mode requires a scorer root")
                        oracle_start = time.perf_counter()
                        score_repo = sequence_state.state_repo if sequence_state is not None else task["repo"]
                        score_base_ref = continuation_record["start_commit"] if sequence_state is not None else str(task["base_ref"])
                        oracle_result = load_env.score_agent_diff(
                            repo=score_repo,
                            base_ref=score_base_ref,
                            agent_diff=agent_result.patch,
                            oracle_cmd=str(task["oracle_cmd"]),
                            scorer_root=scorer_root,
                            timeout_seconds=ORACLE_TIMEOUT_SECONDS,
                        )
                        judge_latency += time.perf_counter() - oracle_start
                        public_task["files"] = list(oracle_result.get("production_files") or [])
                        if sequence_state is not None:
                            continuation_advanced = _advance_sequence_state(
                                sequence_state,
                                production_diff=str(oracle_result.get("production_diff") or ""),
                                oracle_result=oracle_result,
                                task_id=task_id,
                            )
                            continuation_record["continuation_advanced"] = continuation_advanced
                            continuation_record["end_commit"] = sequence_state.current_commit

                    final_passed = bool(oracle_result["passed"])
                    pass_at_1 = bool(final_passed and agent_result.passed and not agent_result.error_type)
                    error_type = _record_error_type(agent_result, oracle_result, final_passed, task)
                    error_detail = _record_error_detail(
                        error_type=error_type,
                        agent_result=agent_result,
                        agent_metadata=agent_metadata,
                        oracle_result=oracle_result,
                    )
                    repeated_error_events, repeated_error_opportunities = _update_repeated_errors(
                        seen_errors_by_sequence,
                        public_task,
                        error_type,
                        repeated_error_events,
                        repeated_error_opportunities,
                    )

                    trajectory = _trajectory_for_record(
                        task=public_task,
                        model=model,
                        condition=condition,
                        seed=seed,
                        agent_result=agent_result,
                        agent_metadata=agent_metadata,
                        memory_context=memory_context,
                        oracle_result=oracle_result,
                        final_passed=final_passed,
                        error_type=error_type,
                        error_detail=error_detail,
                        injected_events=_events_for_session(task),
                    )

                    sleep_error: Optional[str] = None
                    sleep_start = time.perf_counter()
                    try:
                        policy.write(trajectory)
                    except Exception as exc:  # noqa: BLE001 - per-task accounting must survive policy failures.
                        sleep_error = f"{type(exc).__name__}: {exc}"
                    sleep_latency += time.perf_counter() - sleep_start
                    sleep_tokens += int(getattr(policy, "last_sleep_tokens", 0) or 0)
                    current_judge_usage_total = _judge_usage_total(judge_usage_source)
                    judge_tokens += max(0, current_judge_usage_total - last_judge_usage_total)
                    last_judge_usage_total = current_judge_usage_total
                    trajectory.memory_writes = [item.id for item in getattr(policy, "last_write_items", [])]
                    trajectory.raw_episode["memory_writes"] = list(trajectory.memory_writes)
                    trajectory.raw_episode["sleep_write_count"] = getattr(policy, "last_write_count", 0)
                    if sleep_error:
                        trajectory.raw_episode["sleep_error"] = sleep_error

                    record = {
                        "ordinal": ordinal,
                        "task": public_task,
                        "agent_result": _task_result_dict(agent_result),
                        "agent_metadata": agent_metadata,
                        "isolation_mode": isolation_mode,
                        "memory_context": memory_context,
                        "admitted_memory_tokens": admitted_tokens,
                        "retrieval_decisions": _sanitize_for_agent(list(getattr(policy, "last_retrieval_decisions", []))),
                        "memory_writes": _sanitize_for_agent([_memory_item_dict(item) for item in getattr(policy, "last_write_items", [])]),
                        "oracle": _oracle_public(oracle_result),
                        "score": _score_public(oracle_result),
                        "final_passed": final_passed,
                        "pass_at_1": pass_at_1,
                        "error_type": error_type,
                        "error_detail": error_detail,
                        "sleep_error": sleep_error,
                        "trajectory": _sanitize_for_agent(trajectory.to_dict()),
                        **continuation_record,
                    }
                except Exception as exc:  # noqa: BLE001 - one bad task must not kill the run.
                    if sequence_state is not None:
                        continuation_record["end_commit"] = sequence_state.current_commit
                    error_type = f"task_exception:{type(exc).__name__}"
                    error_detail = _error_detail(exc)
                    repeated_error_events, repeated_error_opportunities = _update_repeated_errors(
                        seen_errors_by_sequence,
                        public_task,
                        error_type,
                        repeated_error_events,
                        repeated_error_opportunities,
                    )
                    record = {
                        "ordinal": ordinal,
                        "task": public_task,
                        "agent_result": _task_result_dict(
                            TaskResult(
                                patch="",
                                passed=False,
                                steps=0,
                                tokens=0,
                                error_type=error_type,
                                error_detail=error_detail,
                            )
                        ),
                        "agent_metadata": _sanitize_for_agent(getattr(agent, "last_run", {})),
                        "isolation_mode": str(
                            (_sanitize_for_agent(getattr(agent, "last_run", {})) or {}).get("isolation_mode") or "unknown"
                        ),
                        "memory_context": [],
                        "admitted_memory_tokens": 0,
                        "retrieval_decisions": [],
                        "memory_writes": [],
                        "oracle": {
                            "passed": False,
                            "exit_code": None,
                            "stdout": "",
                            "stderr": error_detail,
                        },
                        "score": {"passed": False, "error_type": error_type, "error_detail": error_detail},
                        "final_passed": False,
                        "pass_at_1": False,
                        "error_type": error_type,
                        "error_detail": error_detail,
                        "trajectory": {},
                        **continuation_record,
                    }
                if sequence_mode and isinstance(task.get("_sequence_record"), Mapping):
                    contamination = scan_record(record, task["_sequence_record"])
                else:
                    contamination = {"contaminated": False, "evidence": []}
                record["contaminated"] = bool(contamination.get("contaminated"))
                record["contamination_evidence"] = list(contamination.get("evidence") or [])
                records.append(record)
                (trajectories_dir / f"{ordinal:03d}-{_safe_name(task_id)}.json").write_text(
                    json.dumps(_jsonable(record), indent=2, sort_keys=True, allow_nan=False),
                    encoding="utf-8",
                )
                flush()
            finally:
                if cleanup_worktrees and worktree is not None:
                    shutil.rmtree(worktree, ignore_errors=True)

        return flush()
    finally:
        if cleanup_worktrees:
            if agent_worktrees_root is not None:
                shutil.rmtree(agent_worktrees_root, ignore_errors=True)
            if scorer_root is not None:
                shutil.rmtree(scorer_root, ignore_errors=True)
            if continuation_root is not None:
                shutil.rmtree(continuation_root, ignore_errors=True)


class _LiveBaselineArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        for condition in sorted(LIVE_BASELINE_CONDITIONS, key=len, reverse=True):
            if condition in message:
                self.exit(2, f"{self.prog}: error: {condition} requires --include-live-baselines\n")
        super().error(message)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    include_live_baselines = "--include-live-baselines" in raw_argv
    parser = _LiveBaselineArgumentParser(description="Run DreamBench-SWE experiments.")
    parser.add_argument(
        "--condition",
        choices=_condition_choices(
            include_live_baselines=include_live_baselines,
            include_ablations=True,
        ),
        help=(
            "Memory condition B0..B7, DF, DF-hybrid, DF-raw-only, "
            "DF-strict, DF-strict-hybrid, or a DF ablation A0/A2/A4/A5/A6/A11."
        ),
    )
    parser.add_argument("--conditions", help="Comma-separated sequence-slice conditions, e.g. B0,DF,B1,B3.")
    parser.add_argument(
        "--include-live-baselines",
        action="store_true",
        help="Enable live third-party baselines such as B5-MEM0 and B5-MEM0-LIT.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.5",
        help="Coding agent model: codex/gpt-5.5 or grid:glm-latest/grid:kimi-latest.",
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        choices=sorted(JUDGE_MODELS),
        help="GRID, cc2, or Codex judge model for DF sleep judges.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run sequence slice with StubAgent and deterministic stub judge.")
    parser.add_argument("--limit", type=int, help="Maximum number of tasks to run.")
    parser.add_argument("--seq", help="Optional seq_id filter, comma-separated.")
    parser.add_argument("--sequence-records", help="Optional memory-trap sequence JSON/JSONL path.")
    parser.add_argument("--selftest", action="store_true", help="Run the in-process StubAgent selftest.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--results-root", default=str(default_results_root()), help=argparse.SUPPRESS)
    return parser.parse_args(raw_argv)


def _agent_for_model(model: str) -> Any:
    normalized = model.strip()
    if normalized in {"codex", "gpt-5.5"}:
        return CodexAgent("gpt-5.5", timeout_seconds=300.0)
    if normalized.startswith("grid:"):
        return GridAgent(normalized, max_steps=DEFAULT_MAX_MODEL_CALLS)
    if normalized in {"glm-latest", "kimi-latest"}:
        return GridAgent(normalized, max_steps=DEFAULT_MAX_MODEL_CALLS)
    raise ValueError("model must be one of: codex, gpt-5.5, grid:glm-latest, grid:kimi-latest")


def _select_tasks(tasks: Sequence[Mapping[str, Any]], *, limit: Optional[int], seq: Optional[str]) -> List[Mapping[str, Any]]:
    selected = list(tasks)
    sequence_mode = _has_sequence_records(selected)
    if seq:
        wanted = {item.strip() for item in seq.split(",") if item.strip()}
        selected = [task for task in selected if str(task.get("seq_id") or task.get("sequence_id")) in wanted]
    if sequence_mode:
        selected.sort(key=lambda task: str(task.get("seq_id") or task.get("sequence_id") or ""))
        if limit is not None:
            selected = selected[: max(0, int(limit))]
        return selected
    selected.sort(key=lambda task: (str(task.get("seq_id", "")), int(task.get("session_index") or 0)))
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    return selected


def _has_sequence_records(records: Sequence[Mapping[str, Any]]) -> bool:
    return any(isinstance(record.get("sessions"), list) for record in records)


def _ordered_run_items(records: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    if not _has_sequence_records(records):
        return list(records)
    items: List[Mapping[str, Any]] = []
    for sequence in sorted(records, key=lambda item: str(item.get("seq_id") or item.get("sequence_id") or "")):
        sessions = sequence.get("sessions")
        if not isinstance(sessions, list):
            raise ValueError(f"sequence {sequence.get('seq_id')!r} must include sessions[]")
        for session in sorted(sessions, key=lambda item: int(item.get("session_index") or 0)):
            task = dict(session)
            task.setdefault("seq_id", sequence.get("seq_id") or sequence.get("sequence_id"))
            task.setdefault("sequence_id", task.get("seq_id"))
            task.setdefault("seq_type", sequence.get("seq_type") or sequence.get("sequence_type"))
            task.setdefault("repo", sequence.get("repo"))
            task.setdefault("initial_commit", sequence.get("initial_commit"))
            task.setdefault("oracle_id", f"{task.get('seq_id')}-s{int(task.get('session_index') or 0)}")
            if task.get("oracle_cmd") is None:
                command = _sequence_oracle_command(sequence, str(task.get("oracle_id") or ""))
                if command:
                    task["oracle_cmd"] = command
            task["_sequence_record"] = sequence
            items.append(task)
    return items


def _sequence_oracle_command(sequence: Mapping[str, Any], oracle_id: str) -> Optional[str]:
    oracles = sequence.get("oracles") or sequence.get("oracle_cmds") or {}
    if not isinstance(oracles, Mapping) or oracle_id not in oracles:
        return None
    spec = oracles[oracle_id]
    if isinstance(spec, Mapping):
        command = spec.get("oracle_cmd") or spec.get("cmd") or spec.get("command")
    else:
        command = spec
    return str(command) if command else None


def _is_sequence_session_task(task: Mapping[str, Any]) -> bool:
    return isinstance(task.get("_sequence_record"), Mapping)


def _public_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    task_id = f"{task['seq_id']}-s{int(task['session_index']):02d}"
    public = {
        "id": task_id,
        "repo": task.get("repo"),
        "sequence_id": task.get("seq_id"),
        "seq_id": task.get("seq_id"),
        "seq_type": task.get("seq_type"),
        "session_index": int(task.get("session_index") or 0),
        "instruction": task.get("instruction", ""),
        "prompt": task.get("instruction", ""),
        "files": list(task.get("visible_files") or task.get("file_scope") or []),
    }
    if task.get("base_ref") is not None:
        public["base_ref"] = task.get("base_ref")
    return public


def _baseline_read_task(public_task: Mapping[str, Any], source_task: Mapping[str, Any]) -> Dict[str, Any]:
    task = dict(public_task)
    target = _read_budget_event_target_for_task(source_task)
    if target is not None:
        task["read_budget_event_target"] = target
    return task


def _read_budget_event_target_for_task(task: Mapping[str, Any]) -> Optional[Any]:
    for candidate in _metadata_candidates(task):
        if isinstance(candidate, Mapping) and candidate.get("read_budget_event_target") is not None:
            return candidate.get("read_budget_event_target")
    sequence = task.get("_sequence_record")
    if isinstance(sequence, Mapping):
        for candidate in _metadata_candidates(sequence):
            if isinstance(candidate, Mapping) and candidate.get("read_budget_event_target") is not None:
                return candidate.get("read_budget_event_target")
    return None


def _metadata_candidates(record: Mapping[str, Any]) -> List[Any]:
    return [
        record.get("validation_metadata"),
        record.get("authoring_metadata"),
    ]


def _normalize_event(event: Any) -> Dict[str, Any]:
    if not isinstance(event, Mapping):
        return {}
    kind = str(event.get("kind") or event.get("event_type") or "")
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        content = " ".join(str(value) for value in payload.values())
    else:
        content = str(payload or event.get("content") or "")
    return {
        "event_id": event.get("event_id") or event.get("id"),
        "after_session": event.get("after_session"),
        "event_type": kind,
        "kind": kind,
        "content": content,
        "payload": dict(payload) if isinstance(payload, Mapping) else payload,
        "scope": dict(event.get("scope") or {}) if isinstance(event.get("scope"), Mapping) else event.get("scope"),
        "labels": list(event.get("labels") or []),
        "memory_type": _event_memory_type(kind),
        "confidence": 0.9 if kind in {"human_feedback", "staleness", "contradiction"} else 0.6,
    }


def _event_memory_type(kind: str) -> str:
    if kind == "human_feedback":
        return "human_feedback"
    if kind == "contradiction":
        return "contradiction"
    if kind == "staleness":
        return "semantic_project"
    return "procedural"


def _initialize_sequence_state(
    *,
    condition: str,
    sequence: Mapping[str, Any],
    root: Path,
    allow_synthetic: bool = False,
) -> SequenceState:
    seq_id = str(sequence.get("seq_id") or sequence.get("sequence_id") or "")
    repo_id = str(sequence.get("repo") or "")
    initial_commit = str(sequence.get("initial_commit") or "")
    if not seq_id or not repo_id or not initial_commit:
        raise ValueError("sequence records require seq_id, repo, and initial_commit")
    state_repo = root / f".state-{_safe_name(condition)}-{_safe_name(seq_id)}"
    if allow_synthetic:
        state_repo.mkdir(parents=True)
        _git_checked(state_repo, "init", "--quiet")
    else:
        load_env.materialize(repo_id, initial_commit, dest=state_repo)
    _git_checked(state_repo, "config", "user.email", "dreambench@example.invalid")
    _git_checked(state_repo, "config", "user.name", "DreamBench Harness")
    _git_checked(state_repo, "commit", "--allow-empty", "-m", f"DreamBench continuation start: {condition} {seq_id}")
    return SequenceState(
        condition=condition,
        seq_id=seq_id,
        repo_id=repo_id,
        state_repo=state_repo,
        current_commit=_rev_parse(state_repo, "HEAD"),
    )


def _advance_sequence_state(
    state: SequenceState,
    *,
    production_diff: str,
    oracle_result: Mapping[str, Any],
    task_id: str,
) -> bool:
    state.session_index += 1
    eligible = (
        bool(production_diff.strip())
        and not bool(oracle_result.get("policy_rejected"))
        and not bool(oracle_result.get("empty_production_diff"))
        and bool((oracle_result.get("apply") or {}).get("passed"))
    )
    if not eligible:
        state.last_end_commit = state.current_commit
        return False

    _git_checked(state.state_repo, "checkout", "--quiet", "--detach", state.current_commit)
    patch_result = load_env.apply_patch(state.state_repo, production_diff)
    if not patch_result["passed"]:
        state.last_end_commit = state.current_commit
        return False
    _git_checked(state.state_repo, "add", "-A")
    staged = _git(state.state_repo, "diff", "--cached", "--quiet")
    if staged.returncode == 0:
        state.last_end_commit = state.current_commit
        return False
    _git_checked(state.state_repo, "commit", "-m", f"DreamBench continuation: {task_id}")
    state.current_commit = _rev_parse(state.state_repo, "HEAD")
    state.last_end_commit = state.current_commit
    return True


def _advance_sequence_state_dry_run(state: SequenceState, *, task_id: str) -> bool:
    state.session_index += 1
    _git_checked(state.state_repo, "checkout", "--quiet", "--detach", state.current_commit)
    _git_checked(state.state_repo, "commit", "--allow-empty", "-m", f"DreamBench dry-run continuation: {task_id}")
    state.current_commit = _rev_parse(state.state_repo, "HEAD")
    state.last_end_commit = state.current_commit
    return True


def _events_for_session(task: Mapping[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    legacy_event = task.get("injected_memory_event")
    if isinstance(legacy_event, Mapping):
        events.append(_normalize_event(legacy_event))

    sequence = task.get("_sequence_record")
    if isinstance(sequence, Mapping):
        session_index = int(task.get("session_index") or 0)
        inject_ids = _next_session_inject_ids(sequence, session_index)
        for event in sequence.get("events") or []:
            if not isinstance(event, Mapping):
                continue
            event_id = str(event.get("event_id") or event.get("id") or "")
            after_session = event.get("after_session")
            try:
                after_session_index = int(after_session) if after_session is not None else None
            except (TypeError, ValueError):
                after_session_index = None
            if after_session_index == session_index:
                events.append(_normalize_event(event))
            elif after_session is None and event_id and event_id in inject_ids:
                events.append(_normalize_event(event))
    return [event for event in events if event]


def _next_session_inject_ids(sequence: Mapping[str, Any], session_index: int) -> set[str]:
    ids: set[str] = set()
    for session in sequence.get("sessions") or []:
        if not isinstance(session, Mapping):
            continue
        try:
            candidate_index = int(session.get("session_index") or 0)
        except (TypeError, ValueError):
            continue
        if candidate_index == session_index + 1:
            ids.update(str(value) for value in session.get("inject_after") or [])
    return ids


def _git(cwd: Path, *args: str, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "diff.external=", *args],
        cwd=str(cwd),
        input=input_text,
        text=True,
        capture_output=True,
    )


def _git_checked(cwd: Path, *args: str, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    proc = _git(cwd, *args, input_text=input_text)
    if proc.returncode != 0:
        command = " ".join(args)
        raise RuntimeError(f"git {command} failed in {cwd}: {proc.stderr}")
    return proc


def _rev_parse(cwd: Path, ref: str) -> str:
    return _git_checked(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _trajectory_for_record(
    *,
    task: Mapping[str, Any],
    model: str,
    condition: str,
    seed: int,
    agent_result: TaskResult,
    agent_metadata: Mapping[str, Any],
    memory_context: Sequence[Mapping[str, Any]],
    oracle_result: Mapping[str, Any],
    final_passed: bool,
    error_type: Optional[str],
    error_detail: Optional[str],
    injected_events: Sequence[Mapping[str, Any]],
) -> Trajectory:
    memory_ids = [str(item.get("id")) for item in memory_context if item.get("id")]
    steps = [
        Step(
            action=str(step.get("action", "agent-step")),
            observation=str(step.get("outcome") or step.get("observation") or ""),
            metadata=dict(step),
        )
        for step in _agent_steps(agent_metadata)
    ]
    if not steps:
        steps = [Step(action="agent-run", observation="agent returned result")]
    outcome = "success" if final_passed else "failure"
    raw_episode = {
        "trajectory_id": f"{task['id']}-{condition}-{model}",
        "task_id": task["sequence_id"],
        "benchmark_task_id": task["id"],
        "session_id": task["id"],
        "model_id": model,
        "condition_id": condition,
        "repo_scope": task["sequence_id"],
        "sequence_id": task["sequence_id"],
        "seq_type": task.get("seq_type"),
        "repo_commit": task.get("base_ref"),
        "file_paths": list(task.get("files") or []),
        "prompt": task.get("prompt") or task.get("instruction"),
        "actions": [step.action for step in steps],
        "agent_patch": agent_result.patch,
        "production_diff": str(oracle_result.get("production_diff") or ""),
        "observations": _observations(agent_metadata, oracle_result),
        "failure_observations": [] if final_passed else [str(error_detail or oracle_result.get("stderr") or oracle_result.get("stdout") or error_type)],
        "memory_reads": memory_ids,
        "memory_context": list(memory_context),
        "outcome": outcome,
        "terminal_outcome": outcome,
        "successful_recovery": "oracle passed" if final_passed else None,
        "error_type": error_type,
        "error_detail": error_detail,
    }
    normalized_events = [dict(event) for event in injected_events if event]
    if normalized_events:
        raw_episode["injected_memory_events"] = normalized_events
        raw_episode["injected_memory_event"] = normalized_events[0]
    return Trajectory(
        task_id=task["id"],
        session_id=task["id"],
        model_id=model,
        condition_id=condition,
        seed=seed,
        budget={"wake_max_steps": getattr(agent_result, "steps", None)},
        repo_commit=str(task.get("base_ref") or ""),
        steps=steps,
        file_diffs=list(task.get("files") or []),
        memory_reads=memory_ids,
        final_outcome=outcome,
        raw_episode=raw_episode,
    )


def _compute_metrics(
    *,
    records: Sequence[Mapping[str, Any]],
    memory_items: Sequence[Any],
    wake_tokens: int,
    sleep_tokens: int,
    judge_tokens: int,
    wake_latency: float,
    sleep_latency: float,
    judge_latency: float,
    total_cost: float,
    repeated_error_events: int,
    repeated_error_opportunities: int,
    stale_activation_count: int,
    stale_available_count: int,
    harmful_memory_count: int,
    useful_memory_count: int,
    retrieved_count: int,
    scope_correct_count: int,
    admitted_memory_tokens_total: int,
) -> Dict[str, Optional[float]]:
    successes = [1.0 if record["final_passed"] else 0.0 for record in records]
    pass_at_1 = [1.0 if record.get("pass_at_1") else 0.0 for record in records]
    active_memories = [item for item in memory_items if getattr(item, "status", None) == MemoryStatus.ACTIVE]
    grounded_active = [
        item
        for item in active_memories
        if getattr(item, "provenance", None) is not None and item.provenance.is_grounded()
    ]
    contradiction_items = [item for item in memory_items if getattr(item, "type", None) == MemoryType.CONTRADICTION]
    correct_contradictions = [
        item
        for item in contradiction_items
        if getattr(item, "contradicts", None) and getattr(item, "provenance", None) and item.provenance.is_grounded()
    ]
    total_memory_tokens = sum(_token_count(getattr(item, "content", "")) for item in active_memories)
    useful_memory_tokens = sum(
        _token_count(getattr(item, "content", ""))
        for item in active_memories
        if float(getattr(item, "utility_score", 0.0) or 0.0) >= 0.6
    )
    raw = {
        "TaskSuccess": evaluation.TaskSuccess(successes),
        "Pass@1": evaluation.PassAt1(pass_at_1),
        "RepeatedErrorRate": evaluation.RepeatedErrorRate(repeated_error_events, repeated_error_opportunities),
        "StaleMemoryActivationRate": evaluation.StaleMemoryActivationRate(stale_activation_count, stale_available_count),
        "HarmfulMemoryRate": evaluation.HarmfulMemoryRate(harmful_memory_count, retrieved_count),
        "UsefulMemoryPrecision": evaluation.UsefulMemoryPrecision(useful_memory_count, retrieved_count),
        "ProvenanceCompleteness": evaluation.ProvenanceCompleteness(len(grounded_active), len(active_memories)),
        "ScopeAccuracy": evaluation.ScopeAccuracy(scope_correct_count, retrieved_count),
        "ContradictionRepairAccuracy": evaluation.ContradictionRepairAccuracy(len(correct_contradictions), len(contradiction_items)),
        "TransferScore": evaluation.TransferScore([]),
        "RegressionAfterUpdate": evaluation.RegressionAfterUpdate(0, 0),
        "MemoryBloat": evaluation.MemoryBloat(total_memory_tokens, useful_memory_tokens),
        "TotalTokens": evaluation.TotalTokens(wake_tokens, sleep_tokens, judge_tokens),
        "TotalLatency": evaluation.TotalLatency(wake_latency, sleep_latency, judge_latency),
        "CostPerSuccessfulTask": evaluation.CostPerSuccessfulTask(total_cost, sum(successes)),
        "SleepCostShare": evaluation.SleepCostShare(_estimate_sleep_cost(sleep_tokens), total_cost),
        "AdmittedMemoryTokensPerTask": (
            float(admitted_memory_tokens_total) / float(len(records)) if records else math.nan
        ),
    }
    return {key: _clean_float(value) for key, value in raw.items()}


def _write_outputs(payload: Mapping[str, Any], run_dir: Path) -> None:
    public_payload = {
        "manifest": payload["manifest"],
        "run_type": payload.get("run_type"),
        "selftest": payload.get("selftest", False),
        "metrics": payload["metrics"],
        "counts": payload["counts"],
        "memory_snapshot": payload["memory_snapshot"],
        "records": payload["records"],
    }
    if payload.get("slice_report") is not None:
        public_payload["slice_report"] = payload["slice_report"]
    if payload.get("judge") is not None:
        public_payload["judge"] = payload["judge"]
    (run_dir / "results.json").write_text(
        json.dumps(_jsonable(public_payload), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(_summary(public_payload), encoding="utf-8")


def _build_gate_report(
    *,
    parent_run_id: str,
    run_type: str,
    dry_run: bool,
    sequence_records_path: Path,
    sequences: Sequence[Mapping[str, Any]],
    condition_payloads: Mapping[str, Mapping[str, Any]],
    condition_dirs: Mapping[str, Path],
    model: str,
    judge_model: str,
    seed: int,
) -> Dict[str, Any]:
    condition_reports: Dict[str, Any] = {}
    total_sleep_error_count = 0
    total_contaminated_count = 0
    for condition, payload in condition_payloads.items():
        records = list(payload.get("records") or [])
        sleep_error_count = sum(1 for record in records if bool(record.get("sleep_error")))
        contaminated_count = sum(1 for record in records if bool(record.get("contaminated")))
        total_sleep_error_count += sleep_error_count
        total_contaminated_count += contaminated_count
        condition_reports[condition] = {
            "result_path": str((condition_dirs[condition] / "results.json").resolve()),
            "summary_path": str((condition_dirs[condition] / "summary.md").resolve()),
            "task_count": len(records),
            "sleep_error_count": sleep_error_count,
            "contaminated_count": contaminated_count,
            "s3_task_success": _session_success_metric(records, {3}),
            "s1_s2_task_success": _session_success_metric(records, {1, 2}),
            "metrics": dict(payload.get("metrics") or {}),
            "judge": payload.get("judge") or _judge_usage_snapshot(None),
        }

    hygiene_metrics = _aggregate_hygiene_metrics(condition_payloads.values())
    b0_report = condition_reports.get("B0", {})
    b0_s3 = b0_report.get("s3_task_success") or _metric_payload_for_report(0, 0)
    b0_s1_s2 = b0_report.get("s1_s2_task_success") or _metric_payload_for_report(0, 0)
    continuation_audit = _continuation_audit_report(condition_payloads)
    hygiene_ok = all(
        hygiene_metrics[name]["denominator"] > 0 and hygiene_metrics[name]["value"] is not None
        for name in HEADLINE_SLICE_METRICS
    )
    b0_s3_value = b0_s3.get("value")
    b0_s1_s2_value = b0_s1_s2.get("value")
    gates = {
        "b0_s3_task_success_le_0_50": bool(b0_s3_value is not None and b0_s3_value <= 0.50),
        "b0_s3_task_success_le_0_40_preferred": bool(b0_s3_value is not None and b0_s3_value <= 0.40),
        "b0_s1_s2_task_success_ge_0_80": bool(b0_s1_s2_value is not None and b0_s1_s2_value >= 0.80),
        "hygiene_metrics_non_null": bool(hygiene_ok),
        "continuation_audit_valid": bool(continuation_audit["valid"]),
        "no_sleep_errors": bool(total_sleep_error_count == 0),
    }
    return {
        "run_id": parent_run_id,
        "run_type": run_type,
        "dry_run": bool(dry_run),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sequence_records_path": str(sequence_records_path),
        "sequence_count": len(sequences),
        "conditions_requested": list(condition_payloads),
        "model": model,
        "judge_model": "stub-judge" if dry_run else judge_model,
        "seed": seed,
        "sleep_error_count": total_sleep_error_count,
        "contaminated_count": total_contaminated_count,
        "hygiene_metrics": hygiene_metrics,
        "b0_s3_task_success": b0_s3,
        "b0_s1_s2_task_success": b0_s1_s2,
        "acceptance_gates": gates,
        "continuation_audit": continuation_audit,
        "conditions": condition_reports,
    }


def _write_gate_report(report: Mapping[str, Any], run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "gate_report.json").write_text(
        json.dumps(_jsonable(report), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    (run_dir / "GATE_REPORT.md").write_text(_gate_report_markdown(report), encoding="utf-8")
    aggregate = {
        "manifest": {
            "run_id": report["run_id"],
            "run_type": report["run_type"],
            "dry_run": report["dry_run"],
            "sequence_count": report["sequence_count"],
            "conditions": report["conditions_requested"],
            "model": report["model"],
            "judge_model": report["judge_model"],
            "seed": report["seed"],
        },
        "gate_report": report,
    }
    (run_dir / "results.json").write_text(
        json.dumps(_jsonable(aggregate), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )


def _aggregate_hygiene_metrics(payloads: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    totals = {name: {"numerator": 0, "denominator": 0} for name in HEADLINE_SLICE_METRICS}
    for payload in payloads:
        slice_report = payload.get("slice_report")
        if not isinstance(slice_report, Mapping):
            continue
        metrics = ((slice_report.get("aggregate") or {}).get("metrics") or {})
        if not isinstance(metrics, Mapping):
            continue
        for name in HEADLINE_SLICE_METRICS:
            metric = metrics.get(name) or {}
            if not isinstance(metric, Mapping):
                continue
            totals[name]["numerator"] += int(metric.get("numerator") or 0)
            totals[name]["denominator"] += int(metric.get("denominator") or 0)
    return {
        name: _metric_payload_for_report(values["numerator"], values["denominator"], name=name)
        for name, values in totals.items()
    }


def _session_success_metric(records: Sequence[Mapping[str, Any]], sessions: set[int]) -> Dict[str, Any]:
    selected = [
        record for record in records
        if int((record.get("task") or {}).get("session_index") or record.get("session_index") or 0) in sessions
    ]
    numerator = sum(1 for record in selected if bool(record.get("final_passed")))
    return _metric_payload_for_report(numerator, len(selected), name="TaskSuccess")


def _metric_payload_for_report(numerator: int, denominator: int, *, name: str = "metric") -> Dict[str, Any]:
    value = None if denominator == 0 else float(numerator) / float(denominator)
    return {
        "name": name,
        "numerator": int(numerator),
        "denominator": int(denominator),
        "value": value,
    }


def _continuation_audit_report(condition_payloads: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    audited: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    for condition, payload in condition_payloads.items():
        for record in payload.get("records") or []:
            task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
            session_index = int(task.get("session_index") or record.get("session_index") or 0)
            if session_index <= 1:
                continue
            item = {
                "condition": condition,
                "sequence_id": task.get("seq_id") or task.get("sequence_id") or record.get("sequence_id"),
                "task_id": task.get("id"),
                "session_index": session_index,
                "start_commit": record.get("start_commit"),
                "end_commit": record.get("end_commit"),
                "previous_session_end_commit": record.get("previous_session_end_commit"),
                "started_from_previous_session": record.get("started_from_previous_session"),
                "forbidden_fresh_base_ref_used": record.get("forbidden_fresh_base_ref_used"),
                "oracle_id": record.get("oracle_id"),
                "continuation_advanced": record.get("continuation_advanced"),
            }
            audited.append(item)
            if not _continuation_audit_item_valid(item):
                invalid.append(item)
    return {
        "valid": bool(audited) and not invalid,
        "checked_records": len(audited),
        "invalid_records": invalid,
        "records": audited,
    }


def _continuation_audit_item_valid(item: Mapping[str, Any]) -> bool:
    return bool(
        item.get("start_commit")
        and item.get("end_commit")
        and item.get("previous_session_end_commit")
        and item.get("previous_session_end_commit") == item.get("start_commit")
        and item.get("started_from_previous_session") is True
        and item.get("forbidden_fresh_base_ref_used") is False
        and item.get("oracle_id")
        and "continuation_advanced" in item
    )


def _gate_report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Memory-Trap Gate Report {report['run_id']}",
        "",
        f"- run_type: {report['run_type']}",
        f"- dry_run: {report['dry_run']}",
        f"- sequences: {report['sequence_count']}",
        f"- conditions: {', '.join(report['conditions_requested'])}",
        f"- model: {report['model']}",
        f"- judge_model: {report['judge_model']}",
        "",
        "## Acceptance Gates",
        "",
    ]
    for key, value in sorted((report.get("acceptance_gates") or {}).items()):
        lines.append(f"- {key}: {bool(value)}")
    lines.extend(["", "## B0 Difficulty", ""])
    for key in ("b0_s3_task_success", "b0_s1_s2_task_success"):
        metric = report.get(key) or {}
        rendered = "NA" if metric.get("value") is None else f"{metric['value']:.6f}"
        lines.append(
            f"- {key}: {rendered} "
            f"({metric.get('numerator', 0)}/{metric.get('denominator', 0)})"
        )
    lines.extend(["", "## Sleep Errors", ""])
    lines.append(f"- total_sleep_error_count: {int(report.get('sleep_error_count') or 0)}")
    for condition, payload in sorted((report.get("conditions") or {}).items()):
        if not isinstance(payload, Mapping):
            continue
        lines.append(f"- {condition}: {int(payload.get('sleep_error_count') or 0)}")
    lines.extend(["", "## Contamination Scan", ""])
    lines.append(f"- total_contaminated_count: {int(report.get('contaminated_count') or 0)}")
    for condition, payload in sorted((report.get("conditions") or {}).items()):
        if not isinstance(payload, Mapping):
            continue
        lines.append(f"- {condition}: {int(payload.get('contaminated_count') or 0)}")
    lines.extend(["", "## Hygiene Metrics", ""])
    for name in HEADLINE_SLICE_METRICS:
        metric = (report.get("hygiene_metrics") or {}).get(name) or {}
        rendered = "NA" if metric.get("value") is None else f"{metric['value']:.6f}"
        lines.append(
            f"- {name}: {rendered} "
            f"({metric.get('numerator', 0)}/{metric.get('denominator', 0)})"
        )
    lines.extend(["", "## Continuation Audit", ""])
    audit = report.get("continuation_audit") or {}
    lines.append(f"- valid: {bool(audit.get('valid'))}")
    lines.append(f"- checked_records: {audit.get('checked_records', 0)}")
    lines.append(f"- invalid_records: {len(audit.get('invalid_records') or [])}")
    return "\n".join(lines) + "\n"


def _summary(payload: Mapping[str, Any]) -> str:
    manifest = payload["manifest"]
    metrics = payload["metrics"]
    lines = [
        f"# DreamBench-SWE Run {manifest['run_id']}",
        "",
        f"- condition: {manifest['condition']}",
        f"- model: {manifest['model']}",
        f"- tasks: {manifest['task_count']}",
        f"- seed: {manifest['seed']}",
        f"- tokens: {manifest['tokens']['total']}",
        f"- estimated cost: {manifest['estimated_cost']:.6f}",
        "",
        "## Metrics",
        "",
    ]
    for key in sorted(metrics):
        value = metrics[key]
        rendered = "NA" if value is None else f"{value:.6f}"
        lines.append(f"- {key}: {rendered}")
    lines.extend(["", "## Tasks", ""])
    for record in payload["records"]:
        status = "pass" if record["final_passed"] else "fail"
        lines.append(f"- {record['task']['id']}: {status}")
    return "\n".join(lines) + "\n"


def _stub_oracle(task: Mapping[str, Any], result: TaskResult, *, condition: Optional[str] = None) -> Dict[str, Any]:
    expected_pass = task.get("expected_pass")
    if expected_pass is None:
        expected_pass = not (
            _is_sequence_session_task(task)
            and str(condition or "").upper() == "B0"
            and int(task.get("session_index") or 0) == 3
        )
    passed = bool(result.patch.strip()) and expected_pass is True
    return {
        "passed": passed,
        "stdout": "stub oracle accepted non-empty offline patch\n" if passed else "",
        "stderr": "" if passed else "stub oracle rejected empty patch\n",
        "exit_code": 0 if passed else 1,
        "error_type": None if passed else "stub_oracle_failed",
    }


def _record_error_type(
    agent_result: TaskResult,
    oracle_result: Mapping[str, Any],
    final_passed: bool,
    task: Mapping[str, Any],
) -> Optional[str]:
    if agent_result.error_type:
        return str(agent_result.error_type)
    if final_passed:
        return None
    return str(oracle_result.get("error_type") or task.get("error_taxonomy_hint") or "oracle_failed")


def _record_error_detail(
    *,
    error_type: Optional[str],
    agent_result: TaskResult,
    agent_metadata: Mapping[str, Any],
    oracle_result: Mapping[str, Any],
) -> Optional[str]:
    if not error_type:
        return None
    candidates = [
        getattr(agent_result, "error_detail", None),
        agent_metadata.get("failure_reason") if isinstance(agent_metadata, Mapping) else None,
        oracle_result.get("error_detail"),
        oracle_result.get("stderr"),
        oracle_result.get("stdout"),
    ]
    for value in candidates:
        detail = _truncate_error_detail(value)
        if detail:
            return detail
    return _truncate_error_detail(error_type)


def _error_detail(exc: BaseException, *, limit: int = 500) -> str:
    return _truncate_error_detail(f"{type(exc).__name__}: {exc}", limit=limit) or type(exc).__name__


def _truncate_error_detail(value: Any, *, limit: int = 500) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _update_repeated_errors(
    seen_errors_by_sequence: Dict[str, set[str]],
    public_task: Mapping[str, Any],
    error_type: Optional[str],
    repeated_error_events: int,
    repeated_error_opportunities: int,
) -> Tuple[int, int]:
    if not error_type:
        return repeated_error_events, repeated_error_opportunities
    sequence_errors = seen_errors_by_sequence.setdefault(str(public_task.get("sequence_id") or "unknown"), set())
    if sequence_errors:
        repeated_error_opportunities += 1
    if error_type in sequence_errors:
        repeated_error_events += 1
    sequence_errors.add(str(error_type))
    return repeated_error_events, repeated_error_opportunities


def _agent_usage_tokens(agent_result: TaskResult, agent_metadata: Mapping[str, Any]) -> Tuple[int, int]:
    usage = agent_metadata.get("usage") if isinstance(agent_metadata, Mapping) else {}
    usage_map = usage if isinstance(usage, Mapping) else {}
    prompt_tokens = int(usage_map.get("prompt_tokens") or 0)
    completion_tokens = int(usage_map.get("completion_tokens") or 0)
    total_tokens = int(usage_map.get("total_tokens") or 0)
    if prompt_tokens or completion_tokens:
        return prompt_tokens, completion_tokens
    if total_tokens:
        return total_tokens, 0
    return int(agent_result.tokens), 0


def _sanitize_for_agent(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_for_agent(item)
            for key, item in value.items()
            if str(key) not in SCORER_ONLY_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_for_agent(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_agent(item) for item in value]
    if isinstance(value, set):
        return sorted(_sanitize_for_agent(item) for item in value)
    return value


def _score_public(result: Mapping[str, Any], *, limit: int = 12000) -> Dict[str, Any]:
    return {
        "passed": bool(result.get("passed")),
        "error_type": result.get("error_type"),
        "error_detail": _truncate_error_detail(result.get("error_detail") or result.get("stderr") or result.get("stdout")),
        "empty_production_diff": bool(result.get("empty_production_diff")),
        "policy_rejected": bool(result.get("policy_rejected")),
        "production_files": list(result.get("production_files") or []),
        "rejected_files": list(result.get("rejected_files") or []),
        "rejected_reasons": dict(result.get("rejected_reasons") or {}),
        "agent_diff_files": list(result.get("agent_diff_files") or []),
        "apply": result.get("apply"),
        "production_diff": str(result.get("production_diff") or "")[:limit],
    }


def _agent_steps(agent_metadata: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    steps = agent_metadata.get("steps")
    if isinstance(steps, list):
        return [dict(step) if isinstance(step, Mapping) else {"action": str(step)} for step in steps]
    return []


def _observations(agent_metadata: Mapping[str, Any], oracle_result: Mapping[str, Any]) -> List[str]:
    observations: List[str] = []
    if agent_metadata.get("stdout"):
        observations.append(str(agent_metadata["stdout"])[:4000])
    if agent_metadata.get("stderr"):
        observations.append(str(agent_metadata["stderr"])[:4000])
    observations.append(f"oracle exit={oracle_result.get('exit_code')} passed={oracle_result.get('passed')}")
    if oracle_result.get("stdout"):
        observations.append(str(oracle_result["stdout"])[:4000])
    if oracle_result.get("stderr"):
        observations.append(str(oracle_result["stderr"])[:4000])
    return observations


def _human_feedback(event: Any) -> Optional[str]:
    if not isinstance(event, Mapping):
        return None
    if event.get("kind") == "human_feedback" or event.get("event_type") == "human_feedback":
        return str(event.get("content") or event.get("payload") or "")
    return None


def _patch_files(patch: str) -> List[str]:
    files: List[str] = []
    for match in re.finditer(r"^diff --git a/(.*?) b/(.*?)$", patch, flags=re.MULTILINE):
        files.append(match.group(2))
    return _dedupe(files)


def _scope_matches(item: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    repo_scope = item.get("repo_scope")
    if repo_scope and repo_scope != task.get("sequence_id"):
        return False
    file_scope = set(str(value) for value in item.get("file_scope") or [])
    task_files = set(str(value) for value in task.get("files") or [])
    if file_scope and task_files and not (file_scope & task_files):
        return False
    return True


def _is_stale_memory(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status", "active")).lower()
    return status in {"stale", "superseded", "deleted", "requires_review"} or float(item.get("staleness_score") or 0.0) >= 0.95


def _is_harmful_memory(item: Mapping[str, Any]) -> bool:
    return _is_stale_memory(item) or float(item.get("risk_score") or 0.0) >= 0.95


def _is_useful_memory(item: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    return _scope_matches(item, task) and not _is_harmful_memory(item)


def _has_stale_available(policy: MemoryPolicy) -> bool:
    return any(_memory_item_is_stale(item) for item in policy.memory_items())


def _memory_item_is_stale(item: Any) -> bool:
    return getattr(item, "status", None) in {
        MemoryStatus.STALE,
        MemoryStatus.SUPERSEDED,
        MemoryStatus.DELETED,
        MemoryStatus.REQUIRES_REVIEW,
    } or float(getattr(item, "staleness_score", 0.0) or 0.0) >= 0.95


def _task_result_dict(result: TaskResult) -> Dict[str, Any]:
    return {
        "patch": result.patch,
        "passed": result.passed,
        "steps": result.steps,
        "tokens": result.tokens,
        "error_type": result.error_type,
        "error_detail": getattr(result, "error_detail", None),
    }


def _memory_item_dict(item: Any) -> Dict[str, Any]:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if isinstance(item, Mapping):
        return dict(item)
    return {"repr": repr(item)}


def _oracle_public(result: Mapping[str, Any], *, limit: int = 12000) -> Dict[str, Any]:
    return {
        "passed": bool(result.get("passed")),
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout") or "")[:limit],
        "stderr": str(result.get("stderr") or "")[:limit],
        "timed_out": bool(result.get("timed_out")),
        "error_type": result.get("error_type"),
        "error_detail": _truncate_error_detail(result.get("error_detail") or result.get("stderr") or result.get("stdout")),
    }


def _estimate_cost(model: str, input_tokens: int, output_tokens: int = 0) -> float:
    price = TOKEN_PRICE_PER_MILLION.get(model, {"input": 0.0, "output": 0.0})
    if isinstance(price, Mapping):
        input_price = float(price.get("input", 0.0))
        output_price = float(price.get("output", 0.0))
    else:
        input_price = float(price)
        output_price = 0.0
    return (float(input_tokens) * input_price + float(output_tokens) * output_price) / 1_000_000.0


def _estimate_sleep_cost(tokens: int) -> float:
    return _estimate_cost("stub", tokens, 0)


def _judge_usage_total(source: Optional[Any]) -> int:
    if source is None:
        return 0
    usage = getattr(source, "total_usage", {})
    if not isinstance(usage, Mapping):
        return 0
    return int(usage.get("total_tokens") or 0)


def _judge_usage_snapshot(source: Optional[Any]) -> Dict[str, Any]:
    if source is None:
        return {
            "model": None,
            "calls": 0,
            "calls_by_tag": {},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "live_llm_used": False,
            "last_isolation_mode": None,
            "isolation_mode_counts": {},
        }
    usage = getattr(source, "total_usage", {})
    calls_by_tag = getattr(source, "calls_by_tag", {})
    isolation_mode_counts = getattr(source, "isolation_mode_counts", {})
    return {
        "model": getattr(source, "model", None),
        "calls": int(getattr(source, "calls", 0) or 0),
        "calls_by_tag": dict(calls_by_tag) if isinstance(calls_by_tag, Mapping) else {},
        "usage": dict(usage) if isinstance(usage, Mapping) else {},
        "live_llm_used": not isinstance(source, StubJudgeClient),
        "last_isolation_mode": getattr(source, "last_isolation_mode", None),
        "isolation_mode_counts": dict(isolation_mode_counts) if isinstance(isolation_mode_counts, Mapping) else {},
    }


def _clean_float(value: float) -> Optional[float]:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    return value


def _run_id(condition: str, model: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_safe_name(condition)}-{_safe_name(model)}-{uuid.uuid4().hex[:8]}"


def _safe_name(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value)).strip("-") or "run"


def _token_count(text: Any) -> int:
    return len(re.findall(r"\S+", str(text or "")))


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
