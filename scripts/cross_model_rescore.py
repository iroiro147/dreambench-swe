#!/usr/bin/env python3
"""Cross-model hygiene re-score harness for DreamBench-SWE Phase C.

This script reuses the stored container result records.  It does not re-run
agents and it does not touch executable Pass@1; only LLM-label hygiene
questions routed through ``slice_scorer``'s ``label_judge`` hook are re-judged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dream_memory.slice_scorer import HEADLINE_SLICE_METRICS, rescore_from_records  # noqa: E402


JUDGE_SELECTORS = ("glm-latest", "kimi-latest", "claude-cc2")
DEFAULT_SEQUENCE_RECORDS = ROOT / "experiments" / "env" / "sequences.jsonl"
DEFAULT_OUT_JSON = ROOT / "analysis" / "fold" / "cross_model_rescore.json"
DEFAULT_OUT_MD = ROOT / "analysis" / "fold" / "cross_model_rescore.md"

METRIC_DIRECTIONS = {
    "StaleMemoryActivationRate": "lo",
    "ContradictionRepairAccuracy": "hi",
    "RepeatedErrorRate": "lo",
    "HumanFeedbackUseAccuracy": "hi",
    "HarmfulMemoryRate": "lo",
    "UsefulMemoryPrecision": "hi",
    "ScopeAccuracy": "hi",
    "TransferScore": "hi",
    "RegressionAfterUpdate": "lo",
}

FAMILIES = {
    "task-coupled": (
        "ContradictionRepairAccuracy",
        "HumanFeedbackUseAccuracy",
        "TransferScore",
        "ScopeAccuracy",
    ),
    "retrieval-avoidance": ("StaleMemoryActivationRate", "HarmfulMemoryRate"),
    "precision": ("UsefulMemoryPrecision",),
    "regression": ("RegressionAfterUpdate",),
    "repeated": ("RepeatedErrorRate",),
}

JUDGE_AFFECTED_BY_QUESTION = {
    "contradiction_repair": ("ContradictionRepairAccuracy",),
    "repeated_named_bad_action": ("RepeatedErrorRate",),
    "patch_and_memory_follow_feedback": (
        "HumanFeedbackUseAccuracy",
        "ScopeAccuracy",
        "TransferScore",
    ),
}


@dataclass
class ResultPayload:
    path: Path
    condition: str
    data: Mapping[str, Any]
    mtime: float


@dataclass
class ConditionInput:
    condition: str
    records: list[Mapping[str, Any]]
    payloads: list[ResultPayload]
    source_paths: list[str]
    memory_snapshot: Sequence[Mapping[str, Any]] | None = None
    non_container_record_count: int = 0


@dataclass
class JudgeCall:
    question: str
    sequence_id: str
    label_id: str
    condition: str
    status: str
    answer: bool | None
    attempts: int
    latency_seconds: float
    error: str | None = None


@dataclass
class LabelJudgeAdapter:
    """Callable adapter matching ``slice_scorer.LabelJudge``."""

    client: Any
    judge_selector: str
    max_retries: int = 2
    calls: list[JudgeCall] = field(default_factory=list)

    def __call__(self, judge_input: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = build_label_judge_prompt(judge_input)
        question = str(judge_input.get("question") or "")
        payload = as_mapping(judge_input.get("payload"))
        label = as_mapping(payload.get("label"))
        sequence_id = str(payload.get("sequence_id") or "")
        condition = str(payload.get("condition") or "")
        label_id = str(label.get("label_id") or "")
        attempts = max(1, int(self.max_retries) + 1)
        last_error = ""
        start = time.perf_counter()

        for attempt in range(1, attempts + 1):
            try:
                text = str(self.client(prompt))
                parsed = parse_json_object(text)
                answer = normalize_answer(parsed.get("answer"))
                if answer is None:
                    status = "abstain"
                    error = f"invalid_or_null_answer:{parsed.get('answer')!r}"
                    self.calls.append(
                        JudgeCall(
                            question=question,
                            sequence_id=sequence_id,
                            label_id=label_id,
                            condition=condition,
                            status=status,
                            answer=None,
                            attempts=attempt,
                            latency_seconds=time.perf_counter() - start,
                            error=error,
                        )
                    )
                    return {
                        "answer": None,
                        "status": status,
                        "abstain": True,
                        "error": error,
                        "attempts": attempt,
                        "judge_selector": self.judge_selector,
                        "parsed": parsed,
                    }
                self.calls.append(
                    JudgeCall(
                        question=question,
                        sequence_id=sequence_id,
                        label_id=label_id,
                        condition=condition,
                        status="ok",
                        answer=answer,
                        attempts=attempt,
                        latency_seconds=time.perf_counter() - start,
                    )
                )
                return {
                    "answer": answer,
                    "status": "ok",
                    "abstain": False,
                    "attempts": attempt,
                    "judge_selector": self.judge_selector,
                    "parsed": parsed,
                }
            except Exception as exc:  # noqa: BLE001 - retry boundary normalizes judge failures.
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < attempts:
                    time.sleep(min(2.0 ** (attempt - 1), 8.0))

        self.calls.append(
            JudgeCall(
                question=question,
                sequence_id=sequence_id,
                label_id=label_id,
                condition=condition,
                status="abstain",
                answer=None,
                attempts=attempts,
                latency_seconds=time.perf_counter() - start,
                error=last_error,
            )
        )
        return {
            "answer": None,
            "status": "abstain",
            "abstain": True,
            "error": last_error,
            "attempts": attempts,
            "judge_selector": self.judge_selector,
        }


class MockTextJudgeClient:
    """Deterministic prompt->text client for synthetic end-to-end tests."""

    def __init__(self, responses: Sequence[Any]) -> None:
        self.responses = list(responses)
        self.index = 0

    def __call__(self, prompt: str) -> str:  # noqa: ARG002 - prompt intentionally ignored by canned mock.
        if not self.responses:
            return json.dumps({"answer": None, "rationale": "mock response list was empty"})
        response = self.responses[self.index % len(self.responses)]
        self.index += 1
        if isinstance(response, (Mapping, list)):
            return json.dumps(response, sort_keys=True)
        return str(response)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results_root = resolve_path(args.results_root)
    sequence_records_path = resolve_path(args.sequence_records)
    out_json = resolve_path(args.out_json)
    out_md = resolve_path(args.out_md)
    conditions_filter = parse_conditions(args.conditions)

    sequence_records = load_jsonl(sequence_records_path)
    condition_inputs = discover_condition_inputs(
        results_root,
        conditions_filter=conditions_filter,
        require_container=args.require_container,
    )
    if not condition_inputs:
        raise SystemExit(f"no result records found under {results_root}")

    client = build_text_judge_client(args)
    label_judge = LabelJudgeAdapter(
        client=client,
        judge_selector=args.judge,
        max_retries=args.max_retries,
    )

    conditions: dict[str, dict[str, Any]] = {}
    baseline_reports: dict[str, Mapping[str, Any]] = {}
    cross_reports: dict[str, Mapping[str, Any]] = {}
    for condition in sorted(condition_inputs):
        condition_input = condition_inputs[condition]
        baseline_report, baseline_source = baseline_report_for_condition(
            condition_input,
            sequence_records=sequence_records,
        )
        cross_report = rescore_from_records(
            records={
                "records": condition_input.records,
                "condition": condition,
                "memory_snapshot": condition_input.memory_snapshot or [],
            },
            sequence_records=sequence_records,
            condition=condition,
            memory_snapshot=condition_input.memory_snapshot,
            label_judge=label_judge,
        )
        neutralize_abstentions(cross_report)

        baseline_reports[condition] = baseline_report
        cross_reports[condition] = cross_report
        conditions[condition] = condition_summary(
            condition_input,
            baseline_report=baseline_report,
            baseline_source=baseline_source,
            cross_report=cross_report,
        )

    agreement = agreement_summary(baseline_reports, cross_reports)
    survival = survival_summary(cross_reports)
    output = {
        "metadata": {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "results_root": display_path(results_root),
            "sequence_records": display_path(sequence_records_path),
            "judge_selector": args.judge,
            "mock_judge": bool(args.mock_judge_responses),
            "max_retries": int(args.max_retries),
            "timeout_seconds": float(args.timeout_seconds),
            "conditions": sorted(conditions),
            "executable_pass_at_1_untouched": True,
            "note": (
                "This harness re-scores only LLM-label hygiene questions via "
                "slice_scorer.label_judge over existing records; executable Pass@1 "
                "is copied from the original records/folds and is not recomputed here."
            ),
        },
        "conditions": conditions,
        "comparison": {
            "agreement_by_metric": agreement["by_metric"],
            "agreement_by_condition": agreement["by_condition"],
            "per_condition_metric_delta": {
                condition: conditions[condition]["metric_delta_vs_baseline"]
                for condition in sorted(conditions)
            },
        },
        "survival": survival,
        "judge_calls": judge_call_summary(label_judge.calls),
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(output) + "\n", encoding="utf-8")

    print(
        "CROSS_MODEL_RESCORE complete "
        f"judge={args.judge} conditions={len(conditions)} "
        f"label_calls={len(label_judge.calls)} abstentions={output['judge_calls']['abstentions']} "
        f"task_coupled_survives={str(survival['task_coupled_family_win_survives']).lower()} "
        f"independent_4_of_5_survives={str(survival['independent_family_4_of_5_survives']).lower()}"
    )
    print(f"Wrote {display_path(out_json)}")
    print(f"Wrote {display_path(out_md)}")
    print("Pass@1 untouched: true")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-score DreamBench-SWE hygiene labels with a non-GPT-5.5 judge over stored records.",
    )
    parser.add_argument("results_root", help="Result root, condition dir, or results.json to re-score.")
    parser.add_argument("--judge", choices=JUDGE_SELECTORS, required=True)
    parser.add_argument("--sequence-records", default=str(DEFAULT_SEQUENCE_RECORDS))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--conditions", default=None, help="Optional comma-separated condition filter.")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--secrets-path", default=str(ROOT / "experiments" / "secrets.env"))
    parser.add_argument(
        "--mock-judge-responses",
        default=None,
        help="JSON file with canned text/object responses; used for synthetic tests, not live runs.",
    )
    parser.add_argument(
        "--require-container",
        action="store_true",
        help="Drop records whose isolation_mode is present and not 'container'.",
    )
    return parser.parse_args(argv)


def resolve_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def parse_conditions(value: str | None) -> set[str] | None:
    if value is None or not value.strip():
        return None
    return {part.strip().upper() for part in value.split(",") if part.strip()}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: sequence record is not an object")
        records.append(value)
    return records


def discover_condition_inputs(
    results_root: Path,
    *,
    conditions_filter: set[str] | None,
    require_container: bool,
) -> dict[str, ConditionInput]:
    payloads_by_condition: dict[str, list[ResultPayload]] = defaultdict(list)
    for path in discover_result_paths(results_root):
        data = read_json_object(path)
        records = data.get("records")
        if not isinstance(records, list) or not records:
            continue
        condition = infer_condition(path, data, records)
        if not condition:
            continue
        condition = condition.upper()
        if conditions_filter is not None and condition not in conditions_filter:
            continue
        payloads_by_condition[condition].append(
            ResultPayload(path=path, condition=condition, data=data, mtime=path.stat().st_mtime)
        )

    out: dict[str, ConditionInput] = {}
    for condition, payloads in sorted(payloads_by_condition.items()):
        records, non_container_count = dedupe_records(payloads, require_container=require_container)
        if not records:
            continue
        snapshot = single_payload_snapshot(payloads)
        out[condition] = ConditionInput(
            condition=condition,
            records=records,
            payloads=payloads,
            source_paths=[display_path(payload.path) for payload in payloads],
            memory_snapshot=snapshot,
            non_container_record_count=non_container_count,
        )
    return out


def discover_result_paths(results_root: Path) -> list[Path]:
    if results_root.is_file():
        return [results_root]
    direct = results_root / "results.json"
    if direct.exists():
        return [direct]
    return sorted(results_root.rglob("results.json"))


def read_json_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def infer_condition(path: Path, data: Mapping[str, Any], records: Sequence[Any]) -> str:
    manifest = as_mapping(data.get("manifest"))
    for value in (data.get("condition"), manifest.get("condition")):
        if value:
            return str(value)
    for parent in (path.parent, path.parent.parent):
        name = parent.name
        if re.fullmatch(r"(?:B\d+|DF|A\d+|DF-[A-Za-z0-9_-]+)", name, flags=re.IGNORECASE):
            return name
    record_conditions = {
        str(as_mapping(record).get("condition") or as_mapping(as_mapping(record).get("task")).get("condition") or "")
        for record in records
        if isinstance(record, Mapping)
    }
    record_conditions = {value for value in record_conditions if value}
    if len(record_conditions) == 1:
        return next(iter(record_conditions))
    return ""


def dedupe_records(
    payloads: Sequence[ResultPayload],
    *,
    require_container: bool,
) -> tuple[list[Mapping[str, Any]], int]:
    by_key: dict[tuple[str, int], tuple[float, int, Mapping[str, Any]]] = {}
    non_container_count = 0
    for payload in sorted(payloads, key=lambda item: (item.mtime, str(item.path))):
        for ordinal, record in enumerate(payload.data.get("records") or []):
            if not isinstance(record, Mapping):
                continue
            isolation = record.get("isolation_mode")
            if isolation is not None and isolation != "container":
                non_container_count += 1
                if require_container:
                    continue
            key = record_key(record)
            if key is None:
                continue
            current = (payload.mtime, ordinal, record)
            if key not in by_key or (payload.mtime, ordinal) >= (by_key[key][0], by_key[key][1]):
                by_key[key] = current
    return [value[2] for _, value in sorted(by_key.items())], non_container_count


def record_key(record: Mapping[str, Any]) -> tuple[str, int] | None:
    task = as_mapping(record.get("task"))
    seq_id = str(task.get("seq_id") or task.get("sequence_id") or record.get("sequence_id") or "")
    try:
        session_index = int(task.get("session_index") or record.get("session_index") or 0)
    except (TypeError, ValueError):
        session_index = 0
    if not seq_id or session_index <= 0:
        return None
    return (seq_id, session_index)


def single_payload_snapshot(payloads: Sequence[ResultPayload]) -> Sequence[Mapping[str, Any]] | None:
    if len(payloads) != 1:
        return None
    snapshot = payloads[0].data.get("memory_snapshot")
    if isinstance(snapshot, list):
        return [item for item in snapshot if isinstance(item, Mapping)]
    return None


def baseline_report_for_condition(
    condition_input: ConditionInput,
    *,
    sequence_records: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], str]:
    if len(condition_input.payloads) == 1:
        embedded = condition_input.payloads[0].data.get("slice_report")
        if isinstance(embedded, Mapping) and embedded.get("aggregate"):
            return embedded, "embedded_slice_report"
    report = rescore_from_records(
        records={
            "records": condition_input.records,
            "condition": condition_input.condition,
            "memory_snapshot": condition_input.memory_snapshot or [],
        },
        sequence_records=sequence_records,
        condition=condition_input.condition,
        memory_snapshot=condition_input.memory_snapshot,
        label_judge=None,
    )
    return report, "deterministic_fallback_rescore"


def build_text_judge_client(args: argparse.Namespace) -> Any:
    if args.mock_judge_responses:
        responses_payload = json.loads(resolve_path(args.mock_judge_responses).read_text(encoding="utf-8"))
        if isinstance(responses_payload, Mapping):
            responses = responses_payload.get("responses") or []
        else:
            responses = responses_payload
        if not isinstance(responses, list):
            raise ValueError("--mock-judge-responses must contain a JSON list or {'responses': [...]}")
        return MockTextJudgeClient(responses)
    if args.judge in {"glm-latest", "kimi-latest"}:
        from experiments.grid_judge_client import GridJudgeClient

        return GridJudgeClient(
            model=args.judge,
            secrets_path=resolve_path(args.secrets_path),
            timeout_seconds=float(args.timeout_seconds),
        )
    if args.judge == "claude-cc2":
        from experiments.cc2_judge_client import Cc2JudgeClient

        return Cc2JudgeClient(timeout_seconds=float(args.timeout_seconds))
    raise ValueError(f"unsupported judge selector: {args.judge}")


def build_label_judge_prompt(judge_input: Mapping[str, Any]) -> str:
    payload = {
        "question": judge_input.get("question"),
        "fallback_answer": bool(judge_input.get("fallback_answer")),
        "payload": judge_input.get("payload") or {},
    }
    return (
        "DREAMBENCH_HYGIENE_LABEL_JUDGE\n"
        "You are re-scoring one DreamBench-SWE hygiene label over an existing "
        "container result record. Do not re-run code and do not judge executable "
        "Pass@1. Answer only the question in the JSON payload.\n\n"
        "Return only valid JSON with this schema:\n"
        '{"answer": true|false|null, "confidence": 0.0, "rationale": "short reason"}\n'
        "Use null only when the evidence is insufficient or malformed.\n\n"
        "INPUT_JSON:\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}"
    )


def parse_json_object(text: str) -> Mapping[str, Any]:
    candidate = strip_json_fence(text)
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        if start < 0:
            raise
        value, _ = decoder.raw_decode(candidate[start:])
    if not isinstance(value, Mapping):
        raise ValueError("judge response JSON was not an object")
    return value


def strip_json_fence(text: str) -> str:
    candidate = str(text or "").strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return candidate


def normalize_answer(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "pass", "passed", "correct"}:
            return True
        if normalized in {"false", "no", "n", "0", "fail", "failed", "incorrect"}:
            return False
        if normalized in {"null", "none", "na", "n/a", "unknown", "abstain", "insufficient"}:
            return None
    return None


def neutralize_abstentions(report: Mapping[str, Any]) -> None:
    """Set judge-dependent contributions to 0/0 for abstained judge calls."""

    if not isinstance(report, dict):
        return
    abstentions: list[dict[str, Any]] = []
    for detail in report.get("details") or []:
        if not isinstance(detail, dict):
            continue
        for label_result in detail.get("label_results") or []:
            if not isinstance(label_result, dict):
                continue
            abstained_questions = [
                str(note.get("question") or "")
                for note in label_result.get("judge_notes") or []
                if isinstance(note, Mapping) and judge_note_abstained(note)
            ]
            if not abstained_questions:
                continue
            for question in abstained_questions:
                affected_metrics = JUDGE_AFFECTED_BY_QUESTION.get(question, ())
                for metric_name in affected_metrics:
                    contributions = label_result.get("metric_contributions")
                    if isinstance(contributions, dict) and metric_name in contributions:
                        previous = contributions.get(metric_name)
                        contributions[metric_name] = {
                            "numerator": 0,
                            "denominator": 0,
                            "abstained": True,
                            "previous": previous,
                        }
                        abstentions.append(
                            {
                                "condition": detail.get("condition"),
                                "sequence_id": detail.get("sequence_id"),
                                "session_index": detail.get("session_index"),
                                "label_id": label_result.get("label_id"),
                                "question": question,
                                "metric": metric_name,
                            }
                        )
    recompute_aggregate_metrics(report)
    report["cross_model_abstentions"] = abstentions


def judge_note_abstained(note: Mapping[str, Any]) -> bool:
    raw = note.get("raw")
    if not isinstance(raw, Mapping):
        return False
    return bool(raw.get("abstain") or raw.get("status") == "abstain" or raw.get("answer") is None)


def recompute_aggregate_metrics(report: Mapping[str, Any]) -> None:
    if not isinstance(report, dict):
        return
    totals = {name: {"numerator": 0, "denominator": 0} for name in HEADLINE_SLICE_METRICS}
    for detail in report.get("details") or []:
        if not isinstance(detail, Mapping):
            continue
        for metric_name, contribution in as_mapping(detail.get("metric_contributions")).items():
            add_contribution(totals, metric_name, contribution)
        for label_result in detail.get("label_results") or []:
            if not isinstance(label_result, Mapping):
                continue
            for metric_name, contribution in as_mapping(label_result.get("metric_contributions")).items():
                add_contribution(totals, metric_name, contribution)
    aggregate = report.setdefault("aggregate", {})
    if not isinstance(aggregate, dict):
        return
    aggregate["metrics"] = {
        name: {
            "name": name,
            "numerator": totals[name]["numerator"],
            "denominator": totals[name]["denominator"],
            "value": safe_div(totals[name]["numerator"], totals[name]["denominator"]),
        }
        for name in HEADLINE_SLICE_METRICS
    }


def add_contribution(totals: dict[str, dict[str, int]], metric_name: Any, contribution: Any) -> None:
    name = str(metric_name)
    if name not in totals or not isinstance(contribution, Mapping):
        return
    totals[name]["numerator"] += int(contribution.get("numerator") or 0)
    totals[name]["denominator"] += int(contribution.get("denominator") or 0)


def condition_summary(
    condition_input: ConditionInput,
    *,
    baseline_report: Mapping[str, Any],
    baseline_source: str,
    cross_report: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_metrics = aggregate_metrics(baseline_report)
    cross_metrics = aggregate_metrics(cross_report)
    return {
        "condition": condition_input.condition,
        "record_count": len(condition_input.records),
        "s3_record_count": sum(1 for record in condition_input.records if record_session(record) == 3),
        "source_paths": condition_input.source_paths,
        "non_container_record_count": condition_input.non_container_record_count,
        "baseline_source": baseline_source,
        "baseline_judge": as_mapping(baseline_report.get("judge")),
        "cross_model_judge": as_mapping(cross_report.get("judge")),
        "baseline_metrics": baseline_metrics,
        "cross_model_metrics": cross_metrics,
        "metric_delta_vs_baseline": metric_deltas(baseline_metrics, cross_metrics),
        "abstentions": cross_report.get("cross_model_abstentions") or [],
    }


def aggregate_metrics(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    metrics = as_mapping(as_mapping(report.get("aggregate")).get("metrics"))
    out: dict[str, dict[str, Any]] = {}
    for name in HEADLINE_SLICE_METRICS:
        payload = as_mapping(metrics.get(name))
        numerator = int(payload.get("numerator") or 0)
        denominator = int(payload.get("denominator") or 0)
        out[name] = {
            "value": payload.get("value") if denominator > 0 else None,
            "numerator": numerator,
            "denominator": denominator,
        }
    return out


def metric_deltas(
    baseline_metrics: Mapping[str, Mapping[str, Any]],
    cross_metrics: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in HEADLINE_SLICE_METRICS:
        baseline_value = numeric_or_none(as_mapping(baseline_metrics.get(name)).get("value"))
        cross_value = numeric_or_none(as_mapping(cross_metrics.get(name)).get("value"))
        out[name] = None if baseline_value is None or cross_value is None else cross_value - baseline_value
    return out


def agreement_summary(
    baseline_reports: Mapping[str, Mapping[str, Any]],
    cross_reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    by_metric: dict[str, dict[str, int]] = {
        name: {"agree": 0, "total": 0, "missing": 0, "abstained": 0}
        for name in HEADLINE_SLICE_METRICS
    }
    by_condition: dict[str, dict[str, dict[str, Any]]] = {}

    for condition in sorted(cross_reports):
        baseline_map = contribution_map(baseline_reports.get(condition) or {})
        cross_map = contribution_map(cross_reports.get(condition) or {})
        condition_counts = {
            name: {"agree": 0, "total": 0, "missing": 0, "abstained": 0}
            for name in HEADLINE_SLICE_METRICS
        }
        for key in sorted(set(baseline_map) | set(cross_map)):
            metric = key[-1]
            if metric not in by_metric:
                continue
            baseline = baseline_map.get(key)
            cross = cross_map.get(key)
            if baseline is None or cross is None:
                by_metric[metric]["missing"] += 1
                condition_counts[metric]["missing"] += 1
                continue
            if cross.get("abstained") or int(cross.get("denominator") or 0) <= 0:
                by_metric[metric]["abstained"] += 1
                condition_counts[metric]["abstained"] += 1
                continue
            if int(baseline.get("denominator") or 0) <= 0:
                by_metric[metric]["missing"] += 1
                condition_counts[metric]["missing"] += 1
                continue
            by_metric[metric]["total"] += 1
            condition_counts[metric]["total"] += 1
            if contribution_signature(baseline) == contribution_signature(cross):
                by_metric[metric]["agree"] += 1
                condition_counts[metric]["agree"] += 1
        by_condition[condition] = finalize_agreement_counts(condition_counts)
    return {
        "by_metric": finalize_agreement_counts(by_metric),
        "by_condition": by_condition,
    }


def contribution_map(report: Mapping[str, Any]) -> dict[tuple[str, int, str, str], Mapping[str, Any]]:
    out: dict[tuple[str, int, str, str], Mapping[str, Any]] = {}
    for detail in report.get("details") or []:
        if not isinstance(detail, Mapping):
            continue
        seq_id = str(detail.get("sequence_id") or "")
        try:
            session_index = int(detail.get("session_index") or 0)
        except (TypeError, ValueError):
            session_index = 0
        for metric_name, contribution in as_mapping(detail.get("metric_contributions")).items():
            out[(seq_id, session_index, "__detail__", str(metric_name))] = as_mapping(contribution)
        for label_result in detail.get("label_results") or []:
            if not isinstance(label_result, Mapping):
                continue
            label_id = str(label_result.get("label_id") or "")
            for metric_name, contribution in as_mapping(label_result.get("metric_contributions")).items():
                out[(seq_id, session_index, label_id, str(metric_name))] = as_mapping(contribution)
    return out


def contribution_signature(contribution: Mapping[str, Any]) -> tuple[int, int]:
    return (int(contribution.get("numerator") or 0), int(contribution.get("denominator") or 0))


def finalize_agreement_counts(counts: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for metric, payload in counts.items():
        total = int(payload.get("total") or 0)
        agree = int(payload.get("agree") or 0)
        out[metric] = {
            "agree": agree,
            "total": total,
            "rate": safe_div(agree, total),
            "missing": int(payload.get("missing") or 0),
            "abstained": int(payload.get("abstained") or 0),
        }
    return out


def survival_summary(cross_reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    values = {
        condition: {
            metric: numeric_or_none(payload.get("value"))
            for metric, payload in aggregate_metrics(report).items()
        }
        for condition, report in cross_reports.items()
    }
    metric_verdicts = {metric: metric_verdict(values, metric) for metric in HEADLINE_SLICE_METRICS}
    family_verdicts = {
        family: family_verdict(metric_verdicts, metrics)
        for family, metrics in FAMILIES.items()
    }
    best_or_tied_count = sum(
        1 for payload in family_verdicts.values()
        if payload["verdict"] in {"strict_win", "tie_for_best"}
    )
    return {
        "df_metric_verdicts_vs_best_B0_B7": metric_verdicts,
        "df_family_verdicts_vs_best_B0_B7": family_verdicts,
        "task_coupled_family_win_survives": (
            family_verdicts.get("task-coupled", {}).get("verdict") == "strict_win"
        ),
        "independent_family_best_or_tied_count": best_or_tied_count,
        "independent_family_4_of_5_survives": best_or_tied_count >= 4,
        "rule": (
            "A family survives if DF is not worse than the best available B0-B7 baseline "
            "on every metric in the family; strict_win requires at least one strict metric win."
        ),
    }


def metric_verdict(values: Mapping[str, Mapping[str, float | None]], metric: str) -> dict[str, Any]:
    direction = METRIC_DIRECTIONS[metric]
    df_value = values.get("DF", {}).get(metric)
    baselines = {
        condition: metrics.get(metric)
        for condition, metrics in values.items()
        if re.fullmatch(r"B\d+", condition)
    }
    baselines = {condition: value for condition, value in baselines.items() if value is not None}
    if df_value is None or not baselines:
        return {
            "metric": metric,
            "direction": direction,
            "df": df_value,
            "best_baseline": None,
            "best_baseline_conditions": [],
            "verdict": "not_computable",
        }
    best = min(baselines.values()) if direction == "lo" else max(baselines.values())
    best_conditions = sorted(condition for condition, value in baselines.items() if value == best)
    if direction == "lo":
        if df_value < best:
            verdict = "strict_win"
        elif df_value == best:
            verdict = "tie_for_best"
        else:
            verdict = "loss"
    else:
        if df_value > best:
            verdict = "strict_win"
        elif df_value == best:
            verdict = "tie_for_best"
        else:
            verdict = "loss"
    return {
        "metric": metric,
        "direction": direction,
        "df": df_value,
        "best_baseline": best,
        "best_baseline_conditions": best_conditions,
        "verdict": verdict,
    }


def family_verdict(
    metric_verdicts: Mapping[str, Mapping[str, Any]],
    metrics: Sequence[str],
) -> dict[str, Any]:
    verdicts = [as_mapping(metric_verdicts.get(metric)).get("verdict") for metric in metrics]
    if any(verdict == "not_computable" for verdict in verdicts):
        verdict = "not_computable"
    elif any(verdict == "loss" for verdict in verdicts):
        verdict = "loss"
    elif any(verdict == "strict_win" for verdict in verdicts):
        verdict = "strict_win"
    else:
        verdict = "tie_for_best"
    return {
        "metrics": list(metrics),
        "metric_verdicts": {metric: as_mapping(metric_verdicts.get(metric)).get("verdict") for metric in metrics},
        "verdict": verdict,
    }


def judge_call_summary(calls: Sequence[JudgeCall]) -> dict[str, Any]:
    by_status: dict[str, int] = defaultdict(int)
    by_question: dict[str, int] = defaultdict(int)
    abstentions: list[dict[str, Any]] = []
    total_latency = 0.0
    for call in calls:
        by_status[call.status] += 1
        by_question[call.question] += 1
        total_latency += call.latency_seconds
        if call.status == "abstain":
            abstentions.append(
                {
                    "condition": call.condition,
                    "sequence_id": call.sequence_id,
                    "label_id": call.label_id,
                    "question": call.question,
                    "attempts": call.attempts,
                    "error": call.error,
                }
            )
    return {
        "total": len(calls),
        "ok": by_status.get("ok", 0),
        "abstentions": by_status.get("abstain", 0),
        "by_status": dict(sorted(by_status.items())),
        "by_question": dict(sorted(by_question.items())),
        "latency_seconds": total_latency,
        "abstention_details": abstentions,
    }


def render_markdown(output: Mapping[str, Any]) -> str:
    metadata = as_mapping(output.get("metadata"))
    survival = as_mapping(output.get("survival"))
    conditions = as_mapping(output.get("conditions"))
    judge_calls = as_mapping(output.get("judge_calls"))
    lines = [
        "# Cross-Model Hygiene Rescore",
        "",
        f"- Generated: `{metadata.get('generated_at')}`",
        f"- Results root: `{metadata.get('results_root')}`",
        f"- Sequence records: `{metadata.get('sequence_records')}`",
        f"- Cross-model judge: `{metadata.get('judge_selector')}`",
        f"- Pass@1 untouched: `{str(metadata.get('executable_pass_at_1_untouched')).lower()}`",
        f"- Judge calls: `{judge_calls.get('total')}` ok=`{judge_calls.get('ok')}` abstain=`{judge_calls.get('abstentions')}`",
        "",
        "This rescore uses the same stored container records. It does not rerun agents and does not recompute executable Pass@1.",
        "",
        "## Survival Check",
        "",
        f"- Task-coupled family strict win survives: `{str(survival.get('task_coupled_family_win_survives')).lower()}`",
        f"- 4/5 independent-family best-or-tied verdict survives: `{str(survival.get('independent_family_4_of_5_survives')).lower()}`",
        f"- Best-or-tied family count: `{survival.get('independent_family_best_or_tied_count')}`",
        "",
        "## Per-Condition Cross-Model Hygiene",
        "",
        "| Condition | Records | Abstentions | ContradictionRepairAccuracy | HumanFeedbackUseAccuracy | TransferScore | ScopeAccuracy | UsefulMemoryPrecision | RepeatedErrorRate | RegressionAfterUpdate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for condition, payload in sorted(conditions.items()):
        metrics = as_mapping(payload).get("cross_model_metrics") or {}
        metrics = as_mapping(metrics)
        lines.append(
            "| "
            + " | ".join(
                [
                    condition,
                    str(as_mapping(payload).get("record_count")),
                    str(len(as_mapping(payload).get("abstentions") or [])),
                    fmt_metric(metrics, "ContradictionRepairAccuracy"),
                    fmt_metric(metrics, "HumanFeedbackUseAccuracy"),
                    fmt_metric(metrics, "TransferScore"),
                    fmt_metric(metrics, "ScopeAccuracy"),
                    fmt_metric(metrics, "UsefulMemoryPrecision"),
                    fmt_metric(metrics, "RepeatedErrorRate"),
                    fmt_metric(metrics, "RegressionAfterUpdate"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## DF Family Verdicts",
            "",
            "| Family | Metrics | Verdict |",
            "|---|---|---|",
        ]
    )
    for family, payload in sorted(as_mapping(survival.get("df_family_verdicts_vs_best_B0_B7")).items()):
        lines.append(
            f"| {family} | {', '.join(as_mapping(payload).get('metrics') or [])} | {as_mapping(payload).get('verdict')} |"
        )
    return "\n".join(lines)


def fmt_metric(metrics: Mapping[str, Any], name: str) -> str:
    metric = as_mapping(metrics.get(name))
    value = numeric_or_none(metric.get("value"))
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def record_session(record: Mapping[str, Any]) -> int:
    task = as_mapping(record.get("task"))
    try:
        return int(task.get("session_index") or record.get("session_index") or 0)
    except (TypeError, ValueError):
        return 0


def safe_div(numerator: float | int, denominator: float | int) -> float | None:
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def numeric_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    return None


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
