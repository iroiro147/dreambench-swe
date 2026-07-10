#!/usr/bin/env python3
"""Fold DreamForge live result directories into paper-ready summaries.

The fold is intentionally read-only with respect to experiment outputs: it
discovers completed result JSON files, selects the latest live cohort for each
requested condition and seed, deduplicates records by sequence/session, and
writes analysis artifacts under the requested output directory.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "experiments" / "results"
DEFAULT_OUT = ROOT / "analysis" / "fold"
RUN_STAMP_RE = re.compile(r"\d{8}T\d{6}Z")

HEADLINE_SLICE_METRICS = (
    "StaleMemoryActivationRate",
    "ContradictionRepairAccuracy",
    "RepeatedErrorRate",
    "HumanFeedbackUseAccuracy",
    "HarmfulMemoryRate",
    "UsefulMemoryPrecision",
    "ScopeAccuracy",
    "TransferScore",
    "RegressionAfterUpdate",
)

MAIN_TABLE_HYGIENE = (
    "TransferScore",
    "ContradictionRepairAccuracy",
    "UsefulMemoryPrecision",
    "HumanFeedbackUseAccuracy",
    "ScopeAccuracy",
    "HarmfulMemoryRate",
    "StaleMemoryActivationRate",
    "RepeatedErrorRate",
)

PRIMARY_HYGIENE_BY_TYPE = {
    "convention-learning": "UsefulMemoryPrecision",
    "generated-files": "StaleMemoryActivationRate",
    "stale-architecture": "ContradictionRepairAccuracy",
    "reviewer-preference": "HarmfulMemoryRate",
    "flaky-test": "RepeatedErrorRate",
}

CONDITION_LABELS = {
    "B0": "B0 No memory",
    "B1": "B1 Raw episodic",
    "B2": "B2 Vector traces",
    "B3": "B3 Reflection-only",
    "B4": "B4 Untyped summary",
    "B5": "B5 Mem0-style",
    "B6": "B6 Subtask memory",
    "B7": "B7 Task tracker",
    "DF": "DF \\sys{} full",
    "A0": "A0 Episodic-only",
    "A2": "A2 No contradiction repair",
    "A4": "A4 No counterfactual replay",
    "A5": "A5 No stale suppression",
    "A6": "A6 No retrieval gate",
    "A11": "A11 Forced consolidation",
}


@dataclass(frozen=True)
class Candidate:
    condition: str
    path: Path
    run_dir: Path
    run_group: str
    sort_key: tuple[str, str, str]
    data: Mapping[str, Any]
    manifest: Mapping[str, Any]
    gate: Mapping[str, Any]
    condition_gate: Mapping[str, Any]
    dry_run: bool
    judge_models: tuple[str, ...]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conditions = [value.strip().upper() for value in args.conditions.split(",") if value.strip()]
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "seed": args.seed,
        "conditions_requested": conditions,
        "judge_model_filter": args.judge_model,
        "results_root": str(RESULTS_ROOT.relative_to(ROOT)),
        "conditions": {},
    }

    all_candidates = discover_candidates(args.seed, set(conditions), args.judge_model)
    for condition in conditions:
        summary["conditions"][condition] = fold_condition(
            condition,
            all_candidates.get(condition, []),
            judge_model_filter=args.judge_model,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"fold_seed{args.seed}.json"
    tex_path = out_dir / f"tables_seed{args.seed}.tex"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tex_path.write_text(render_tex(summary) + "\n", encoding="utf-8")

    print_summary(summary)
    print(f"Wrote {display_path(json_path)}")
    print(f"Wrote {display_path(tex_path)}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fold DreamForge result dirs into JSON and LaTeX table rows.",
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--conditions", required=True)
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Optional judge model filter for DF/ablation result dirs.",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT.relative_to(ROOT)))
    return parser.parse_args(argv)


def discover_candidates(
    seed: int,
    requested_conditions: set[str],
    judge_model_filter: str | None,
) -> dict[str, list[Candidate]]:
    by_condition: dict[str, list[Candidate]] = {condition: [] for condition in requested_conditions}
    for path in sorted(RESULTS_ROOT.glob("**/results.json")):
        data = read_json(path)
        if not data:
            continue
        records = data.get("records")
        if not isinstance(records, list) or not records:
            continue

        manifest = as_mapping(data.get("manifest"))
        condition = infer_condition(path, data, manifest)
        if condition not in requested_conditions:
            continue
        if coerce_int(manifest.get("seed") or data.get("seed")) != seed:
            continue

        run_dir = infer_run_dir(path, condition)
        gate = read_json(run_dir / "gate_report.json")
        condition_gate = as_mapping(as_mapping(gate.get("conditions")).get(condition))
        dry_run = infer_dry_run(path, data, manifest, gate)
        if dry_run:
            continue

        judge_models = collect_judge_models(data, manifest, gate, condition_gate, condition)
        uses_judge = condition_uses_judge(condition, data, condition_gate)
        if judge_model_filter and uses_judge:
            if not any(model_matches(model, judge_model_filter) for model in judge_models):
                continue

        run_group = infer_run_group(run_dir, manifest, gate)
        updated_at = first_string(
            manifest.get("updated_at"),
            manifest.get("created_at"),
            gate.get("created_at"),
            run_group,
        )
        candidate = Candidate(
            condition=condition,
            path=path,
            run_dir=run_dir,
            run_group=run_group,
            sort_key=(run_group, updated_at, str(path)),
            data=data,
            manifest=manifest,
            gate=gate,
            condition_gate=condition_gate,
            dry_run=dry_run,
            judge_models=judge_models,
        )
        by_condition.setdefault(condition, []).append(candidate)
    return by_condition


def fold_condition(
    condition: str,
    candidates: list[Candidate],
    *,
    judge_model_filter: str | None,
) -> dict[str, Any]:
    if not candidates:
        reason = "no live result dirs"
        if judge_model_filter and is_judge_condition(condition):
            reason += f" matching judge_model={judge_model_filter}"
        return no_data_summary(condition, reason)

    latest_group = max(candidate.run_group for candidate in candidates)
    selected = [candidate for candidate in candidates if candidate.run_group == latest_group]
    selected.sort(key=lambda candidate: candidate.sort_key)

    kept_records, record_sources = dedupe_records(selected)
    if not kept_records:
        return no_data_summary(condition, f"latest cohort {latest_group} has no records")

    records = list(kept_records.values())
    source_counts: dict[Path, int] = defaultdict(int)
    for source in record_sources.values():
        source_counts[source.path] += 1

    tokens, estimated_cost, cost_breakdown = aggregate_costs(selected, source_counts)
    hygiene = aggregate_hygiene(selected, kept_records, record_sources)
    per_type = aggregate_per_type(records, hygiene["by_sequence_type"], estimated_cost)

    warmup_records = [record for record in records if record_session(record) in (1, 2)]
    s3_records = [record for record in records if record_session(record) == 3]
    all_successes = sum(1 for record in records if record_passed(record))
    sleep_error_count = sum(1 for record in records if bool(record.get("sleep_error")))
    suspect = bool(sleep_error_count > 0)
    status = "SUSPECT" if suspect else "ok"
    judge_models = sorted({model for candidate in selected for model in candidate.judge_models})

    return {
        "status": status,
        "suspect": suspect,
        "condition": condition,
        "condition_label": CONDITION_LABELS.get(condition, condition),
        "run_group": latest_group,
        "result_dirs_used": [
            str(candidate.path.parent.relative_to(ROOT))
            for candidate in selected
            if source_counts.get(candidate.path, 0) > 0
        ],
        "result_dirs_selected": [str(candidate.path.parent.relative_to(ROOT)) for candidate in selected],
        "n_records": len(records),
        "n_sequences": len({record_sequence_id(record) for record in records if record_sequence_id(record)}),
        "n_s1_s2": len(warmup_records),
        "n_s3": len(s3_records),
        "s1_s2_task_success": rate_payload(warmup_records),
        "s3_task_success": rate_payload(s3_records),
        "overall_task_success": rate_payload(records),
        "pass_at_1": pass_at_1_payload(records),
        "hygiene_metrics": hygiene["metrics"],
        "per_sequence_type": per_type,
        "total_tokens": tokens.get("total"),
        "token_breakdown": tokens,
        "estimated_cost": estimated_cost,
        "estimated_cost_breakdown": cost_breakdown,
        "cost_per_successful_task": safe_div(estimated_cost, all_successes),
        "sleep_cost_share": safe_div(tokens.get("sleep"), tokens.get("total")),
        "sleep_error_count": sleep_error_count,
        "judge": {
            "models": judge_models,
            "calls": aggregate_judge_calls(selected, source_counts),
        },
    }


def no_data_summary(condition: str, reason: str) -> dict[str, Any]:
    return {
        "status": "no_data",
        "condition": condition,
        "condition_label": CONDITION_LABELS.get(condition, condition),
        "no_data_reason": reason,
        "result_dirs_used": [],
        "n_records": 0,
        "n_sequences": 0,
        "n_s1_s2": 0,
        "n_s3": 0,
        "s1_s2_task_success": empty_rate(),
        "s3_task_success": empty_rate(),
        "overall_task_success": empty_rate(),
        "pass_at_1": empty_rate(),
        "hygiene_metrics": {name: empty_metric(name, "no_data") for name in HEADLINE_SLICE_METRICS},
        "per_sequence_type": {},
        "total_tokens": None,
        "token_breakdown": {},
        "estimated_cost": None,
        "estimated_cost_breakdown": {},
        "cost_per_successful_task": None,
        "sleep_cost_share": None,
        "sleep_error_count": 0,
        "judge": {"models": [], "calls": 0},
    }


def dedupe_records(
    selected: list[Candidate],
) -> tuple[dict[tuple[str, int], Mapping[str, Any]], dict[tuple[str, int], Candidate]]:
    kept: dict[tuple[str, int], Mapping[str, Any]] = {}
    sources: dict[tuple[str, int], Candidate] = {}
    order: dict[tuple[str, int], tuple[tuple[str, str, str], int]] = {}
    for candidate in selected:
        for ordinal, record in enumerate(candidate.data.get("records") or []):
            if not isinstance(record, Mapping):
                continue
            key = record_key(record)
            if key is None:
                continue
            current_order = (candidate.sort_key, ordinal)
            if key not in order or current_order >= order[key]:
                order[key] = current_order
                kept[key] = record
                sources[key] = candidate
    return kept, sources


def aggregate_hygiene(
    selected: list[Candidate],
    kept_records: Mapping[tuple[str, int], Mapping[str, Any]],
    record_sources: Mapping[tuple[str, int], Candidate],
) -> dict[str, Any]:
    detail_maps = {candidate.path: detail_map(candidate) for candidate in selected}
    totals = empty_totals()
    by_sequence_type: dict[str, dict[str, dict[str, float]]] = defaultdict(empty_totals)
    used_detail = False

    for key, record in kept_records.items():
        source = record_sources[key]
        detail = detail_maps.get(source.path, {}).get(key)
        if not detail:
            continue
        seq_type = record_sequence_type(record)
        for metric_name, numerator, denominator in detail_contributions(detail):
            if metric_name not in HEADLINE_SLICE_METRICS:
                continue
            totals[metric_name]["numerator"] += numerator
            totals[metric_name]["denominator"] += denominator
            by_sequence_type[seq_type][metric_name]["numerator"] += numerator
            by_sequence_type[seq_type][metric_name]["denominator"] += denominator
            used_detail = True

    fallback_totals = aggregate_slice_report_totals(selected)
    metrics: dict[str, dict[str, Any]] = {}
    for name in HEADLINE_SLICE_METRICS:
        source = "slice_report.details" if used_detail else "slice_report.aggregate"
        metric_total = totals[name]
        if metric_total["denominator"] <= 0 and fallback_totals[name]["denominator"] > 0:
            metric_total = fallback_totals[name]
            source = "slice_report.aggregate"
        metrics[name] = metric_payload(name, metric_total, source)

    return {
        "metrics": metrics,
        "by_sequence_type": {
            seq_type: {
                name: metric_payload(name, metric_total, "slice_report.details")
                for name, metric_total in totals_by_metric.items()
            }
            for seq_type, totals_by_metric in sorted(by_sequence_type.items())
        },
    }


def detail_map(candidate: Candidate) -> dict[tuple[str, int], Mapping[str, Any]]:
    out: dict[tuple[str, int], Mapping[str, Any]] = {}
    slice_report = as_mapping(candidate.data.get("slice_report"))
    details = slice_report.get("details") or []
    if not isinstance(details, list):
        return out
    for detail in details:
        if not isinstance(detail, Mapping):
            continue
        seq_id = first_string(detail.get("sequence_id"), detail.get("seq_id"))
        session_index = coerce_int(detail.get("session_index"))
        if seq_id and session_index is not None:
            out[(seq_id, session_index)] = detail
    return out


def detail_contributions(detail: Mapping[str, Any]) -> Iterable[tuple[str, float, float]]:
    for metric_name, contribution in as_mapping(detail.get("metric_contributions")).items():
        numerator, denominator = contribution_values(contribution)
        yield str(metric_name), numerator, denominator
    label_results = detail.get("label_results") or []
    if not isinstance(label_results, list):
        return
    for label_result in label_results:
        if not isinstance(label_result, Mapping):
            continue
        for metric_name, contribution in as_mapping(label_result.get("metric_contributions")).items():
            numerator, denominator = contribution_values(contribution)
            yield str(metric_name), numerator, denominator


def aggregate_slice_report_totals(selected: list[Candidate]) -> dict[str, dict[str, float]]:
    totals = empty_totals()
    for candidate in selected:
        metrics = as_mapping(
            as_mapping(as_mapping(candidate.data.get("slice_report")).get("aggregate")).get("metrics")
        )
        for name in HEADLINE_SLICE_METRICS:
            payload = as_mapping(metrics.get(name))
            totals[name]["numerator"] += coerce_float(payload.get("numerator")) or 0.0
            totals[name]["denominator"] += coerce_float(payload.get("denominator")) or 0.0
    return totals


def aggregate_per_type(
    records: list[Mapping[str, Any]],
    hygiene_by_type: Mapping[str, Mapping[str, Mapping[str, Any]]],
    estimated_cost: float | None,
) -> dict[str, Any]:
    total_records = len(records)
    out: dict[str, Any] = {}
    records_by_type: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_type[record_sequence_type(record)].append(record)

    for seq_type, type_records in sorted(records_by_type.items()):
        s3_records = [record for record in type_records if record_session(record) == 3]
        successes = sum(1 for record in type_records if record_passed(record))
        type_cost = None
        if estimated_cost is not None and total_records > 0:
            type_cost = estimated_cost * (len(type_records) / total_records)
        primary_metric = PRIMARY_HYGIENE_BY_TYPE.get(seq_type)
        primary_hygiene = None
        if primary_metric:
            primary_hygiene = as_mapping(as_mapping(hygiene_by_type.get(seq_type)).get(primary_metric))
        out[seq_type] = {
            "n_records": len(type_records),
            "n_s3": len(s3_records),
            "s3_task_success": rate_payload(s3_records),
            "primary_hygiene_metric": primary_metric,
            "primary_hygiene": primary_hygiene or empty_metric(primary_metric or "unknown", "not_available"),
            "estimated_cost_prorated": type_cost,
            "cost_per_successful_task": safe_div(type_cost, successes),
        }
    return out


def aggregate_costs(
    selected: list[Candidate],
    source_counts: Mapping[Path, int],
) -> tuple[dict[str, float | None], float | None, dict[str, float]]:
    token_totals: dict[str, float] = defaultdict(float)
    cost_breakdown: dict[str, float] = defaultdict(float)
    estimated_cost = 0.0
    saw_cost = False

    for candidate in selected:
        records = candidate.data.get("records") or []
        if not records:
            continue
        contributed = source_counts.get(candidate.path, 0)
        if contributed <= 0:
            continue
        factor = contributed / len(records)
        manifest = candidate.manifest
        tokens = as_mapping(manifest.get("tokens"))
        token_total = coerce_float(tokens.get("total"))
        if token_total is None:
            token_total = sum(
                coerce_float(tokens.get(key)) or 0.0
                for key in ("wake", "sleep", "judge")
            )
        if token_total == 0:
            token_total = coerce_float(as_mapping(candidate.data.get("metrics")).get("TotalTokens"))

        for key in ("wake", "sleep", "judge", "wake_input", "wake_output", "admitted_memory_total"):
            value = coerce_float(tokens.get(key))
            if value is not None:
                token_totals[key] += value * factor
        if token_total is not None:
            token_totals["total"] += token_total * factor

        raw_cost = coerce_float(manifest.get("estimated_cost"))
        if raw_cost is not None:
            estimated_cost += raw_cost * factor
            saw_cost = True
        for key, value in as_mapping(manifest.get("estimated_cost_breakdown")).items():
            numeric = coerce_float(value)
            if numeric is not None:
                cost_breakdown[str(key)] += numeric * factor

    if "total" not in token_totals:
        subtotal = sum(token_totals.get(key, 0.0) for key in ("wake", "sleep", "judge"))
        if subtotal > 0:
            token_totals["total"] = subtotal

    tokens_out = {key: round_clean(value) for key, value in sorted(token_totals.items())}
    return tokens_out, round_clean(estimated_cost) if saw_cost else None, {
        key: round_clean(value) for key, value in sorted(cost_breakdown.items())
    }


def aggregate_judge_calls(selected: list[Candidate], source_counts: Mapping[Path, int]) -> int:
    total = 0.0
    for candidate in selected:
        records = candidate.data.get("records") or []
        if not records:
            continue
        contributed = source_counts.get(candidate.path, 0)
        if contributed <= 0:
            continue
        factor = contributed / len(records)
        judge = as_mapping(candidate.data.get("judge")) or as_mapping(candidate.condition_gate.get("judge"))
        calls = coerce_float(judge.get("calls"))
        if calls is not None:
            total += calls * factor
    return int(round(total))


def render_tex(summary: Mapping[str, Any]) -> str:
    conditions = list(summary.get("conditions_requested") or [])
    summaries = as_mapping(summary.get("conditions"))
    lines: list[str] = []
    lines.append(f"% Auto-generated by scripts/fold_results.py for seed {summary.get('seed')}.")
    lines.append("% Paste rows into paper/sections/07_results.tex after reviewing no_data/SUSPECT comments.")
    lines.append("")
    lines.extend(render_main_rows(conditions, summaries))
    lines.append("")
    lines.extend(render_hygiene_rows(conditions, summaries))
    lines.append("")
    lines.extend(render_ablation_rows(conditions, summaries))
    lines.append("")
    lines.extend(render_per_type_rows(conditions, summaries))
    lines.append("")
    lines.extend(render_pareto_block(conditions, summaries))
    return "\n".join(lines)


def render_main_rows(conditions: list[str], summaries: Mapping[str, Any]) -> list[str]:
    lines = [
        "% Main results table rows (tab:main-results).",
        "% Columns: Condition & S3 Task Success & S1+S2 Warmup & Transfer & Contra. Repair Acc & Useful Mem Prec & HF Use Acc & Scope Acc & Harmful Mem Rate & Stale Mem Act. & Repeated Error Rate",
    ]
    for condition in conditions:
        item = as_mapping(summaries.get(condition))
        metrics = as_mapping(item.get("hygiene_metrics"))
        values = [
            item.get("condition_label") or CONDITION_LABELS.get(condition, condition),
            fmt_rate(rate_value(item.get("s3_task_success"))),
            fmt_rate(rate_value(item.get("s1_s2_task_success"))),
        ]
        values.extend(fmt_rate(metric_value(metrics.get(name))) for name in MAIN_TABLE_HYGIENE)
        lines.append(" & ".join(values) + r" \\" + status_comment(item))
    return lines


def render_hygiene_rows(conditions: list[str], summaries: Mapping[str, Any]) -> list[str]:
    lines = [
        "% Hygiene table rows (all headline slice metrics).",
        "% Columns: Condition & " + " & ".join(HEADLINE_SLICE_METRICS),
    ]
    for condition in conditions:
        item = as_mapping(summaries.get(condition))
        metrics = as_mapping(item.get("hygiene_metrics"))
        values = [item.get("condition_label") or CONDITION_LABELS.get(condition, condition)]
        values.extend(fmt_rate(metric_value(metrics.get(name))) for name in HEADLINE_SLICE_METRICS)
        lines.append(" & ".join(values) + r" \\" + status_comment(item))
    return lines


def render_ablation_rows(conditions: list[str], summaries: Mapping[str, Any]) -> list[str]:
    lines = [
        "% Operation ablation table rows (tab:ablations).",
        "% Columns: Condition & TaskSuccess(S3) & Pass@1 & ContradictionRepairAccuracy & RepeatedErrorRate & RegressionAfterUpdate & SleepCostShare",
    ]
    for condition in conditions:
        if condition != "DF" and not condition.startswith("A"):
            continue
        item = as_mapping(summaries.get(condition))
        metrics = as_mapping(item.get("hygiene_metrics"))
        values = [
            item.get("condition_label") or CONDITION_LABELS.get(condition, condition),
            fmt_rate(rate_value(item.get("s3_task_success"))),
            fmt_rate(rate_value(item.get("pass_at_1"))),
            fmt_rate(metric_value(metrics.get("ContradictionRepairAccuracy"))),
            fmt_rate(metric_value(metrics.get("RepeatedErrorRate"))),
            fmt_rate(metric_value(metrics.get("RegressionAfterUpdate"))),
            fmt_rate(item.get("sleep_cost_share")),
        ]
        lines.append(" & ".join(values) + r" \\" + status_comment(item))
    return lines


def render_per_type_rows(conditions: list[str], summaries: Mapping[str, Any]) -> list[str]:
    lines = [
        "% Per-sequence-type breakdown rows (tab:sequence-breakdown).",
        "% Columns: Sequence type & Primary task metric & Primary hygiene metric & Cost metric",
    ]
    all_types = sorted({
        seq_type
        for condition in conditions
        for seq_type in as_mapping(as_mapping(summaries.get(condition)).get("per_sequence_type")).keys()
    })
    ordered_types = [seq_type for seq_type in PRIMARY_HYGIENE_BY_TYPE if seq_type in all_types]
    ordered_types.extend(seq_type for seq_type in all_types if seq_type not in ordered_types)
    for seq_type in ordered_types:
        primary_metric = PRIMARY_HYGIENE_BY_TYPE.get(seq_type, "primary")
        task_bits: list[str] = []
        hygiene_bits: list[str] = []
        cost_bits: list[str] = []
        for condition in conditions:
            item = as_mapping(summaries.get(condition))
            per_type = as_mapping(as_mapping(item.get("per_sequence_type")).get(seq_type))
            if not per_type:
                continue
            task_bits.append(f"{condition} {fmt_rate(rate_value(per_type.get('s3_task_success')))}")
            hygiene_bits.append(f"{condition} {fmt_rate(metric_value(per_type.get('primary_hygiene')))}")
            cost_bits.append(f"{condition} {fmt_cost(per_type.get('cost_per_successful_task'))}")
        label = seq_type[:1].upper() + seq_type[1:]
        values = [
            label,
            "; ".join(task_bits) or "--",
            f"{primary_metric}: " + ("; ".join(hygiene_bits) or "--"),
            "CostPerSuccessfulTask: " + ("; ".join(cost_bits) or "--"),
        ]
        lines.append(" & ".join(values) + r" \\")
    return lines


def render_pareto_block(conditions: list[str], summaries: Mapping[str, Any]) -> list[str]:
    lines = [
        "% Pareto data block: condition, S3-success, estimated_cost.",
        "% PARETO_DATA_BEGIN",
        "% condition,s3_success,estimated_cost",
    ]
    for condition in conditions:
        item = as_mapping(summaries.get(condition))
        if item.get("status") == "no_data":
            lines.append(f"% {condition},no_data,no_data")
            continue
        lines.append(
            f"% {condition},{fmt_csv(rate_value(item.get('s3_task_success')))},{fmt_csv(item.get('estimated_cost'))}"
        )
    lines.append("% PARETO_DATA_END")
    return lines


def print_summary(summary: Mapping[str, Any]) -> None:
    conditions = list(summary.get("conditions_requested") or [])
    summaries = as_mapping(summary.get("conditions"))
    header = (
        f"{'Cond':<5} {'Status':<8} {'N':>4} {'S1+S2':>13} {'S3':>13} "
        f"{'Transfer':>8} {'Tokens':>10} {'Cost':>10} {'SleepErr':>8} {'Dirs':>4}"
    )
    print(header)
    print("-" * len(header))
    for condition in conditions:
        item = as_mapping(summaries.get(condition))
        if item.get("status") == "no_data":
            print(
                f"{condition:<5} {'no_data':<8} {0:>4} {'--':>13} {'--':>13} "
                f"{'--':>8} {'--':>10} {'--':>10} {0:>8} {0:>4}  {item.get('no_data_reason')}"
            )
            continue
        metrics = as_mapping(item.get("hygiene_metrics"))
        print(
            f"{condition:<5} {str(item.get('status')):<8} {int(item.get('n_records') or 0):>4} "
            f"{fmt_count_rate(item.get('s1_s2_task_success')):>13} "
            f"{fmt_count_rate(item.get('s3_task_success')):>13} "
            f"{fmt_rate(metric_value(metrics.get('TransferScore'))):>8} "
            f"{fmt_tokens(item.get('total_tokens')):>10} "
            f"{fmt_cost(item.get('estimated_cost')):>10} "
            f"{int(item.get('sleep_error_count') or 0):>8} "
            f"{len(item.get('result_dirs_used') or []):>4}"
        )


def infer_condition(path: Path, data: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    manifest_condition = first_string(manifest.get("condition"), data.get("condition"))
    if manifest_condition:
        return manifest_condition.upper()
    parent = path.parent.name.upper()
    if re.fullmatch(r"[A-Z][A-Z0-9]*", parent):
        return parent
    return parent


def infer_run_dir(path: Path, condition: str) -> Path:
    if path.parent.name.upper() == condition.upper() and path.parent.parent != RESULTS_ROOT:
        return path.parent.parent
    return path.parent


def infer_dry_run(
    path: Path,
    data: Mapping[str, Any],
    manifest: Mapping[str, Any],
    gate: Mapping[str, Any],
) -> bool:
    for value in (gate.get("dry_run"), manifest.get("dry_run"), data.get("dry_run")):
        if isinstance(value, bool):
            return value
    return any("DRYRUN" in part.upper() for part in path.parts)


def infer_run_group(run_dir: Path, manifest: Mapping[str, Any], gate: Mapping[str, Any]) -> str:
    match = RUN_STAMP_RE.search(run_dir.name)
    if match:
        return match.group(0)
    created_at = first_string(manifest.get("created_at"), gate.get("created_at"))
    if created_at:
        return created_at
    return run_dir.name


def collect_judge_models(
    data: Mapping[str, Any],
    manifest: Mapping[str, Any],
    gate: Mapping[str, Any],
    condition_gate: Mapping[str, Any],
    condition: str,
) -> tuple[str, ...]:
    models = {
        first_string(as_mapping(condition_gate.get("judge")).get("model")),
        first_string(as_mapping(data.get("judge")).get("model")),
        first_string(manifest.get("judge_model")),
        first_string(as_mapping(manifest.get("slice_label_judge")).get("model")),
    }
    if is_judge_condition(condition):
        models.add(first_string(gate.get("judge_model")))
    return tuple(sorted(model for model in models if model))


def model_matches(actual: str, expected: str) -> bool:
    actual_norm = normalize_model(actual)
    expected_norm = normalize_model(expected)
    return actual_norm == expected_norm


def normalize_model(model: str) -> str:
    value = model.strip().lower()
    if value.startswith("cc2-opus"):
        value = value.replace("cc2-opus", "claude-opus", 1)
    return value


def is_judge_condition(condition: str) -> bool:
    return condition == "DF" or condition.startswith("A")


def condition_uses_judge(
    condition: str,
    data: Mapping[str, Any],
    condition_gate: Mapping[str, Any],
) -> bool:
    if is_judge_condition(condition):
        return True
    return bool(as_mapping(data.get("judge")).get("live_llm_used")) or bool(
        as_mapping(condition_gate.get("judge")).get("live_llm_used")
    )


def record_key(record: Mapping[str, Any]) -> tuple[str, int] | None:
    seq_id = record_sequence_id(record)
    session_index = record_session(record)
    if not seq_id or session_index is None:
        return None
    return seq_id, session_index


def record_sequence_id(record: Mapping[str, Any]) -> str:
    task = as_mapping(record.get("task"))
    return first_string(
        task.get("seq_id"),
        task.get("sequence_id"),
        record.get("seq_id"),
        record.get("sequence_id"),
    )


def record_sequence_type(record: Mapping[str, Any]) -> str:
    task = as_mapping(record.get("task"))
    return first_string(task.get("seq_type"), record.get("seq_type"), "unknown")


def record_session(record: Mapping[str, Any]) -> int | None:
    task = as_mapping(record.get("task"))
    return coerce_int(task.get("session_index") or record.get("session_index"))


def record_passed(record: Mapping[str, Any]) -> bool:
    return bool(record.get("final_passed"))


def rate_payload(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    denominator = len(records)
    numerator = sum(1 for record in records if record_passed(record))
    return {
        "name": "TaskSuccess",
        "numerator": numerator,
        "denominator": denominator,
        "value": safe_div(numerator, denominator),
    }


def pass_at_1_payload(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    denominator = len(records)
    numerator = sum(1 for record in records if bool(record.get("pass_at_1", record.get("final_passed"))))
    return {
        "name": "Pass@1",
        "numerator": numerator,
        "denominator": denominator,
        "value": safe_div(numerator, denominator),
    }


def empty_rate() -> dict[str, Any]:
    return {"name": "TaskSuccess", "numerator": 0, "denominator": 0, "value": None}


def empty_metric(name: str, source: str) -> dict[str, Any]:
    return {
        "name": name,
        "numerator": 0,
        "denominator": 0,
        "value": None,
        "source": source,
    }


def empty_totals() -> dict[str, dict[str, float]]:
    return {name: {"numerator": 0.0, "denominator": 0.0} for name in HEADLINE_SLICE_METRICS}


def metric_payload(name: str, total: Mapping[str, float], source: str) -> dict[str, Any]:
    numerator = total.get("numerator", 0.0)
    denominator = total.get("denominator", 0.0)
    return {
        "name": name,
        "numerator": round_clean(numerator),
        "denominator": round_clean(denominator),
        "value": safe_div(numerator, denominator),
        "source": source,
    }


def contribution_values(contribution: Any) -> tuple[float, float]:
    payload = as_mapping(contribution)
    numerator = coerce_float(payload.get("numerator")) or 0.0
    denominator = coerce_float(payload.get("denominator")) or 0.0
    return numerator, denominator


def safe_div(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def first_string(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return ""


def coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def round_clean(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    numeric = float(value)
    if abs(numeric - round(numeric)) < 1e-9:
        return int(round(numeric))
    return round(numeric, 12)


def rate_value(payload: Any) -> float | None:
    return coerce_float(as_mapping(payload).get("value"))


def metric_value(payload: Any) -> float | None:
    return coerce_float(as_mapping(payload).get("value"))


def status_comment(item: Mapping[str, Any]) -> str:
    status = item.get("status")
    if status == "SUSPECT":
        return f" % SUSPECT sleep_error_count={item.get('sleep_error_count')}"
    if status == "no_data":
        return f" % no data: {item.get('no_data_reason')}"
    return ""


def fmt_rate(value: Any) -> str:
    numeric = coerce_float(value)
    return "--" if numeric is None else f"{numeric:.2f}"


def fmt_cost(value: Any) -> str:
    numeric = coerce_float(value)
    return "--" if numeric is None else f"{numeric:.6f}"


def fmt_tokens(value: Any) -> str:
    numeric = coerce_float(value)
    if numeric is None:
        return "--"
    return str(int(round(numeric)))


def fmt_csv(value: Any) -> str:
    numeric = coerce_float(value)
    return "NA" if numeric is None else f"{numeric:.6f}"


def fmt_count_rate(payload: Any) -> str:
    data = as_mapping(payload)
    denominator = coerce_int(data.get("denominator")) or 0
    numerator = coerce_int(data.get("numerator")) or 0
    value = rate_value(data)
    if denominator == 0 or value is None:
        return "--"
    return f"{numerator}/{denominator} {value:.2f}"


if __name__ == "__main__":
    sys.exit(main())
