#!/usr/bin/env python3
"""Cost-frontier and hygiene-independence analysis for the seed-1 fold.

All reported numbers are derived from real container-mode result records and
their paired gate reports. The selector matches barrier2's latest-by-mtime
union, while retaining source paths so token accounting can be traced back to
the raw run artifacts.
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(os.environ.get("DREAMFORGE_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts import barrier2_rescore  # noqa: E402

RESULTS_ROOT = ROOT / "experiments" / "results"
OUTPUT_DIR = ROOT / "analysis" / "fold"
CONDITIONS = barrier2_rescore.CONDS
BASELINES = [condition for condition in CONDITIONS if condition.startswith("B")]
HYG = barrier2_rescore.HYG
EPSILON = 1e-12

FAMILIES = {
    "task-coupled": {
        "metrics": [
            "ContradictionRepairAccuracy",
            "HumanFeedbackUseAccuracy",
            "TransferScore",
            "ScopeAccuracy",
        ],
        "note": "repair, human-feedback use, transfer, and scope share task-answer pathways",
    },
    "retrieval-avoidance": {
        "metrics": ["StaleMemoryActivationRate", "HarmfulMemoryRate"],
        "note": "avoid activating stale or harmful retrieved memory",
    },
    "precision": {
        "metrics": ["UsefulMemoryPrecision"],
        "note": "useful retrieved memory divided by all retrieved memory",
    },
    "regression": {
        "metrics": ["RegressionAfterUpdate"],
        "note": "prior-behavior regression after updates",
    },
    "repeated": {
        "metrics": ["RepeatedErrorRate"],
        "note": "same labeled error repeated across sessions",
    },
}


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sequence_map(sequence_records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        str(sequence.get("seq_id") or sequence.get("sequence_id") or ""): sequence
        for sequence in sequence_records
        if isinstance(sequence, Mapping)
    }


def _record_key(record: Mapping[str, Any]) -> tuple[str, int]:
    return barrier2_rescore._record_key(record)  # noqa: SLF001 - shared fold key.


def _sequence_id(record: Mapping[str, Any]) -> str:
    return barrier2_rescore._sequence_id(record)  # noqa: SLF001 - shared fold key.


def _session_index(record: Mapping[str, Any]) -> int:
    return barrier2_rescore._session_index(record)  # noqa: SLF001 - shared fold key.


def _as_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "" or value == "n/a":
            return None
        parsed = float(value)
        if math.isnan(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _gate_condition(condition_dir: Path, condition: str) -> Mapping[str, Any]:
    gate_path = barrier2_rescore._gate_report_path(condition_dir)  # noqa: SLF001 - shared gate lookup.
    if gate_path is None:
        return {}
    gate = _read_json(gate_path)
    conditions = gate.get("conditions") if isinstance(gate.get("conditions"), Mapping) else {}
    payload = conditions.get(condition) if isinstance(conditions.get(condition), Mapping) else {}
    return payload


def _condition_dirs(condition: str, results_root: Path = RESULTS_ROOT) -> list[Path]:
    pattern = str(results_root / "*-gpt-5.5-*" / condition)
    return sorted(Path(path) for path in glob.glob(pattern))


def selected_container_records(
    condition: str,
    *,
    results_root: Path = RESULTS_ROOT,
    sequence_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sequences = _sequence_map(sequence_records)
    selected_by_key: dict[tuple[str, int], tuple[float, Mapping[str, Any], Path]] = {}
    valid_keys_by_path: dict[str, set[tuple[str, int]]] = defaultdict(set)
    excluded: list[dict[str, Any]] = []

    for condition_dir in _condition_dirs(condition, results_root=results_root):
        results_path = condition_dir / "results.json"
        if not results_path.exists():
            continue
        gate_errors = barrier2_rescore._gate_report_errors(condition_dir)  # noqa: SLF001 - shared gate.
        if gate_errors:
            excluded.append(
                {
                    "condition": condition,
                    "path": str(results_path),
                    "scope": "result_dir",
                    "reason": ",".join(gate_errors),
                }
            )
            continue
        try:
            payload = _read_json(results_path)
        except Exception as exc:  # noqa: BLE001 - artifact audit should keep going.
            excluded.append(
                {
                    "condition": condition,
                    "path": str(results_path),
                    "scope": "result_dir",
                    "reason": f"unreadable_results:{type(exc).__name__}",
                }
            )
            continue
        mtime = results_path.stat().st_mtime
        records = payload.get("records") if isinstance(payload.get("records"), list) else []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            key = _record_key(record)
            if not key[0] or not key[1]:
                excluded.append(
                    {
                        "condition": condition,
                        "path": str(results_path),
                        "scope": "record",
                        "reason": "missing_sequence_or_session",
                    }
                )
                continue
            if str(record.get("isolation_mode") or "") != "container":
                excluded.append(
                    {
                        "condition": condition,
                        "path": str(results_path),
                        "scope": "record",
                        "sequence_id": key[0],
                        "session_index": key[1],
                        "reason": "non_container_record",
                    }
                )
                continue
            record_errors = barrier2_rescore._record_validity_errors(record)  # noqa: SLF001 - shared gate.
            if record_errors:
                excluded.append(
                    {
                        "condition": condition,
                        "path": str(results_path),
                        "scope": "record",
                        "sequence_id": key[0],
                        "session_index": key[1],
                        "reason": ",".join(record_errors),
                    }
                )
                continue
            contamination = barrier2_rescore._record_contamination(record, sequences.get(key[0]))  # noqa: SLF001
            if contamination.get("contaminated"):
                excluded.append(
                    {
                        "condition": condition,
                        "path": str(results_path),
                        "scope": "record",
                        "sequence_id": key[0],
                        "session_index": key[1],
                        "reason": barrier2_rescore._contamination_reason(contamination),  # noqa: SLF001
                        "contamination_evidence": contamination.get("evidence") or [],
                    }
                )
                continue
            valid_keys_by_path[str(results_path)].add(key)
            if key not in selected_by_key or mtime > selected_by_key[key][0]:
                selected_by_key[key] = (mtime, record, results_path)

    selected_records = [item[1] for item in selected_by_key.values()]
    selected_keys_by_path: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for key, (_, _, path) in selected_by_key.items():
        selected_keys_by_path[str(path)].add(key)

    partial_source_files = []
    for path, selected_keys in sorted(selected_keys_by_path.items()):
        valid_keys = valid_keys_by_path.get(path, set())
        if selected_keys != valid_keys:
            partial_source_files.append(
                {
                    "path": path,
                    "selected_records": len(selected_keys),
                    "valid_records_in_source": len(valid_keys),
                    "unselected_records": sorted([f"{seq}:S{session}" for seq, session in valid_keys - selected_keys]),
                }
            )

    return {
        "records": selected_records,
        "selected_keys_by_path": selected_keys_by_path,
        "valid_keys_by_path": valid_keys_by_path,
        "partial_source_files": partial_source_files,
        "excluded": excluded,
    }


def _source_cost(path: Path, condition: str) -> dict[str, Any]:
    payload = _read_json(path)
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), Mapping) else {}
    tokens = manifest.get("tokens") if isinstance(manifest.get("tokens"), Mapping) else {}
    condition_dir = path.parent
    gate_condition = _gate_condition(condition_dir, condition)
    gate_judge = gate_condition.get("judge") if isinstance(gate_condition.get("judge"), Mapping) else {}
    result_judge = payload.get("judge") if isinstance(payload.get("judge"), Mapping) else {}
    judge = gate_judge or result_judge
    judge_usage = judge.get("usage") if isinstance(judge.get("usage"), Mapping) else {}
    metrics = gate_condition.get("metrics") if isinstance(gate_condition.get("metrics"), Mapping) else {}
    judge_tokens = _as_int(judge_usage.get("total_tokens") or tokens.get("judge"))
    sleep_tokens = _as_int(tokens.get("sleep"))
    wake_tokens = _as_int(tokens.get("wake"))
    total_tokens = _as_int(tokens.get("total")) or wake_tokens + sleep_tokens + judge_tokens
    return {
        "path": str(path),
        "wake_tokens": wake_tokens,
        "sleep_tokens": sleep_tokens,
        "judge_tokens": judge_tokens,
        "sleep_judge_tokens": sleep_tokens + judge_tokens,
        "total_tokens": total_tokens,
        "judge_call_count": _as_int(judge.get("calls")),
        "reported_sleep_cost_share": _as_float(metrics.get("SleepCostShare")),
        "reported_total_tokens": _as_float(metrics.get("TotalTokens")),
    }


def build_cost_analysis(
    *,
    sequence_records: Sequence[Mapping[str, Any]] | None = None,
    conditions: Sequence[str] = CONDITIONS,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    sequences = list(sequence_records) if sequence_records is not None else barrier2_rescore.load_sequences()
    expected_ids = {str(sequence.get("seq_id") or sequence.get("sequence_id") or "") for sequence in sequences}
    rows: dict[str, Any] = {}
    all_excluded: list[dict[str, Any]] = []
    for condition in conditions:
        selection = selected_container_records(condition, results_root=results_root, sequence_records=sequences)
        records = list(selection["records"])
        all_excluded.extend(selection["excluded"])
        source_paths = sorted(Path(path) for path in selection["selected_keys_by_path"])
        sources = [_source_cost(path, condition) for path in source_paths]
        s3 = [record for record in records if _session_index(record) == 3]
        s3_ids = {_sequence_id(record) for record in s3}
        pass_count = sum(1 for record in s3 if record.get("pass_at_1"))
        wake_tokens = sum(source["wake_tokens"] for source in sources)
        sleep_tokens = sum(source["sleep_tokens"] for source in sources)
        judge_tokens = sum(source["judge_tokens"] for source in sources)
        sleep_judge_tokens = sleep_tokens + judge_tokens
        total_tokens = sum(source["total_tokens"] for source in sources)
        true_sleep_judge_share = (sleep_judge_tokens / total_tokens) if total_tokens else None
        rows[condition] = {
            "condition": condition,
            "n_records": len(records),
            "n_S3": len(s3),
            "missing_sequence_ids": sorted(expected_ids - s3_ids),
            "pass_at_1_successes": pass_count,
            "pass_at_1_rate": (pass_count / len(s3)) if s3 else None,
            "source_result_files": len(source_paths),
            "partial_source_file_count": len(selection["partial_source_files"]),
            "partial_source_files": selection["partial_source_files"],
            "WakeTokens": wake_tokens,
            "SleepTokens": sleep_tokens,
            "JudgeTokens": judge_tokens,
            "SleepJudgeTokens": sleep_judge_tokens,
            "TotalTokens": total_tokens,
            "judge_call_count": sum(source["judge_call_count"] for source in sources),
            "CostPerPass1Success": (total_tokens / pass_count) if pass_count else None,
            "TrueSleepJudgeTokenShare": true_sleep_judge_share,
            "reported_sleep_cost_share_values": sorted(
                {
                    source["reported_sleep_cost_share"]
                    for source in sources
                    if source["reported_sleep_cost_share"] is not None
                }
            ),
            "source_costs": sources,
        }
    return {
        "metadata": {
            "results_root": str(results_root),
            "selection": "container records only; latest by results.json mtime per sequence/session; validity and contamination gated",
            "conditions": list(conditions),
            "expected_s3": barrier2_rescore.EXPECTED_S3,
            "sleep_cost_share_note": (
                "Run gate metrics report SleepCostShare from sleep token cost only. Codex judge usage is stored "
                "as JudgeTokens, so true offline-token share here is SleepJudgeTokens / TotalTokens."
            ),
        },
        "conditions": rows,
        "excluded": all_excluded,
    }


def _metric_value(rows: Mapping[str, Mapping[str, Any]], condition: str, metric: str) -> float | None:
    metric_payload = rows[condition]["hygiene_metrics"].get(metric, {})
    return _as_float(metric_payload.get("value"))


def _pass_value(rows: Mapping[str, Mapping[str, Any]], condition: str) -> float | None:
    return _as_float(rows[condition].get("pass_at_1_rate"))


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom_x = sum(x * x for x in dx)
    denom_y = sum(y * y for y in dy)
    if denom_x <= EPSILON or denom_y <= EPSILON:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / math.sqrt(denom_x * denom_y)


def _correlate(series_a: Mapping[str, float | None], series_b: Mapping[str, float | None]) -> dict[str, Any]:
    xs: list[float] = []
    ys: list[float] = []
    used: list[str] = []
    for condition in CONDITIONS:
        a = series_a.get(condition)
        b = series_b.get(condition)
        if a is None or b is None:
            continue
        xs.append(a)
        ys.append(b)
        used.append(condition)
    return {"r": _pearson(xs, ys), "n": len(xs), "conditions": used}


def _metric_verdict(rows: Mapping[str, Mapping[str, Any]], metric: str, direction: str) -> dict[str, Any]:
    df_value = _metric_value(rows, "DF", metric)
    baseline_values = {
        condition: _metric_value(rows, condition, metric)
        for condition in BASELINES
        if _metric_value(rows, condition, metric) is not None
    }
    if df_value is None or not baseline_values:
        return {"metric": metric, "direction": direction, "df_value": df_value, "verdict": "no_data"}
    best_value = min(baseline_values.values()) if direction == "lo" else max(baseline_values.values())
    strict = df_value < best_value - EPSILON if direction == "lo" else df_value > best_value + EPSILON
    tied = abs(df_value - best_value) <= EPSILON
    verdict = "strict_win" if strict else ("tie_for_best" if tied else "loss")
    return {
        "metric": metric,
        "direction": direction,
        "df_value": df_value,
        "best_baseline_value": best_value,
        "best_baselines": [
            condition for condition, value in baseline_values.items() if abs(value - best_value) <= EPSILON
        ],
        "verdict": verdict,
    }


def _family_verdict(metric_verdicts: Sequence[Mapping[str, Any]]) -> str:
    verdicts = [str(item.get("verdict")) for item in metric_verdicts]
    if not verdicts or any(verdict in {"loss", "no_data"} for verdict in verdicts):
        return "loss"
    if any(verdict == "strict_win" for verdict in verdicts):
        return "strict_win"
    return "tie_for_best"


def _connected_components(edges: Sequence[tuple[str, str]]) -> list[list[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for left, right in edges:
        graph[left].add(right)
        graph[right].add(left)
    seen: set[str] = set()
    components: list[list[str]] = []
    for node in sorted(graph):
        if node in seen:
            continue
        stack = [node]
        component: list[str] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for nxt in sorted(graph[current]):
                if nxt in seen:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        components.append(sorted(component))
    return components


def build_hygiene_independence(
    *,
    sequence_records: Sequence[Mapping[str, Any]] | None = None,
    conditions: Sequence[str] = CONDITIONS,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    sequences = list(sequence_records) if sequence_records is not None else barrier2_rescore.load_sequences()
    rows, excluded, coverage_errors = barrier2_rescore.build_rows(
        sequences=sequences,
        conditions=list(conditions),
        results_root=results_root,
    )
    metric_names = [name for name, _ in HYG]
    series: dict[str, dict[str, float | None]] = {
        metric: {condition: _metric_value(rows, condition, metric) for condition in conditions}
        for metric in metric_names
    }
    pass_series = {condition: _pass_value(rows, condition) for condition in conditions}
    pairwise: dict[str, dict[str, dict[str, Any]]] = {}
    collinear_pairs: list[dict[str, Any]] = []
    for left in metric_names:
        pairwise[left] = {}
        for right in metric_names:
            result = _correlate(series[left], series[right])
            pairwise[left][right] = result
            if left < right and result["r"] is not None and abs(result["r"]) >= 0.95 and result["n"] >= 10:
                collinear_pairs.append({"left": left, "right": right, "r": result["r"], "n": result["n"]})
    pass_correlations = {metric: _correlate(series[metric], pass_series) for metric in metric_names}
    df_pass = pass_series["DF"]
    df_equal_to_pass = [
        metric
        for metric in metric_names
        if df_pass is not None and _metric_value(rows, "DF", metric) is not None and abs(_metric_value(rows, "DF", metric) - df_pass) <= EPSILON
    ]

    direction_by_metric = dict(HYG)
    metric_verdicts = {metric: _metric_verdict(rows, metric, direction_by_metric[metric]) for metric in metric_names}
    family_results: dict[str, Any] = {}
    for family, payload in FAMILIES.items():
        verdicts = [metric_verdicts[metric] for metric in payload["metrics"]]
        verdict = _family_verdict(verdicts)
        family_results[family] = {
            "metrics": payload["metrics"],
            "note": payload["note"],
            "metric_verdicts": verdicts,
            "df_family_verdict": verdict,
            "df_best_or_tied": verdict in {"strict_win", "tie_for_best"},
        }
    best_or_tied_count = sum(1 for item in family_results.values() if item["df_best_or_tied"])
    strict_count = sum(1 for item in family_results.values() if item["df_family_verdict"] == "strict_win")

    return {
        "metadata": {
            "conditions": list(conditions),
            "metric_order": metric_names,
            "correlation": "Pearson across conditions using pairwise non-null metric cells",
            "collinear_threshold_abs_r": 0.95,
            "family_count_rule": (
                "A family is counted best-or-tied only if DF is not worse than the best B0-B7 baseline "
                "on every metric in that family."
            ),
        },
        "pass_at_1": {
            condition: {
                "value": rows[condition]["pass_at_1_rate"],
                "numerator": rows[condition]["pass_at_1_count"],
                "denominator": rows[condition]["n3"],
            }
            for condition in conditions
        },
        "metric_values": {
            condition: {metric: _metric_value(rows, condition, metric) for metric in metric_names}
            for condition in conditions
        },
        "correlation_with_pass_at_1": pass_correlations,
        "pairwise_metric_correlation": pairwise,
        "collinear_pairs_abs_r_ge_0_95": collinear_pairs,
        "collinear_groups_abs_r_ge_0_95": _connected_components([(item["left"], item["right"]) for item in collinear_pairs]),
        "df_metrics_equal_to_df_pass_at_1": {
            "df_pass_at_1": df_pass,
            "metrics": df_equal_to_pass,
        },
        "metric_verdicts_vs_best_baseline": metric_verdicts,
        "independent_families": family_results,
        "df_independent_family_count": {
            "best_or_tied": best_or_tied_count,
            "strict_wins": strict_count,
            "denominator": len(FAMILIES),
            "losses": [
                family
                for family, item in family_results.items()
                if item["df_family_verdict"] == "loss"
            ],
        },
        "excluded": excluded,
        "coverage_errors": coverage_errors,
    }


def _fmt_int(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{int(value):,}"


def _fmt_float(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _fmt_tex_number(value: Any) -> str:
    return _fmt_int(value).replace(",", "{,}")


def _fmt_cost(value: Any) -> str:
    if value is None:
        return "n/a"
    return _fmt_tex_number(round(float(value)))


def render_cost_tex(analysis: Mapping[str, Any]) -> str:
    rows = analysis["conditions"]
    lines = [
        "% DreamForge seed-1 cost frontier. Generated by scripts/cost_analysis.py.",
        "\\begin{table}[t]",
        "\\centering",
        "\\scriptsize",
        "\\begin{tabular}{lrrrrrrr}",
        "\\toprule",
        "Condition & $n_{S3}$ & Pass@1 & Wake tokens & Sleep/Judge tokens & Total tokens & Judge calls & Tokens / Pass@1 success \\\\",
        "\\midrule",
    ]
    for condition in CONDITIONS:
        row = rows[condition]
        label = barrier2_rescore.CONDITION_LABELS.get(condition, condition)
        if row["partial_source_file_count"]:
            label += "$^\\dagger$"
        values = [
            label,
            str(row["n_S3"]),
            _fmt_float(row["pass_at_1_rate"]),
            _fmt_tex_number(row["WakeTokens"]),
            _fmt_tex_number(row["SleepJudgeTokens"]),
            _fmt_tex_number(row["TotalTokens"]),
            _fmt_tex_number(row["judge_call_count"]),
            _fmt_cost(row["CostPerPass1Success"]),
        ]
        lines.append(" & ".join(values) + r" \\")
    lines.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "\\caption{Cost frontier for the seed-1 container fold. Sleep/Judge tokens are manifest sleep tokens plus gate-reported judge tokens; the true offline-token share is Sleep/Judge tokens divided by Total tokens.}",
            "\\label{tab:cost-frontier}",
            "\\end{table}",
            "",
            "% SleepCostShare oddity: gate metrics report SleepCostShare=0.0 for DF slices even when JudgeTokens are nonzero.",
            "% True offline-token share uses SleepJudgeTokens / TotalTokens.",
            "% Pareto data block: condition pass_at_1 total_tokens",
            "\\begin{verbatim}",
            "condition pass_at_1 total_tokens",
        ]
    )
    for condition in CONDITIONS:
        row = rows[condition]
        lines.append(f"{condition} {_fmt_float(row['pass_at_1_rate'])} {row['TotalTokens']}")
    lines.extend(["\\end{verbatim}"])
    partials = [
        condition for condition, row in rows.items() if row["partial_source_file_count"]
    ]
    if partials:
        lines.append("")
        lines.append(
            "% Dagger note: partial source files mean run-level token totals are real source totals, "
            "but not cleanly allocatable to only the latest selected records."
        )
    return "\n".join(lines)


def _corr_cell(result: Mapping[str, Any]) -> str:
    r = result.get("r")
    if r is None:
        return "n/a"
    return f"{float(r):.3f}"


def render_hygiene_markdown(analysis: Mapping[str, Any]) -> str:
    metric_names = analysis["metadata"]["metric_order"]
    lines = [
        "# Hygiene Metric Independence",
        "",
        "Pearson correlations are computed across the 15 conditions with pairwise non-null cells. Values are diagnostic, not causal proof.",
        "",
        "## Correlation With Pass@1",
        "",
        "| Metric | r | n |",
        "|---|---:|---:|",
    ]
    for metric in metric_names:
        result = analysis["correlation_with_pass_at_1"][metric]
        lines.append(f"| {metric} | {_corr_cell(result)} | {result['n']} |")
    lines.extend(["", "## Pairwise Metric Correlation", ""])
    lines.append("| Metric | " + " | ".join(metric_names) + " |")
    lines.append("|---" + "|---:" * len(metric_names) + "|")
    for left in metric_names:
        cells = [_corr_cell(analysis["pairwise_metric_correlation"][left][right]) for right in metric_names]
        lines.append("| " + left + " | " + " | ".join(cells) + " |")
    equal = analysis["df_metrics_equal_to_df_pass_at_1"]
    lines.extend(
        [
            "",
            "## DF Pass@1 Equality",
            "",
            f"DF Pass@1 is {_fmt_float(equal['df_pass_at_1'])}. Metrics exactly equal to DF Pass@1: "
            + (", ".join(equal["metrics"]) if equal["metrics"] else "none")
            + ".",
            "",
            "## Collinearity",
            "",
        ]
    )
    pairs = analysis["collinear_pairs_abs_r_ge_0_95"]
    if pairs:
        lines.extend(["| Metric A | Metric B | r | n |", "|---|---|---:|---:|"])
        for item in pairs:
            lines.append(f"| {item['left']} | {item['right']} | {item['r']:.3f} | {item['n']} |")
    else:
        lines.append("No pair has |r| >= 0.95 with n >= 10.")
    lines.extend(["", "## Independent Families", ""])
    family_count = analysis["df_independent_family_count"]
    lines.append(
        f"DF is best-or-tied in {family_count['best_or_tied']}/{family_count['denominator']} independent families "
        f"({family_count['strict_wins']} strict, {family_count['best_or_tied'] - family_count['strict_wins']} tied). "
        "It loses the precision family."
    )
    lines.extend(["", "| Family | Metrics | DF family verdict | Note |", "|---|---|---|---|"])
    for family, payload in analysis["independent_families"].items():
        lines.append(
            f"| {family} | {', '.join(payload['metrics'])} | {payload['df_family_verdict']} | {payload['note']} |"
        )
    lines.extend(["", "## Metric Verdicts Vs Best B0-B7 Baseline", ""])
    lines.extend(["| Metric | Direction | DF | Best baseline | Verdict |", "|---|---:|---:|---:|---|"])
    for metric in metric_names:
        verdict = analysis["metric_verdicts_vs_best_baseline"][metric]
        lines.append(
            f"| {metric} | {verdict['direction']} | {_fmt_float(verdict.get('df_value'))} | "
            f"{_fmt_float(verdict.get('best_baseline_value'))} | {verdict['verdict']} |"
        )
    return "\n".join(lines) + "\n"


def write_outputs(output_dir: Path = OUTPUT_DIR) -> tuple[dict[str, Any], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sequences = barrier2_rescore.load_sequences()
    cost = build_cost_analysis(sequence_records=sequences)
    hygiene = build_hygiene_independence(sequence_records=sequences)
    (output_dir / "cost.json").write_text(json.dumps(cost, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output_dir / "cost.tex").write_text(render_cost_tex(cost) + "\n", encoding="utf-8")
    (output_dir / "hygiene_independence.json").write_text(
        json.dumps(hygiene, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "hygiene_independence.md").write_text(render_hygiene_markdown(hygiene), encoding="utf-8")
    return cost, hygiene


def main() -> int:
    cost, hygiene = write_outputs()
    df = cost["conditions"]["DF"]
    b5 = cost["conditions"]["B5"]
    family_count = hygiene["df_independent_family_count"]
    print("Wrote analysis/fold/cost.json")
    print("Wrote analysis/fold/cost.tex")
    print("Wrote analysis/fold/hygiene_independence.json")
    print("Wrote analysis/fold/hygiene_independence.md")
    print(
        "summary: "
        f"DF Pass@1={df['pass_at_1_rate']:.3f} "
        f"DF TotalTokens={df['TotalTokens']} "
        f"B5 TotalTokens={b5['TotalTokens']} "
        f"DF CostPerPass1Success={df['CostPerPass1Success']:.3f} "
        f"DF families best_or_tied={family_count['best_or_tied']}/{family_count['denominator']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
