#!/usr/bin/env python3
"""Transparent confirmatory fold analysis for DreamBench-SWE.

This script intentionally reads condition result directories directly and keeps
the newest record per (condition, seed, oracle_id) by the timestamp embedded in
the results directory name. It does not import or depend on barrier2_rescore.py.
"""

import argparse
import glob
import json
import math
import os
import re
from collections import defaultdict


CONDITION_ORDER = (
    "B0",
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "B6",
    "B7",
    "DF",
    "DF-strict",
    "DF-hybrid",
    "DF-raw-only",
    "DF-strict-hybrid",
    "A0",
    "A2",
    "A4",
    "A5",
    "A6",
    "A11",
    "B5-MEM0",
    "B5-MEM0-LIT",
)
ABLATION_LADDER = ("B5", "DF", "DF-raw-only", "DF-hybrid")
MCNEMAR_FAMILY = (
    ("DF-hybrid", "B5"),
    ("DF-hybrid", "DF"),
    ("DF-raw-only", "B5"),
    ("DF", "B5"),
    ("DF", "B3"),
)
SUPPLEMENTAL_MCNEMAR_COMPARISONS = (
    ("DF-hybrid", "B5-MEM0"),
    ("DF-hybrid", "B5-MEM0-LIT"),
)
HYGIENE_METRICS = (
    "UsefulMemoryPrecision",
    "RepeatedErrorRate",
    "RegressionAfterUpdate",
    "ScopeAccuracy",
    "ContradictionRepairAccuracy",
    "HumanFeedbackUseAccuracy",
    "TransferScore",
    "HarmfulMemoryRate",
)
HYGIENE_COUNTS = ("contaminated", "retrieved_memories", "sleep_writes")
VALIDITY_ERROR_SUBSTRINGS = (
    "task_exception",
    "isolation_unavailable",
    "codex_exec_failed",
)
TIMESTAMP_RE = re.compile(r"(?:^|[\\/])results[\\/](\d{8}T\d{6}Z)-SLICE-")
RUN_DIR_TIMESTAMP_RE = re.compile(r"^(\d{8}T\d{6}Z)-SLICE-")
SESSION_SUFFIX_RE = re.compile(r"-s0*\d+$", re.IGNORECASE)
SESSION_NUMBER_RE = re.compile(r"-s0*(\d+)$", re.IGNORECASE)


def main(argv=None):
    args = parse_args(argv)
    dreamforge_root = os.path.abspath(
        os.environ.get("DREAMBENCH_ROOT") or os.environ.get("DREAMFORGE_ROOT") or os.getcwd()
    )
    results_root = os.path.abspath(args.results_root or os.path.join(dreamforge_root, "experiments", "results"))
    report_path = os.path.join(dreamforge_root, "analysis", "investigation-evidence", "CONFIRMATORY-FOLD.md")
    json_path = os.path.join(dreamforge_root, "analysis", "fold", "confirmatory.json")

    analysis = build_analysis(results_root)
    analysis["metadata"]["dreamforge_root"] = dreamforge_root
    analysis["metadata"]["report_path"] = report_path
    analysis["metadata"]["json_path"] = json_path

    report = render_report(analysis)
    print(report)

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    write_text(report_path, report + "\n")
    write_text(json_path, json.dumps(analysis, indent=2, sort_keys=True) + "\n")
    print("")
    print("Wrote %s" % display_path(report_path))
    print("Wrote %s" % display_path(json_path))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Analyze DreamBench-SWE confirmatory multi-seed result directories."
    )
    parser.add_argument(
        "--results-root",
        default=None,
        help=(
            "Directory containing <TS>-SLICE-*/<CONDITION>/results.json. "
            "Default: $DREAMBENCH_ROOT/experiments/results, $DREAMFORGE_ROOT/experiments/results, "
            "or ./experiments/results."
        ),
    )
    return parser.parse_args(argv)


def build_analysis(results_root):
    result_files = discover_result_files(results_root)
    records, files_by_path, warnings = load_and_dedup(result_files)
    summaries = summarize_conditions(records, files_by_path)
    present_conditions = [condition for condition in CONDITION_ORDER if condition in summaries]
    mcnemar = build_mcnemar_family(summaries)
    apply_holm_to_mcnemar(mcnemar)
    analysis = {
        "metadata": {
            "analysis": "DreamBench-SWE confirmatory fold",
            "results_root": results_root,
            "result_files_discovered": len(result_files),
            "dedup": "newest record per (condition, seed, oracle_id) by results/<YYYYMMDDTHHMMSSZ>-SLICE-* timestamp",
            "validity_gate_excludes_error_type_substrings": list(VALIDITY_ERROR_SUBSTRINGS),
            "s3_definition": "oracle_id endswith '-s3' OR ordinal == 3",
            "warmup_definition": "non-S3 records (oracle_id not ending '-s3' and ordinal != 3)",
            "rate_field": "pass_at_1",
            "hygiene_aggregation": "metric means and count sums over unique result files that contributed at least one deduped record",
            "conditions_order": list(CONDITION_ORDER),
            "warnings": warnings,
        },
        "conditions_present": present_conditions,
        "conditions": summaries,
        "ablation_ladder": build_ablation_ladder(summaries),
        "mcnemar_family": mcnemar,
        "supplemental_mcnemar": build_supplemental_mcnemar(summaries),
        "falsifier_checks": build_falsifier_checks(summaries),
    }
    return analysis


def discover_result_files(results_root):
    pattern = os.path.join(results_root, "*-SLICE-*", "*", "results.json")
    return sorted(glob.glob(pattern))


def load_and_dedup(result_files):
    dedup = {}
    files_by_path = {}
    warnings = []

    for path in result_files:
        condition = os.path.basename(os.path.dirname(path))
        timestamp = timestamp_from_path(path)
        if timestamp is None:
            warnings.append("Skipped %s: missing results/<timestamp>-SLICE-* timestamp" % path)
            continue
        try:
            payload = read_json(path)
        except Exception as exc:
            warnings.append("Skipped %s: could not read JSON (%s)" % (path, type(exc).__name__))
            continue
        if not isinstance(payload, dict):
            warnings.append("Skipped %s: top-level JSON is not an object" % path)
            continue

        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            manifest = {}
        seed = coerce_int(manifest.get("seed"))
        if seed is None:
            warnings.append("Skipped %s: manifest.seed missing or not an int" % path)
            continue

        records = payload.get("records")
        if not isinstance(records, list):
            warnings.append("Skipped %s: records is not a list" % path)
            continue

        files_by_path[path] = {
            "path": path,
            "condition": condition,
            "seed": seed,
            "timestamp": timestamp,
            "metrics": payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
            "counts": payload.get("counts") if isinstance(payload.get("counts"), dict) else {},
            "record_count": len(records),
        }

        for index, record in enumerate(records):
            if not isinstance(record, dict):
                warnings.append("Skipped record %s[%d]: record is not an object" % (path, index))
                continue
            oracle_id = str(record.get("oracle_id") or "")
            if not oracle_id:
                warnings.append("Skipped record %s[%d]: oracle_id missing" % (path, index))
                continue
            key = (condition, seed, oracle_id)
            sort_key = (timestamp, path, index)
            annotated = dict(record)
            annotated["_condition"] = condition
            annotated["_seed"] = seed
            annotated["_oracle_id"] = oracle_id
            annotated["_ordinal"] = record_ordinal(record)
            annotated["_sequence_id"] = sequence_id(record)
            annotated["_source_path"] = path
            annotated["_timestamp"] = timestamp
            annotated["_valid"] = not invalid_reason(record)
            annotated["_invalid_reason"] = invalid_reason(record)
            if key not in dedup or sort_key > dedup[key][0]:
                dedup[key] = (sort_key, annotated)

    return [item[1] for item in sorted(dedup.values(), key=lambda item: item[0])], files_by_path, warnings


def summarize_conditions(records, files_by_path):
    by_condition = defaultdict(list)
    for record in records:
        by_condition[record["_condition"]].append(record)

    summaries = {}
    for condition in sorted(by_condition):
        condition_records = by_condition[condition]
        valid_records = [record for record in condition_records if record["_valid"]]
        invalid_records = [record for record in condition_records if not record["_valid"]]
        s3_records = [record for record in valid_records if is_s3(record)]
        warmup_records = [record for record in valid_records if is_warmup(record)]
        per_seed = {}
        for seed in sorted({record["_seed"] for record in s3_records}):
            seed_records = [record for record in s3_records if record["_seed"] == seed]
            per_seed[str(seed)] = outcome_summary(seed_records)

        source_paths = sorted({record["_source_path"] for record in condition_records})
        hygiene = hygiene_summary([files_by_path[path] for path in source_paths if path in files_by_path])
        s3_cells = {}
        for record in s3_records:
            s3_cells[cell_key(record)] = record_cell(record)

        summaries[condition] = {
            "records_deduped": len(condition_records),
            "valid_records": len(valid_records),
            "excluded_records": len(invalid_records),
            "excluded_by_reason": count_by_reason(invalid_records),
            "source_files": source_paths,
            "source_file_count": len(source_paths),
            "per_seed_s3": per_seed,
            "pooled_s3": outcome_summary(s3_records),
            "warmup": outcome_summary(warmup_records),
            "hygiene": hygiene,
            "s3_cells": s3_cells,
        }
        if condition in ("DF-hybrid", "DF-raw-only"):
            summaries[condition]["capsule_cr_check"] = capsule_cr_summary(s3_records)
    return summaries


def outcome_summary(records):
    n = len(records)
    passed_count = sum(1 for record in records if record_passed(record))
    return {
        "passed": passed_count,
        "n": n,
        "rate": safe_div(passed_count, n),
    }


def hygiene_summary(files):
    metric_values = defaultdict(list)
    count_sums = defaultdict(int)
    for item in files:
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        counts = item.get("counts") if isinstance(item.get("counts"), dict) else {}
        for metric in HYGIENE_METRICS + ("AdmittedMemoryTokensPerTask",):
            value = coerce_float(metrics.get(metric))
            if value is not None:
                metric_values[metric].append(value)
        for name in HYGIENE_COUNTS:
            value = coerce_int(counts.get(name))
            if value is not None:
                count_sums[name] += value
    metrics_out = {}
    for metric in HYGIENE_METRICS:
        metrics_out[metric] = average(metric_values.get(metric, []))
    return {
        "source_file_count": len(files),
        "metrics_mean": metrics_out,
        "counts_sum": {name: count_sums.get(name, 0) for name in HYGIENE_COUNTS},
        "AdmTok/task": average(metric_values.get("AdmittedMemoryTokensPerTask", [])),
    }


def capsule_cr_summary(s3_records):
    n = len(s3_records)
    raw_capsule = sum(1 for record in s3_records if has_raw_evidence_capsule(record))
    contradiction = sum(1 for record in s3_records if has_contradiction_item(record))
    return {
        "s3_n": n,
        "raw_evidence_capsule_count": raw_capsule,
        "raw_evidence_capsule_fraction": safe_div(raw_capsule, n),
        "contradiction_item_count": contradiction,
        "contradiction_item_fraction": safe_div(contradiction, n),
    }


def build_ablation_ladder(summaries):
    ladder = {}
    for condition in ABLATION_LADDER:
        if condition not in summaries:
            ladder[condition] = None
            continue
        summary = summaries[condition]
        ladder[condition] = {
            "pooled_s3": summary["pooled_s3"],
            "per_seed_s3": summary["per_seed_s3"],
        }
    return ladder


def build_mcnemar_family(summaries):
    comparisons = []
    for first, second in MCNEMAR_FAMILY:
        comparisons.append(mcnemar_comparison(first, second, summaries))
    return {
        "family": ["%s vs %s" % (first, second) for first, second in MCNEMAR_FAMILY],
        "comparisons": comparisons,
    }


def build_supplemental_mcnemar(summaries):
    comparisons = []
    for first, second in SUPPLEMENTAL_MCNEMAR_COMPARISONS:
        if first in summaries or second in summaries:
            comparisons.append(mcnemar_comparison(first, second, summaries))
    return {
        "comparisons": comparisons,
        "note": (
            "Supplemental comparisons are not part of the pre-registered Holm-corrected "
            "McNemar family and do not change its adjusted p-values."
        ),
    }


def mcnemar_comparison(first, second, summaries):
    first_cells = s3_cell_outcomes(summaries.get(first))
    second_cells = s3_cell_outcomes(summaries.get(second))
    paired_keys = sorted(set(first_cells) & set(second_cells))
    b = 0
    c = 0
    for key in paired_keys:
        first_passed = first_cells[key]["passed"]
        second_passed = second_cells[key]["passed"]
        if first_passed and not second_passed:
            b += 1
        elif second_passed and not first_passed:
            c += 1
    p_value = mcnemar_exact_p(b, c)
    return {
        "comparison": "%s vs %s" % (first, second),
        "first": first,
        "second": second,
        "paired_n": len(paired_keys),
        "b_first_wins": b,
        "c_second_wins": c,
        "discordant_n": b + c,
        "exact_p": p_value,
        "missing_first_cells": sorted(set(second_cells) - set(first_cells)),
        "missing_second_cells": sorted(set(first_cells) - set(second_cells)),
    }


def s3_cell_outcomes(summary):
    if not isinstance(summary, dict):
        return {}
    cells = summary.get("s3_cells")
    if not isinstance(cells, dict):
        return {}
    return cells


def apply_holm_to_mcnemar(mcnemar):
    p_values = {}
    for item in mcnemar["comparisons"]:
        p_values[item["comparison"]] = item["exact_p"]
    adjusted = holm_adjust(p_values)
    mcnemar["holm"] = adjusted
    for item in mcnemar["comparisons"]:
        holm = adjusted.get(item["comparison"], {})
        item["holm_adjusted_p"] = holm.get("adjusted_p")
        item["holm_reject_0_05"] = bool(holm.get("reject_0_05"))


def build_falsifier_checks(summaries):
    checks = []

    b0 = summaries.get("B0")
    b0_rate = nested_get(b0, ("pooled_s3", "rate"))
    b0_count = nested_get(b0, ("pooled_s3", "passed"))
    b0_n = nested_get(b0, ("pooled_s3", "n"))
    checks.append(falsifier(
        "B0 pooled S3 <= 0.40 preferred / <= 0.50 maximum",
        b0_rate is not None and b0_rate <= 0.50,
        "observed=%s (%s/%s); preferred_holds=%s"
        % (
            fmt_rate(b0_rate),
            b0_count if b0_count is not None else "NA",
            b0_n if b0_n is not None else "NA",
            str(bool(b0_rate is not None and b0_rate <= 0.40)).lower(),
        ),
    ))

    hybrid = summaries.get("DF-hybrid")
    strict_hybrid = summaries.get("DF-strict-hybrid")
    strict_hybrid_rate = nested_get(strict_hybrid, ("pooled_s3", "rate"))
    hybrid_rate = nested_get(hybrid, ("pooled_s3", "rate"))
    strict_gap = None
    if strict_hybrid_rate is not None and hybrid_rate is not None:
        strict_gap = hybrid_rate - strict_hybrid_rate
    checks.append(falsifier(
        "DF-strict-hybrid pooled S3 >= 0.60 and no more than 0.12 below DF-hybrid",
        strict_hybrid_rate is not None
        and strict_hybrid_rate >= 0.60
        and strict_gap is not None
        and strict_gap <= 0.12,
        "observed=%s; DF-hybrid=%s; gap=%s"
        % (fmt_rate(strict_hybrid_rate), fmt_rate(hybrid_rate), fmt_rate(strict_gap)),
    ))

    hybrid_ump = nested_get(hybrid, ("hygiene", "metrics_mean", "UsefulMemoryPrecision"))
    b5_ump = nested_get(summaries.get("B5"), ("hygiene", "metrics_mean", "UsefulMemoryPrecision"))
    ump_gap = None
    if hybrid_ump is not None and b5_ump is not None:
        ump_gap = b5_ump - hybrid_ump
    checks.append(falsifier(
        "DF-hybrid UsefulMemoryPrecision >= 0.45 and no more than 0.15 below B5",
        hybrid_ump is not None
        and hybrid_ump >= 0.45
        and ump_gap is not None
        and ump_gap <= 0.15,
        "DF-hybrid=%s; B5=%s; gap=%s"
        % (fmt_rate(hybrid_ump), fmt_rate(b5_ump), fmt_rate(ump_gap)),
    ))

    hybrid_rates = per_seed_rates(hybrid)
    hybrid_spread = None
    if hybrid_rates:
        hybrid_spread = max(hybrid_rates) - min(hybrid_rates)
    checks.append(falsifier(
        "DF-hybrid seed spread (max-min per-seed S3) <= 0.12 preferred / <= 0.16 maximum",
        len(hybrid_rates) >= 2 and hybrid_spread is not None and hybrid_spread <= 0.16,
        "observed=%s from rates=%s; preferred_holds=%s"
        % (
            fmt_rate(hybrid_spread),
            ", ".join(fmt_rate(rate) for rate in hybrid_rates),
            str(bool(hybrid_spread is not None and hybrid_spread <= 0.12)).lower(),
        ),
    ))

    hybrid_rer = nested_get(hybrid, ("hygiene", "metrics_mean", "RepeatedErrorRate"))
    b5_rer = nested_get(summaries.get("B5"), ("hygiene", "metrics_mean", "RepeatedErrorRate"))
    hybrid_reg = nested_get(hybrid, ("hygiene", "metrics_mean", "RegressionAfterUpdate"))
    checks.append(falsifier(
        "DF-hybrid RepeatedErrorRate <= B5 and <= 0.15; RegressionAfterUpdate <= 0.08",
        hybrid_rer is not None
        and b5_rer is not None
        and hybrid_reg is not None
        and hybrid_rer <= b5_rer
        and hybrid_rer <= 0.15
        and hybrid_reg <= 0.08,
        "DF-hybrid RepeatedErrorRate=%s; B5 RepeatedErrorRate=%s; RegressionAfterUpdate=%s"
        % (fmt_rate(hybrid_rer), fmt_rate(b5_rer), fmt_rate(hybrid_reg)),
    ))
    return checks


def falsifier(name, passed_check, detail):
    return {
        "check": name,
        "status": "PASS" if passed_check else "FAIL",
        "detail": detail,
    }


def mcnemar_exact_p(b, c):
    discordant = int(b) + int(c)
    if discordant == 0:
        return 1.0
    tail = 0
    for i in range(min(int(b), int(c)) + 1):
        tail += math.comb(discordant, i)
    return min(1.0, 2.0 * tail / float(2 ** discordant))


def holm_adjust(p_values):
    ordered = sorted(
        ((key, float(value)) for key, value in p_values.items() if value is not None),
        key=lambda item: (item[1], item[0]),
    )
    m = len(ordered)
    adjusted = {}
    running_max = 0.0
    for index, item in enumerate(ordered):
        key, p_value = item
        raw_adjusted = min(1.0, (m - index) * p_value)
        running_max = max(running_max, raw_adjusted)
        adjusted[key] = {
            "raw_p": p_value,
            "adjusted_p": running_max,
            "reject_0_05": running_max <= 0.05,
        }
    return adjusted


def render_report(analysis):
    lines = []
    metadata = analysis["metadata"]
    summaries = analysis["conditions"]
    lines.append("# DreamBench-SWE Confirmatory Fold")
    lines.append("")
    lines.append("- Results root: `%s`" % metadata["results_root"])
    lines.append("- Result files discovered: %d" % metadata["result_files_discovered"])
    lines.append("- Dedup: %s." % metadata["dedup"])
    lines.append("- Validity gate: exclude records whose `error_type` contains `%s`." % "`, `".join(VALIDITY_ERROR_SUBSTRINGS))
    lines.append("- Outcome: S3 `pass_at_1`; S3 means `oracle_id` ends with `-s3` or `ordinal == 3`.")
    lines.append("- Warmup: valid non-S3 records; grouped runs can therefore contribute ordinals beyond 1 and 2.")
    lines.append("- Hygiene: %s." % metadata["hygiene_aggregation"])
    if metadata["warnings"]:
        lines.append("- Warnings: %d skipped/malformed inputs; see JSON for details." % len(metadata["warnings"]))
    lines.append("")
    render_per_condition_table(lines, summaries)
    lines.append("")
    render_exclusions(lines, summaries)
    lines.append("")
    render_ablation_ladder(lines, analysis["ablation_ladder"])
    lines.append("")
    render_mcnemar(lines, analysis["mcnemar_family"])
    lines.append("")
    render_supplemental_mcnemar(lines, analysis["supplemental_mcnemar"])
    lines.append("")
    render_hygiene(lines, summaries)
    lines.append("")
    render_capsule_cr(lines, summaries)
    lines.append("")
    render_falsifiers(lines, analysis["falsifier_checks"])
    return "\n".join(lines)


def render_per_condition_table(lines, summaries):
    lines.append("## Per-Condition S3 Pass Rates")
    lines.append("")
    present = [condition for condition in CONDITION_ORDER if condition in summaries]
    if not present:
        lines.append("No requested conditions found.")
        return
    seeds = sorted({int(seed) for condition in present for seed in summaries[condition]["per_seed_s3"]})
    header = ["Condition"] + ["seed %s S3" % seed for seed in seeds] + ["pooled S3", "warmup", "S3 n", "warmup n", "excluded"]
    lines.append(markdown_row(header))
    lines.append(markdown_separator(len(header)))
    for condition in present:
        summary = summaries[condition]
        row = [condition]
        for seed in seeds:
            row.append(fmt_count_rate(summary["per_seed_s3"].get(str(seed))))
        row.extend([
            fmt_count_rate(summary["pooled_s3"]),
            fmt_count_rate(summary["warmup"]),
            str(summary["pooled_s3"]["n"]),
            str(summary["warmup"]["n"]),
            str(summary["excluded_records"]),
        ])
        lines.append(markdown_row(row))


def render_exclusions(lines, summaries):
    lines.append("## Validity-Gate Exclusions")
    lines.append("")
    present = [condition for condition in CONDITION_ORDER if condition in summaries]
    if not present:
        lines.append("No exclusions to report.")
        return
    lines.append(markdown_row(["Condition", "Excluded", "Reasons"]))
    lines.append(markdown_separator(3))
    for condition in present:
        summary = summaries[condition]
        reasons = summary["excluded_by_reason"]
        reason_text = ", ".join("%s=%s" % (key, reasons[key]) for key in sorted(reasons)) if reasons else "-"
        lines.append(markdown_row([condition, str(summary["excluded_records"]), reason_text]))


def render_ablation_ladder(lines, ladder):
    lines.append("## Ablation Ladder")
    lines.append("")
    labels = ["B5", "DF (typed-only)", "DF-raw-only", "DF-hybrid"]
    lines.append(markdown_row(["Metric"] + labels))
    lines.append(markdown_separator(1 + len(ABLATION_LADDER)))
    pooled_row = ["pooled S3"]
    per_seed_row = ["per-seed S3"]
    for condition in ABLATION_LADDER:
        item = ladder.get(condition)
        if item is None:
            pooled_row.append("NA")
            per_seed_row.append("NA")
            continue
        per_seed = item["per_seed_s3"]
        per_seed_text = ", ".join(
            "s%s=%s" % (seed, fmt_count_rate(per_seed[seed]))
            for seed in sorted(per_seed, key=lambda value: int(value))
        )
        pooled_row.append(fmt_count_rate(item["pooled_s3"]))
        per_seed_row.append(per_seed_text or "NA")
    lines.append(markdown_row(pooled_row))
    lines.append(markdown_row(per_seed_row))


def render_mcnemar(lines, mcnemar):
    lines.append("## Exact McNemar Family")
    lines.append("")
    lines.append(markdown_row(["Comparison", "paired n", "b first wins", "c second wins", "discordant n", "exact p", "Holm p", "Holm 0.05"]))
    lines.append(markdown_separator(8))
    for item in mcnemar["comparisons"]:
        lines.append(markdown_row([
            item["comparison"],
            str(item["paired_n"]),
            str(item["b_first_wins"]),
            str(item["c_second_wins"]),
            str(item["discordant_n"]),
            fmt_p(item["exact_p"]),
            fmt_p(item.get("holm_adjusted_p")),
            "yes" if item.get("holm_reject_0_05") else "no",
        ]))


def render_supplemental_mcnemar(lines, supplemental):
    lines.append("## Supplemental McNemar Comparisons")
    lines.append("")
    comparisons = supplemental.get("comparisons") if isinstance(supplemental, dict) else []
    if not comparisons:
        lines.append("No supplemental comparisons found.")
        return
    note = supplemental.get("note")
    if note:
        lines.append("%s" % note)
        lines.append("")
    lines.append(markdown_row(["Comparison", "paired n", "b first wins", "c second wins", "discordant n", "exact p"]))
    lines.append(markdown_separator(6))
    for item in comparisons:
        lines.append(markdown_row([
            item["comparison"],
            str(item["paired_n"]),
            str(item["b_first_wins"]),
            str(item["c_second_wins"]),
            str(item["discordant_n"]),
            fmt_p(item["exact_p"]),
        ]))


def render_hygiene(lines, summaries):
    lines.append("## Hygiene Panel")
    lines.append("")
    present = [condition for condition in CONDITION_ORDER if condition in summaries]
    if not present:
        lines.append("No hygiene data found.")
        return
    header = ["Condition", "dirs"] + list(HYGIENE_METRICS) + ["AdmTok/task"] + list(HYGIENE_COUNTS)
    lines.append(markdown_row(header))
    lines.append(markdown_separator(len(header)))
    for condition in present:
        hygiene = summaries[condition]["hygiene"]
        metrics = hygiene["metrics_mean"]
        counts = hygiene["counts_sum"]
        row = [condition, str(hygiene["source_file_count"])]
        row.extend(fmt_rate(metrics.get(metric)) for metric in HYGIENE_METRICS)
        row.append(fmt_float(hygiene.get("AdmTok/task")))
        row.extend(str(counts.get(name, 0)) for name in HYGIENE_COUNTS)
        lines.append(markdown_row(row))


def render_capsule_cr(lines, summaries):
    lines.append("## Capsule + CR Check")
    lines.append("")
    lines.append(markdown_row(["Condition", "S3 n", "raw-evidence capsule", "CONTRADICTION item"]))
    lines.append(markdown_separator(4))
    for condition in ("DF-hybrid", "DF-raw-only"):
        summary = summaries.get(condition)
        if not summary:
            lines.append(markdown_row([condition, "NA", "NA", "NA"]))
            continue
        check = summary.get("capsule_cr_check", {})
        lines.append(markdown_row([
            condition,
            str(check.get("s3_n", 0)),
            "%s/%s %s" % (
                check.get("raw_evidence_capsule_count", 0),
                check.get("s3_n", 0),
                fmt_rate(check.get("raw_evidence_capsule_fraction")),
            ),
            "%s/%s %s" % (
                check.get("contradiction_item_count", 0),
                check.get("s3_n", 0),
                fmt_rate(check.get("contradiction_item_fraction")),
            ),
        ]))


def render_falsifiers(lines, checks):
    lines.append("## Falsifier Checks")
    lines.append("")
    lines.append(markdown_row(["Status", "Check", "Detail"]))
    lines.append(markdown_separator(3))
    for check in checks:
        lines.append(markdown_row([check["status"], check["check"], check["detail"]]))


def timestamp_from_path(path):
    match = TIMESTAMP_RE.search(path)
    if match:
        return match.group(1)
    run_dir = os.path.basename(os.path.dirname(os.path.dirname(path)))
    match = RUN_DIR_TIMESTAMP_RE.search(run_dir)
    if match:
        return match.group(1)
    return None


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def coerce_int(value):
    try:
        if value is None or value is True or value is False:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_float(value):
    try:
        if value is None or value is True or value is False:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def average(values):
    values = list(values)
    if not values:
        return None
    return sum(values) / float(len(values))


def safe_div(numerator, denominator):
    if not denominator:
        return None
    return float(numerator) / float(denominator)


def invalid_reason(record):
    error_type = str(record.get("error_type") or "")
    lowered = error_type.lower()
    reasons = [name for name in VALIDITY_ERROR_SUBSTRINGS if name in lowered]
    return ",".join(reasons)


def record_ordinal(record):
    ordinal = coerce_int(record.get("ordinal"))
    if ordinal is not None:
        return ordinal
    oracle_id = str(record.get("oracle_id") or "")
    match = SESSION_NUMBER_RE.search(oracle_id)
    if match:
        return coerce_int(match.group(1))
    return None


def sequence_id(record):
    oracle_id = str(record.get("oracle_id") or "")
    if oracle_id:
        return SESSION_SUFFIX_RE.sub("", oracle_id)
    return ""


def is_s3(record):
    oracle_id = str(record.get("_oracle_id") or record.get("oracle_id") or "")
    return oracle_id.endswith("-s3") or record.get("_ordinal") == 3 or record_ordinal(record) == 3


def is_warmup(record):
    return not is_s3(record)


def record_passed(record):
    return bool(record.get("pass_at_1"))


def cell_key(record):
    return "seed%s:%s" % (record["_seed"], record["_sequence_id"])


def record_cell(record):
    return {
        "seed": record["_seed"],
        "sequence_id": record["_sequence_id"],
        "oracle_id": record["_oracle_id"],
        "passed": record_passed(record),
        "source_path": record["_source_path"],
        "timestamp": record["_timestamp"],
    }


def count_by_reason(records):
    out = defaultdict(int)
    for record in records:
        reason = record.get("_invalid_reason") or "unknown"
        out[reason] += 1
    return dict(out)


def has_raw_evidence_capsule(record):
    for item in memory_context_items(record):
        content = str(item.get("content") or "")
        if content.startswith("Raw evidence"):
            return True
        tags = item.get("retrieval_tags")
        if isinstance(tags, list) and any(str(tag) == "raw-evidence" for tag in tags):
            return True
    return False


def has_contradiction_item(record):
    for item in memory_context_items(record):
        item_type = str(item.get("type") or "")
        if "CONTRADICTION" in item_type.upper():
            return True
    return False


def memory_context_items(record):
    items = record.get("memory_context")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def per_seed_rates(summary):
    if not isinstance(summary, dict):
        return []
    per_seed = summary.get("per_seed_s3")
    if not isinstance(per_seed, dict):
        return []
    rates = []
    for seed in sorted(per_seed, key=lambda value: int(value)):
        rate = per_seed[seed].get("rate")
        if rate is not None:
            rates.append(rate)
    return rates


def per_seed_rate_pairs(summary):
    if not isinstance(summary, dict):
        return []
    per_seed = summary.get("per_seed_s3")
    if not isinstance(per_seed, dict):
        return []
    pairs = []
    for seed in sorted(per_seed, key=lambda value: int(value)):
        item = per_seed[seed]
        if item.get("n"):
            pairs.append((item.get("passed"), item.get("n")))
    return pairs


def nested_get(mapping, keys):
    value = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def markdown_row(values):
    return "| " + " | ".join(escape_md(str(value)) for value in values) + " |"


def markdown_separator(n):
    return "| " + " | ".join("---" for _ in range(n)) + " |"


def escape_md(value):
    return value.replace("|", "\\|")


def fmt_count_rate(summary):
    if not isinstance(summary, dict) or summary.get("n") in (None, 0):
        return "NA"
    return "%d/%d %s" % (summary["passed"], summary["n"], fmt_rate(summary["rate"]))


def fmt_rate(value):
    if value is None:
        return "NA"
    return "%.3f" % float(value)


def fmt_float(value):
    if value is None:
        return "NA"
    return "%.1f" % float(value)


def fmt_p(value):
    if value is None:
        return "NA"
    if value < 0.0001:
        return "%.2e" % value
    return "%.4f" % value


def display_path(path):
    cwd = os.getcwd()
    try:
        return os.path.relpath(path, cwd)
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
