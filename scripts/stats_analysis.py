#!/usr/bin/env python3
"""Deterministic multi-seed statistics for DreamBench-SWE v1.1 folds.

The script uses only the Python standard library. It reads validity-gated S3
records directly from result directories and computes per-seed and pooled
paired statistics over (seed, sequence_id) cells. DreamBench-SWE v1.0 folds are
not read or merged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "experiments" / "results"
OUTPUT_DIR = ROOT / "analysis" / "fold"
STATS_JSON_PATH = OUTPUT_DIR / "stats.json"
STATS_TEX_PATH = OUTPUT_DIR / "stats.tex"
STATS_NOTES_PATH = OUTPUT_DIR / "STATS-NOTES.md"

BENCHMARK_VERSION = "DreamBench-SWE v1.1"
BOOTSTRAP_B = 10_000
BOOTSTRAP_SEED = 20260703
WILSON_Z_95 = 1.959963984540054
ALPHA = 0.05
EXPECTED_SEEDS = (1, 2, 3)

BASELINES = ("B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B5-MEM0")
ABLATIONS = ("A0", "A2", "A4", "A5", "A6", "A11")
CONDITIONS = BASELINES + ("DF", "DF-strict") + ABLATIONS
KEY_COMPARISONS = (
    ("DF", "B5", "DF_vs_B5-Instance", "DF vs B5-Instance"),
    ("DF", "B5-MEM0", "DF_vs_B5-MEM0", "DF vs B5-MEM0"),
    ("DF", "A4", "DF_vs_A4_DF-lite", "DF vs A4/DF-lite"),
    ("DF", "DF-strict", "DF_vs_DF-strict", "DF vs DF-strict"),
)
CONDITION_LABELS = {
    "B0": "B0 No memory",
    "B1": "B1 Raw episodic",
    "B2": "B2 Vector traces",
    "B3": "B3 Reflection-only",
    "B4": "B4 Untyped summary",
    "B5": "B5-Instance offline substitute",
    "B6": "B6 Subtask memory",
    "B7": "B7 Task tracker",
    "B5-MEM0": "B5-MEM0 live Mem0",
    "DF": "DF DreamForge",
    "DF-strict": "DF-strict retrieval",
    "A0": "A0 Episodic-only",
    "A2": "A2 No contradiction repair",
    "A4": "A4/DF-lite No counterfactual replay",
    "A5": "A5 No stale suppression",
    "A6": "A6 No retrieval gate",
    "A11": "A11 Forced consolidation",
}

_CONTAINER_READ_CONTEXT = re.compile(
    r"\b(cat|sed|rg|grep|find|awk|head|tail|ls|stat)\b|open\(|read_text|test -[efr]|exec\\n|succeeded in|/bin/(?:ba)?sh -lc",
    re.IGNORECASE,
)
_CONTAINER_HIDDEN_CONTEXT = re.compile(
    r"experiments/env/(?:oracles|refsol|sequences\.jsonl)|experiments/secrets\.env|logs/codex|\.py-diff",
    re.IGNORECASE,
)
_CONTAINER_WORK_CONTEXT = re.compile(r"\bin /work\b|/work/", re.IGNORECASE)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stats = build_stats(
        results_root=args.results_root,
        output_dir=args.output_dir,
        conditions=parse_conditions(args.conditions),
        expected_seeds=args.expected_seeds,
    )
    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "stats.json").write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (args.output_dir / "stats.tex").write_text(render_tex(stats) + "\n", encoding="utf-8")
        (args.output_dir / "STATS-NOTES.md").write_text(render_notes(stats) + "\n", encoding="utf-8")
    print_stdout(stats, output_dir=args.output_dir, wrote=not args.no_write)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{BENCHMARK_VERSION} multi-seed paired stats.")
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--conditions", default=",".join(CONDITIONS))
    parser.add_argument("--expected-seeds", type=parse_int_csv, default=EXPECTED_SEEDS)
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args(argv)


def parse_int_csv(value: str) -> tuple[int, ...]:
    seeds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not seeds:
        raise argparse.ArgumentTypeError("expected at least one seed")
    return seeds


def parse_conditions(value: str) -> tuple[str, ...]:
    conditions = tuple(item.strip() for item in value.split(",") if item.strip())
    if not conditions:
        raise SystemExit("--conditions must include at least one condition")
    return conditions


def build_stats(
    *,
    results_root: Path = RESULTS_ROOT,
    output_dir: Path = OUTPUT_DIR,
    conditions: tuple[str, ...] = CONDITIONS,
    expected_seeds: tuple[int, ...] = EXPECTED_SEEDS,
) -> dict[str, Any]:
    records_by_condition, selection = load_condition_records(results_root=results_root, conditions=conditions)
    s3_by_condition = {
        condition: {
            (seed, sequence_id): record
            for (seed, sequence_id, session_index), record in records.items()
            if session_index == 3
        }
        for condition, records in records_by_condition.items()
    }
    condition_stats = {
        condition: condition_summary(condition, s3_by_condition.get(condition, {}), expected_seeds)
        for condition in conditions
    }
    key_comparisons = {
        key: paired_summary(left, right, label, s3_by_condition, expected_seeds)
        for left, right, key, label in KEY_COMPARISONS
        if left in conditions and right in conditions
    }
    corrections = multiple_comparison_corrections(key_comparisons)
    apply_corrections(key_comparisons, corrections)
    inter_seed = inter_seed_agreement(condition_stats, expected_seeds)
    return {
        "metadata": {
            "benchmark_version": BENCHMARK_VERSION,
            "warning": "DreamBench-SWE v1.1 records must not be merged with v1.0-era folds.",
            "analysis": "multi-seed Pass@1, paired McNemar tests, pooled bootstrap CIs, Holm/BH correction",
            "bootstrap_B": BOOTSTRAP_B,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "wilson_z": WILSON_Z_95,
            "alpha": ALPHA,
            "expected_seeds": list(expected_seeds),
            "results_root": str(results_root),
            "output_dir": str(output_dir),
            "paired_unit": "(seed, sequence_id)",
            "record_selection": [
                "read only result directories matching *-gpt-5.5-*/<COND>/results.json",
                "exclude result directories whose continuation gate failed",
                "exclude invalid infrastructure records: sleep_error, isolation_unavailable, task_exception:*",
                "exclude container records only when hidden-read evidence survives the container scan",
                "deduplicate by latest results.json mtime per (condition, seed, sequence_id, session_index)",
                "use session_index == 3 Pass@1 as the headline outcome",
            ],
        },
        "selection": selection,
        "conditions": condition_stats,
        "key_comparisons": key_comparisons,
        "multiple_comparison_corrections": corrections,
        "inter_seed_agreement": inter_seed,
    }


def load_condition_records(
    *,
    results_root: Path,
    conditions: tuple[str, ...],
) -> tuple[dict[str, dict[tuple[int, str, int], Mapping[str, Any]]], dict[str, Any]]:
    records_by_condition: dict[str, dict[tuple[int, str, int], tuple[tuple[float, str], Mapping[str, Any]]]] = {
        condition: {} for condition in conditions
    }
    selection: dict[str, Any] = {
        condition: {
            "candidate_result_files": 0,
            "used_result_files": [],
            "excluded_result_dirs": [],
            "excluded_records": [],
        }
        for condition in conditions
    }
    for condition in conditions:
        for condition_dir in sorted(results_root.glob(f"*-gpt-5.5-*/{condition}")):
            results_path = condition_dir / "results.json"
            if not results_path.exists():
                continue
            selection[condition]["candidate_result_files"] += 1
            gate_errors = gate_report_errors(condition_dir)
            if gate_errors:
                selection[condition]["excluded_result_dirs"].append(
                    {"path": str(results_path), "reasons": gate_errors}
                )
                continue
            try:
                payload = read_json(results_path)
            except Exception as exc:  # pragma: no cover - defensive report path
                selection[condition]["excluded_result_dirs"].append(
                    {"path": str(results_path), "reasons": [f"unreadable_results:{type(exc).__name__}"]}
                )
                continue
            mtime_key = (results_path.stat().st_mtime, str(results_path))
            records = payload.get("records") if isinstance(payload, Mapping) else []
            for record in records or []:
                if not isinstance(record, Mapping):
                    continue
                key = record_seed_key(record, payload)
                seed, sequence_id, session_index = key
                if not sequence_id or not session_index:
                    selection[condition]["excluded_records"].append(
                        {"path": str(results_path), "reason": "missing_sequence_or_session"}
                    )
                    continue
                errors = record_validity_errors(record)
                if errors:
                    selection[condition]["excluded_records"].append(
                        {
                            "path": str(results_path),
                            "seed": seed,
                            "sequence_id": sequence_id,
                            "session_index": session_index,
                            "reason": ",".join(errors),
                        }
                    )
                    continue
                if record_is_contaminated(record):
                    selection[condition]["excluded_records"].append(
                        {
                            "path": str(results_path),
                            "seed": seed,
                            "sequence_id": sequence_id,
                            "session_index": session_index,
                            "reason": "contaminated_container_hidden_read",
                        }
                    )
                    continue
                if key not in records_by_condition[condition] or mtime_key > records_by_condition[condition][key][0]:
                    annotated = dict(record)
                    annotated["_dreambench_seed"] = seed
                    annotated["_source_results_path"] = str(results_path)
                    annotated["_source_mtime"] = mtime_key[0]
                    annotated["_source_manifest"] = payload.get("manifest") if isinstance(payload, Mapping) else {}
                    records_by_condition[condition][key] = (mtime_key, annotated)
    clean_records = {
        condition: {key: value[1] for key, value in records.items()}
        for condition, records in records_by_condition.items()
    }
    for condition, records in clean_records.items():
        selection[condition]["used_result_files"] = sorted(
            {str(record.get("_source_results_path")) for record in records.values() if record.get("_source_results_path")}
        )
    return clean_records, selection


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def gate_report_errors(condition_dir: Path) -> list[str]:
    for candidate in (condition_dir / "gate_report.json", condition_dir.parent / "gate_report.json"):
        if not candidate.exists():
            continue
        try:
            gate = read_json(candidate)
        except Exception as exc:  # pragma: no cover - defensive report path
            return [f"unreadable_gate_report:{type(exc).__name__}"]
        errors: list[str] = []
        gates = gate.get("acceptance_gates") if isinstance(gate.get("acceptance_gates"), Mapping) else {}
        if "continuation_audit_valid" in gates and gates.get("continuation_audit_valid") is not True:
            errors.append("failing_gate:continuation_audit_valid")
        continuation = gate.get("continuation_audit") if isinstance(gate.get("continuation_audit"), Mapping) else {}
        if "valid" in continuation and continuation.get("valid") is not True:
            errors.append("failing_gate:continuation_audit.valid")
        return errors
    return []


def task_mapping(record: Mapping[str, Any]) -> Mapping[str, Any]:
    task = record.get("task")
    return task if isinstance(task, Mapping) else {}


def sequence_id(record: Mapping[str, Any]) -> str:
    task = task_mapping(record)
    return str(task.get("seq_id") or task.get("sequence_id") or record.get("seq_id") or record.get("sequence_id") or "")


def session_index(record: Mapping[str, Any]) -> int | None:
    task = task_mapping(record)
    value = task.get("session_index") or record.get("session_index")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def record_seed(record: Mapping[str, Any], payload: Mapping[str, Any] | None = None) -> int:
    task = task_mapping(record)
    manifest = payload.get("manifest") if isinstance(payload, Mapping) and isinstance(payload.get("manifest"), Mapping) else {}
    for source in (task, record, manifest, payload or {}):
        if not isinstance(source, Mapping):
            continue
        value = source.get("seed") or source.get("random_seed") or source.get("_dreambench_seed")
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 1


def record_seed_key(record: Mapping[str, Any], payload: Mapping[str, Any] | None = None) -> tuple[int, str, int]:
    return (record_seed(record, payload), sequence_id(record), int(session_index(record) or 0))


def record_validity_errors(record: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    sess = int(session_index(record) or 0)
    error_type = str(record.get("error_type") or "")
    if record.get("sleep_error"):
        errors.append("sleep_error")
    if str(record.get("isolation_mode") or "") == "unavailable":
        errors.append("isolation_mode_unavailable")
    if error_type == "isolation_unavailable" or error_type.startswith("task_exception:"):
        errors.append(error_type)
    if sess > 1:
        if record.get("started_from_previous_session") is not True:
            errors.append("invalid_continuation:started_from_previous_session")
        if not record.get("previous_session_end_commit"):
            errors.append("invalid_continuation:previous_session_end_commit")
        if record.get("forbidden_fresh_base_ref_used") is True:
            errors.append("invalid_continuation:forbidden_fresh_base_ref_used")
    return errors


def record_is_contaminated(record: Mapping[str, Any]) -> bool:
    is_container = str(record.get("isolation_mode") or "") == "container"
    if not is_container:
        return record.get("contaminated") is True
    evidence = record.get("contamination_evidence")
    if isinstance(evidence, list) and any(container_evidence_indicates_hidden_read(item) for item in evidence if isinstance(item, Mapping)):
        return True
    text = json.dumps(record, sort_keys=True, default=str)
    return bool(
        _CONTAINER_HIDDEN_CONTEXT.search(text)
        and _CONTAINER_READ_CONTEXT.search(text)
        and _CONTAINER_WORK_CONTEXT.search(text)
    )


def container_evidence_indicates_hidden_read(item: Mapping[str, Any]) -> bool:
    evidence_type = str(item.get("type") or "")
    match = str(item.get("match") or "")
    excerpt = str(item.get("excerpt") or "")
    text = f"{match}\n{excerpt}"
    has_hidden_context = bool(_CONTAINER_HIDDEN_CONTEXT.search(text))
    has_read_context = bool(_CONTAINER_READ_CONTEXT.search(text))
    has_container_work_context = bool(_CONTAINER_WORK_CONTEXT.search(text))
    if evidence_type == "hidden_path":
        return has_hidden_context and has_read_context and has_container_work_context
    if evidence_type == "hidden_marker":
        return item.get("memory_sourced") is not True and has_hidden_context and has_read_context and has_container_work_context
    return False


def condition_summary(
    condition: str,
    records_by_seed_sequence: Mapping[tuple[int, str], Mapping[str, Any]],
    expected_seeds: tuple[int, ...],
) -> dict[str, Any]:
    per_seed = {
        str(seed): outcome_summary(
            [record for (record_seed_value, _), record in records_by_seed_sequence.items() if record_seed_value == seed]
        )
        for seed in expected_seeds
    }
    pooled = outcome_summary(list(records_by_seed_sequence.values()))
    return {
        "label": CONDITION_LABELS.get(condition, condition),
        "per_seed": per_seed,
        "pooled": pooled,
        "sequence_cells": {
            format_seed_sequence(seed_sequence): {
                "pass_at_1": bool(record.get("pass_at_1")),
                "final_passed": bool(record.get("final_passed")),
                "source_results_path": record.get("_source_results_path"),
            }
            for seed_sequence, record in sorted(records_by_seed_sequence.items())
        },
    }


def outcome_summary(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    outcomes = [pass_at_1(record) for record in records]
    final_outcomes = [final_passed(record) for record in records]
    n = len(records)
    successes = sum(outcomes)
    final_successes = sum(final_outcomes)
    return {
        "successes": successes,
        "n": n,
        "rate": safe_div(successes, n),
        "bootstrap_ci_95": bootstrap_mean_ci(outcomes, "condition:pass_at_1:" + ",".join(format_record_id(record) for record in records)),
        "wilson_ci_95": wilson_ci(successes, n),
        "final_passed": {
            "successes": final_successes,
            "n": n,
            "rate": safe_div(final_successes, n),
            "wilson_ci_95": wilson_ci(final_successes, n),
        },
    }


def paired_summary(
    left_condition: str,
    right_condition: str,
    label: str,
    s3_by_condition: Mapping[str, Mapping[tuple[int, str], Mapping[str, Any]]],
    expected_seeds: tuple[int, ...],
) -> dict[str, Any]:
    left = s3_by_condition.get(left_condition, {})
    right = s3_by_condition.get(right_condition, {})
    pooled = paired_for_keys(left_condition, right_condition, left, right, sorted(set(left) & set(right)))
    per_seed = {
        str(seed): paired_for_keys(
            left_condition,
            right_condition,
            left,
            right,
            sorted(key for key in set(left) & set(right) if key[0] == seed),
        )
        for seed in expected_seeds
    }
    return {
        "label": label,
        "left_condition": left_condition,
        "right_condition": right_condition,
        "per_seed": per_seed,
        "pooled": pooled,
        f"missing_{left_condition}_cells": [format_seed_sequence(key) for key in sorted(set(right) - set(left))],
        f"missing_{right_condition}_cells": [format_seed_sequence(key) for key in sorted(set(left) - set(right))],
    }


def paired_for_keys(
    left_condition: str,
    right_condition: str,
    left: Mapping[tuple[int, str], Mapping[str, Any]],
    right: Mapping[tuple[int, str], Mapping[str, Any]],
    paired_keys: list[tuple[int, str]],
) -> dict[str, Any]:
    diffs = [pass_at_1(left[key]) - pass_at_1(right[key]) for key in paired_keys]
    left_only = [key for key in paired_keys if pass_at_1(left[key]) == 1 and pass_at_1(right[key]) == 0]
    right_only = [key for key in paired_keys if pass_at_1(left[key]) == 0 and pass_at_1(right[key]) == 1]
    both_pass = [key for key in paired_keys if pass_at_1(left[key]) == 1 and pass_at_1(right[key]) == 1]
    both_fail = [key for key in paired_keys if pass_at_1(left[key]) == 0 and pass_at_1(right[key]) == 0]
    left_successes = sum(pass_at_1(left[key]) for key in paired_keys)
    right_successes = sum(pass_at_1(right[key]) for key in paired_keys)
    n = len(paired_keys)
    left_only_n = len(left_only)
    right_only_n = len(right_only)
    p_value = mcnemar_exact_p(left_only_n, right_only_n)
    diff = safe_div(sum(diffs), n)
    return {
        "n_paired": n,
        "left_successes": left_successes,
        "right_successes": right_successes,
        "left_rate": safe_div(left_successes, n),
        "right_rate": safe_div(right_successes, n),
        "diff": diff,
        "diff_bootstrap_ci_95": bootstrap_mean_ci(diffs, f"{left_condition}_vs_{right_condition}:pooled_diff:{n}:{left_only_n}:{right_only_n}"),
        "mcnemar_exact_p": p_value,
        "significant_alpha_0_05_uncorrected": bool(p_value is not None and p_value < ALPHA),
        "effect_sizes": effect_sizes(diff, left_only_n, right_only_n, n),
        "discordant": {
            f"{left_condition}_pass_{right_condition}_fail": left_only_n,
            f"{right_condition}_pass_{left_condition}_fail": right_only_n,
            f"{left_condition}_pass_{right_condition}_fail_cells": [format_seed_sequence(key) for key in left_only],
            f"{right_condition}_pass_{left_condition}_fail_cells": [format_seed_sequence(key) for key in right_only],
        },
        "paired_2x2": {
            "both_pass": len(both_pass),
            f"{left_condition}_only": left_only_n,
            f"{right_condition}_only": right_only_n,
            "both_fail": len(both_fail),
        },
    }


def effect_sizes(diff: float | None, left_only: int, right_only: int, n: int) -> dict[str, Any]:
    discordant = left_only + right_only
    odds_ratio = None
    if discordant > 0:
        odds_ratio = (left_only + 0.5) / (right_only + 0.5)
    return {
        "paired_risk_difference": diff,
        "matched_pairs_odds_ratio_haldane": odds_ratio,
        "discordant_rate": safe_div(discordant, n),
        "df_win_rate_among_discordants": safe_div(left_only, discordant),
    }


def multiple_comparison_corrections(key_comparisons: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    p_values = {
        key: comparison["pooled"]["mcnemar_exact_p"]
        for key, comparison in key_comparisons.items()
        if isinstance(comparison.get("pooled", {}).get("mcnemar_exact_p"), (int, float))
    }
    return {
        "family": "planned DF-vs-X pooled McNemar tests",
        "alpha": ALPHA,
        "p_values": p_values,
        "holm": holm_adjust(p_values),
        "benjamini_hochberg": benjamini_hochberg_adjust(p_values),
    }


def apply_corrections(key_comparisons: dict[str, Any], corrections: Mapping[str, Any]) -> None:
    holm = corrections.get("holm") if isinstance(corrections.get("holm"), Mapping) else {}
    bh = corrections.get("benjamini_hochberg") if isinstance(corrections.get("benjamini_hochberg"), Mapping) else {}
    for key, comparison in key_comparisons.items():
        pooled = comparison.get("pooled")
        if not isinstance(pooled, dict):
            continue
        pooled["holm_adjusted_p"] = (holm.get(key) or {}).get("adjusted_p") if isinstance(holm.get(key), Mapping) else None
        pooled["holm_reject_0_05"] = bool((holm.get(key) or {}).get("reject_0_05")) if isinstance(holm.get(key), Mapping) else False
        pooled["bh_adjusted_p"] = (bh.get(key) or {}).get("adjusted_p") if isinstance(bh.get(key), Mapping) else None
        pooled["bh_reject_fdr_0_05"] = bool((bh.get(key) or {}).get("reject_fdr_0_05")) if isinstance(bh.get(key), Mapping) else False


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    out: dict[str, dict[str, Any]] = {}
    running_max = 0.0
    for index, (key, p_value) in enumerate(ordered):
        adjusted = min(1.0, (m - index) * float(p_value))
        running_max = max(running_max, adjusted)
        out[key] = {"raw_p": p_value, "adjusted_p": running_max, "reject_0_05": running_max <= ALPHA}
    return out


def benjamini_hochberg_adjust(p_values: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    adjusted_by_key: dict[str, float] = {}
    running_min = 1.0
    for reverse_index, (key, p_value) in enumerate(reversed(ordered), start=1):
        rank = m - reverse_index + 1
        adjusted = min(1.0, float(p_value) * m / rank)
        running_min = min(running_min, adjusted)
        adjusted_by_key[key] = running_min
    return {
        key: {"raw_p": p_values[key], "adjusted_p": adjusted, "reject_fdr_0_05": adjusted <= ALPHA}
        for key, adjusted in adjusted_by_key.items()
    }


def inter_seed_agreement(condition_stats: Mapping[str, Mapping[str, Any]], expected_seeds: tuple[int, ...]) -> dict[str, Any]:
    rates_by_seed: dict[int, dict[str, float]] = {}
    for seed in expected_seeds:
        seed_rates: dict[str, float] = {}
        for condition, summary in condition_stats.items():
            per_seed = summary.get("per_seed", {})
            item = per_seed.get(str(seed)) if isinstance(per_seed, Mapping) else None
            if isinstance(item, Mapping) and isinstance(item.get("rate"), (int, float)):
                seed_rates[condition] = float(item["rate"])
        rates_by_seed[seed] = seed_rates
    ranks_by_seed = {seed: average_descending_ranks(rates) for seed, rates in rates_by_seed.items()}
    pairwise = []
    seeds = list(expected_seeds)
    for idx, left_seed in enumerate(seeds):
        for right_seed in seeds[idx + 1 :]:
            common = sorted(set(ranks_by_seed[left_seed]) & set(ranks_by_seed[right_seed]))
            rho = spearman_from_ranks(
                [ranks_by_seed[left_seed][condition] for condition in common],
                [ranks_by_seed[right_seed][condition] for condition in common],
            )
            pairwise.append({"seeds": [left_seed, right_seed], "n_conditions": len(common), "spearman_rho": rho})
    rank_variance = {}
    rate_variance = {}
    for condition in condition_stats:
        ranks = [ranks_by_seed[seed][condition] for seed in expected_seeds if condition in ranks_by_seed[seed]]
        rates = [rates_by_seed[seed][condition] for seed in expected_seeds if condition in rates_by_seed[seed]]
        rank_variance[condition] = variance(ranks)
        rate_variance[condition] = variance(rates)
    valid_rhos = [item["spearman_rho"] for item in pairwise if isinstance(item.get("spearman_rho"), (int, float))]
    return {
        "rates_by_seed": {str(seed): rates_by_seed[seed] for seed in expected_seeds},
        "ranks_by_seed": {str(seed): ranks_by_seed[seed] for seed in expected_seeds},
        "pairwise_spearman": pairwise,
        "mean_pairwise_spearman": safe_div(sum(valid_rhos), len(valid_rhos)) if valid_rhos else None,
        "rank_variance_by_condition": rank_variance,
        "pass_at_1_rate_variance_by_condition": rate_variance,
    }


def average_descending_ranks(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        rate = ordered[index][1]
        end = index + 1
        while end < len(ordered) and ordered[end][1] == rate:
            end += 1
        avg_rank = (index + 1 + end) / 2.0
        for condition, _ in ordered[index:end]:
            ranks[condition] = avg_rank
        index = end
    return ranks


def spearman_from_ranks(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_ss = sum((a - left_mean) ** 2 for a in left)
    right_ss = sum((b - right_mean) ** 2 for b in right)
    if left_ss == 0.0 or right_ss == 0.0:
        return None
    return numerator / math.sqrt(left_ss * right_ss)


def variance(values: list[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def pass_at_1(record: Mapping[str, Any]) -> int:
    return 1 if bool(record.get("pass_at_1")) else 0


def final_passed(record: Mapping[str, Any]) -> int:
    return 1 if bool(record.get("final_passed")) else 0


def bootstrap_mean_ci(values: list[int | float], label: str) -> list[float | None]:
    n = len(values)
    if n == 0:
        return [None, None]
    rng = random.Random(derived_seed(label))
    means = []
    for _ in range(BOOTSTRAP_B):
        total = 0.0
        for _ in range(n):
            total += float(values[rng.randrange(n)])
        means.append(total / n)
    means.sort()
    return [percentile(means, 0.025), percentile(means, 0.975)]


def derived_seed(label: str) -> int:
    digest = hashlib.sha256(f"{BOOTSTRAP_SEED}:{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires non-empty values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    weight = pos - lo
    return sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight


def wilson_ci(successes: int, n: int) -> list[float | None]:
    if n == 0:
        return [None, None]
    p = successes / n
    z2 = WILSON_Z_95 * WILSON_Z_95
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = WILSON_Z_95 * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n))) / denom
    return [max(0.0, center - half), min(1.0, center + half)]


def mcnemar_exact_p(left_only: int, right_only: int) -> float:
    discordant = left_only + right_only
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, i) for i in range(min(left_only, right_only) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def safe_div(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def format_seed_sequence(seed_sequence: tuple[int, str]) -> str:
    return f"s{seed_sequence[0]}:{seed_sequence[1]}"


def format_record_id(record: Mapping[str, Any]) -> str:
    return f"s{record_seed(record)}:{sequence_id(record)}:{session_index(record) or 0}:{int(bool(record.get('pass_at_1')))}"


def render_tex(stats: Mapping[str, Any]) -> str:
    lines = [
        "% DreamBench-SWE v1.1 multi-seed statistics.",
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lrrrr}",
        "\\toprule",
        "Comparison & $n$ & Diff & McNemar $p$ & BH FDR 0.05 \\\\",
        "\\midrule",
    ]
    for key, item in stats["key_comparisons"].items():
        pooled = item["pooled"]
        lines.append(
            f"{latex_escape(item['label'])} & {pooled['n_paired']} & {fmt_signed(pooled['diff'])} & "
            f"{fmt_p(pooled['mcnemar_exact_p'])} & {'yes' if pooled['bh_reject_fdr_0_05'] else 'no'} \\\\"
        )
    lines.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "\\caption{DreamBench-SWE v1.1 planned pooled paired tests over seed/sequence cells. Holm and Benjamini--Hochberg corrections are applied across the DF-vs-X family.}",
            "\\label{tab:v11-key-paired-tests}",
            "\\end{table}",
        ]
    )
    return "\n".join(lines)


def render_notes(stats: Mapping[str, Any]) -> str:
    comparisons = stats["key_comparisons"]
    corrections = stats["multiple_comparison_corrections"]
    inter_seed = stats["inter_seed_agreement"]
    lines = [
        "# DreamBench-SWE v1.1 Multi-Seed Statistics Notes",
        "",
        "## Scope",
        "",
        "- This analysis is for DreamBench-SWE v1.1 only. Do not merge v1.0-era seed-1 folds with these records.",
        "- The paired unit is `(seed, sequence_id)`, so complete seeds 1/2/3 produce up to 66 paired cells.",
        "- B5-Instance (`B5`) and B5-MEM0 (`B5-MEM0`) are separate conditions in every table.",
        "",
        "## Planned DF-vs-X Family",
        "",
    ]
    for key, item in comparisons.items():
        pooled = item["pooled"]
        lines.append(
            f"- {item['label']}: n={pooled['n_paired']}, diff={fmt_signed(pooled['diff'])}, "
            f"95% bootstrap CI={fmt_ci(pooled['diff_bootstrap_ci_95'])}, McNemar p={fmt_p(pooled['mcnemar_exact_p'])}, "
            f"Holm p={fmt_p(pooled.get('holm_adjusted_p'))}, BH p={fmt_p(pooled.get('bh_adjusted_p'))}."
        )
    lines.extend(
        [
            "",
            "## Multiple Comparisons",
            "",
            "- Holm and Benjamini-Hochberg corrections are applied across the planned DF-vs-X pooled McNemar tests.",
            "- BH/FDR survivors at 0.05: "
            + ", ".join(key for key, item in corrections["benjamini_hochberg"].items() if item["reject_fdr_0_05"])
            if any(item["reject_fdr_0_05"] for item in corrections["benjamini_hochberg"].values())
            else "- BH/FDR survivors at 0.05: none",
            "",
            "## Inter-Seed Agreement",
            "",
            f"- Mean pairwise Spearman rank correlation: {fmt_rate(inter_seed['mean_pairwise_spearman'])}.",
            "- Inspect `stats.json` for per-condition rank variance and Pass@1-rate variance across seeds.",
        ]
    )
    return "\n".join(lines)


def print_stdout(stats: Mapping[str, Any], *, output_dir: Path, wrote: bool) -> None:
    print(f"=== {BENCHMARK_VERSION} stats analysis ===")
    print(f"bootstrap_B={BOOTSTRAP_B} bootstrap_seed={BOOTSTRAP_SEED}")
    print("paired_unit=(seed, sequence_id); S3 Pass@1; B5-Instance and B5-MEM0 separated")
    print("")
    print("=== Pass@1 by condition: per seed + pooled ===")
    print(f"{'cond':<10}{'s1':>14}{'s2':>14}{'s3':>14}{'pooled':>14}")
    for condition, item in stats["conditions"].items():
        per_seed = item["per_seed"]
        print(
            f"{condition:<10}"
            f"{fmt_count_rate(per_seed.get('1')):>14}"
            f"{fmt_count_rate(per_seed.get('2')):>14}"
            f"{fmt_count_rate(per_seed.get('3')):>14}"
            f"{fmt_count_rate(item['pooled']):>14}"
        )
    print("")
    print("=== Planned DF-vs-X comparisons: pooled paired McNemar + correction ===")
    print(f"{'comparison':<22}{'n':>5}{'diff':>9}{'boot95':>19}{'p':>10}{'holm':>10}{'BH':>10}{'FDR05':>8}")
    for key, item in stats["key_comparisons"].items():
        pooled = item["pooled"]
        print(
            f"{item['label']:<22}{pooled['n_paired']:>5}{fmt_signed(pooled['diff']):>9}"
            f"{fmt_ci(pooled['diff_bootstrap_ci_95']):>19}"
            f"{fmt_p(pooled['mcnemar_exact_p']):>10}"
            f"{fmt_p(pooled.get('holm_adjusted_p')):>10}"
            f"{fmt_p(pooled.get('bh_adjusted_p')):>10}"
            f"{('yes' if pooled.get('bh_reject_fdr_0_05') else 'no'):>8}"
        )
    print("")
    print("=== Per-seed McNemar p-values for planned comparisons ===")
    print(f"{'comparison':<22}{'s1':>10}{'s2':>10}{'s3':>10}")
    for key, item in stats["key_comparisons"].items():
        per_seed = item["per_seed"]
        print(
            f"{item['label']:<22}"
            f"{fmt_p((per_seed.get('1') or {}).get('mcnemar_exact_p')):>10}"
            f"{fmt_p((per_seed.get('2') or {}).get('mcnemar_exact_p')):>10}"
            f"{fmt_p((per_seed.get('3') or {}).get('mcnemar_exact_p')):>10}"
        )
    print("")
    print("=== Inter-seed rank agreement ===")
    for item in stats["inter_seed_agreement"]["pairwise_spearman"]:
        print(f"s{item['seeds'][0]} vs s{item['seeds'][1]} rho={fmt_rate(item['spearman_rho'])} n={item['n_conditions']}")
    print(f"mean_pairwise_spearman={fmt_rate(stats['inter_seed_agreement']['mean_pairwise_spearman'])}")
    if wrote:
        print(f"wrote {output_dir / 'stats.json'}")
        print(f"wrote {output_dir / 'stats.tex'}")
        print(f"wrote {output_dir / 'STATS-NOTES.md'}")
    else:
        print("no_write=yes")


def fmt_rate(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.3f}"
    return "n/a"


def fmt_signed(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):+.3f}"
    return "n/a"


def fmt_ci(value: Any) -> str:
    if (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        return f"[{float(value[0]):.3f}, {float(value[1]):.3f}]"
    return "[n/a, n/a]"


def fmt_p(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    if value < 0.001:
        return "<0.001"
    return f"{float(value):.4f}"


def fmt_count_rate(item: Any) -> str:
    if not isinstance(item, Mapping):
        return "n/a"
    n = int(item.get("n") or 0)
    successes = int(item.get("successes") or 0)
    rate = item.get("rate")
    if not isinstance(rate, (int, float)):
        return f"{successes}/{n}"
    return f"{successes}/{n} ({float(rate):.2f})"


def latex_escape(value: str) -> str:
    return (
        value.replace("\\", "\\textbackslash{}")
        .replace("_", "\\_")
        .replace("%", "\\%")
        .replace("&", "\\&")
    )


if __name__ == "__main__":
    raise SystemExit(main())
