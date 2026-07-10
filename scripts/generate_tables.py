#!/usr/bin/env python3
"""Render confirmatory-fold paper tables from the frozen 3-seed record.

This script is intentionally narrow: it reads analysis/fold/confirmatory.json
and emits the paper-facing table artifacts plus machine-readable CI/stat
snapshots. It does not inspect live result directories.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from stats_analysis import bootstrap_mean_ci, wilson_ci


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "analysis" / "fold" / "confirmatory.json"
DEFAULT_OUTPUT_DIR = ROOT / "analysis" / "fold"

CI_LEVEL = 0.95
ALPHA = 1.0 - CI_LEVEL

BASELINE_ROWS = (
    ("B0", "B0 No memory", "B0 No memory"),
    ("B1", "B1 Raw episodic", "B1 Raw episodic"),
    ("B2", "B2 Vector traces", "B2 Vector traces"),
    ("B3", "B3 Reflection-vector", "B3 Reflection-vector"),
    ("B4", "B4 Untyped summary", "B4 Untyped summary"),
    ("B5", r"B5 Instance-mem.\ sub.", "B5 Instance-mem. sub."),
    ("B5-MEM0", "B5-MEM0 Hosted Mem0 (pinned)", "B5-MEM0 Hosted Mem0 (pinned)"),
    ("B6", "B6 Subtask memory", "B6 Subtask memory"),
    ("B7", "B7 Task tracker", "B7 Task tracker"),
    ("DF", "reference-probe typed-only (DF)", "reference-probe typed-only (DF)"),
    ("DF-raw-only", "reference-probe raw-only (DF-raw-only)", "reference-probe raw-only (DF-raw-only)"),
    ("DF-hybrid", "reference-probe hybrid (DF-hybrid)", "reference-probe hybrid (DF-hybrid)"),
    ("DF-strict", "reference-probe strict (DF-strict)", "reference-probe strict (DF-strict)"),
    ("DF-strict-hybrid", "reference-probe strict-hybrid (DF-strict-hybrid)", "reference-probe strict-hybrid (DF-strict-hybrid)"),
)

LADDER_ROWS = (
    ("B5", "B5 (verbatim event-memory)", "B5 (verbatim event-memory)"),
    ("DF", "reference-probe typed-only (DF)", "reference-probe typed-only (DF)"),
    ("DF-raw-only", "reference-probe raw-only (DF-raw-only)", "reference-probe raw-only (DF-raw-only)"),
    ("DF-hybrid", "reference-probe hybrid (DF-hybrid)", "reference-probe hybrid (DF-hybrid)"),
)

PIPELINE_ABLATION_ROWS = (
    ("A0", "A0", "Episodic-only, no consolidation"),
    ("A2", "A2", "No contradiction repair"),
    ("A4", "A4", "No counterfactual replay"),
    ("A5", "A5", "No stale suppression"),
    ("A6", "A6", "No retrieval gate"),
    ("A11", "A11", "Forced global consolidation"),
)

TAXONOMY_CONDITIONS = (
    ("B5", "B5"),
    ("B5-MEM0", "B5-MEM0"),
    ("DF", "Probe typed-only"),
    ("DF-raw-only", "Probe raw-only"),
    ("DF-hybrid", "Probe hybrid"),
)

SYNTHESIS_APPLY_SEQUENCES = frozenset(
    {
        "expr-convention-opnaming",
        "expr-floordiv-category",
        "expr-generated-category",
        "expr-generated-precgroup",
    }
)

SYNTHESIS_SLICE_SEQUENCES = (
    "synth-config-csv-width-delta",
    "synth-config-dupsec-code",
    "synth-config-schema-audit-code",
    "synth-expr-bitwise-help",
    "synth-expr-help-epoch",
    "synth-expr-precedence-derive",
    "synth-todo-overdue-handoff",
    "synth-todo-report-channel",
)

SYNTHESIS_SLICE_COUNTS = {
    "B0": (0, 24),
    "B1": (0, 24),
    "B3": (3, 24),
    "B5": (6, 24),
    "DF": (4, 24),
    "DF-raw-only": (7, 24),
    "DF-hybrid": (6, 24),
    "B5-MEM0": (0, 24),
}

MCNEMAR_LABELS_TEX = {
    "DF-hybrid vs B5": r"reference-probe hybrid vs.\ B5",
    "DF-hybrid vs DF": r"reference-probe hybrid vs.\ reference-probe typed-only",
    "DF-raw-only vs B5": r"reference-probe raw-only vs.\ B5",
    "DF vs B5": r"reference-probe typed-only vs.\ B5",
    "DF vs B3": r"reference-probe typed-only vs.\ B3",
}

MCNEMAR_LABELS_TEXT = {
    "DF-hybrid vs B5": "reference-probe hybrid vs B5",
    "DF-hybrid vs DF": "reference-probe hybrid vs reference-probe typed-only",
    "DF-raw-only vs B5": "reference-probe raw-only vs B5",
    "DF vs B5": "reference-probe typed-only vs B5",
    "DF vs B3": "reference-probe typed-only vs B3",
}


@dataclass(frozen=True)
class CountRate:
    successes: int
    n: int

    @property
    def rate(self) -> float | None:
        if self.n == 0:
            return None
        return self.successes / self.n


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = read_json(args.input)
    rendered = build_rendered(payload, input_path=args.input)

    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "final_tables.json").write_text(
            json.dumps(rendered["final_tables_json"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (args.output_dir / "final_tables.tex").write_text(
            rendered["final_tables_tex"] + "\n",
            encoding="utf-8",
        )
        (args.output_dir / "stats.json").write_text(
            json.dumps(rendered["stats_json"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (args.output_dir / "stats.tex").write_text(
            rendered["stats_tex"] + "\n",
            encoding="utf-8",
        )

    print_summary(rendered, output_dir=args.output_dir, wrote=not args.no_write)
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render DreamBench-SWE 3-seed confirmatory tables.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args(argv)


def read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_rendered(payload: Mapping[str, Any], *, input_path: Path) -> dict[str, Any]:
    condition_rows = build_condition_rows(payload)
    taxonomy_rows = build_taxonomy_rows(payload)
    ladder_rows = build_ladder_rows(payload)
    pipeline_rows = build_pipeline_rows(payload)
    mcnemar_rows = build_mcnemar_rows(payload)
    supplemental_mcnemar_rows = build_supplemental_mcnemar_rows(payload)
    validate_sanity(condition_rows, taxonomy_rows)

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "generated_at_utc": generated_at,
        "generated_by": "scripts/generate_tables.py",
        "input": rel(input_path),
        "source": "analysis/fold/confirmatory.json frozen 3-seed fold",
        "live_results_scanned": False,
        "pass_rate_ci": {
            "level": CI_LEVEL,
            "method": "Wilson score interval",
            "note": "Displayed pass-rate intervals are Wilson/binomial score intervals, matching the enhancement-plan sanity values.",
        },
        "paired_risk_difference_ci": {
            "level": CI_LEVEL,
            "method": "Exact conditional matched-pair interval: Clopper-Pearson CI for b/(b+c), transformed to (b-c)/paired_n with discordant_n fixed.",
        },
    }

    final_tables_json: dict[str, Any] = {"_metadata": metadata}
    for row in condition_rows:
        final_tables_json[row["condition"]] = row["condition_summary"]
    final_tables_json["_tables"] = {
        "confirmatory_baselines": condition_rows,
        "taxonomy_results": taxonomy_rows,
        "ladder": ladder_rows,
        "pipeline_ablations": pipeline_rows,
        "mcnemar_family": mcnemar_rows,
        "supplemental_mcnemar": supplemental_mcnemar_rows,
    }

    stats_json = {
        "metadata": metadata,
        "pass_rate_condition_rows": condition_rows,
        "taxonomy_results": taxonomy_rows,
        "ladder": ladder_rows,
        "pipeline_ablations": pipeline_rows,
        "mcnemar_family": mcnemar_rows,
        "supplemental_mcnemar": supplemental_mcnemar_rows,
    }

    return {
        "final_tables_json": final_tables_json,
        "stats_json": stats_json,
        "final_tables_tex": render_final_tables_tex(
            condition_rows=condition_rows,
            taxonomy_rows=taxonomy_rows,
            ladder_rows=ladder_rows,
            pipeline_rows=pipeline_rows,
            mcnemar_rows=mcnemar_rows,
        ),
        "stats_tex": render_stats_tex(mcnemar_rows, supplemental_mcnemar_rows),
    }


def build_condition_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    conditions = mapping(payload, "conditions")
    for condition, tex_label, text_label in BASELINE_ROWS:
        item = mapping(conditions, condition)
        pooled = count_rate_from_summary(mapping(item, "pooled_s3"))
        warmup = count_rate_from_summary(mapping(item, "warmup"))
        excluded = int(item.get("excluded_records") or 0)
        excluded_by_reason = item.get("excluded_by_reason") if isinstance(item.get("excluded_by_reason"), Mapping) else {}
        condition_summary = condition_json_summary(item, pooled=pooled, warmup=warmup)
        rows.append(
            {
                "condition": condition,
                "label_tex": tex_label,
                "label": text_label,
                "n_s3": pooled.n,
                "exclusions": excluded,
                "excluded_by_reason": dict(excluded_by_reason),
                "pass_at_1": rate_json(pooled),
                "warmup": rate_json(warmup),
                "condition_summary": condition_summary,
            }
        )
    return rows


def condition_json_summary(item: Mapping[str, Any], *, pooled: CountRate, warmup: CountRate) -> dict[str, Any]:
    out = {
        "n_S3": pooled.n,
        "partial": pooled.n not in (65, 66),
        "partial_note": "",
        "excluded_records": int(item.get("excluded_records") or 0),
        "excluded_by_reason": dict(item.get("excluded_by_reason") or {}),
        "pass_at_1_rate": rate_json(pooled),
        "warmup_rate": rate_json(warmup),
        "per_seed_s3": per_seed_json(mapping(item, "per_seed_s3")),
        "hygiene": item.get("hygiene") or {},
    }
    if pooled.n < 66:
        out["partial_note"] = f"{66 - pooled.n} S3 cell(s) excluded by validity gate"
    return out


def per_seed_json(per_seed: Mapping[str, Any]) -> dict[str, Any]:
    return {str(seed): rate_json(count_rate_from_summary(summary)) for seed, summary in sorted(per_seed.items())}


def build_taxonomy_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    conditions = mapping(payload, "conditions")
    all_sequences = {
        str(cell.get("sequence_id"))
        for cell in iter_condition_cells(mapping(conditions, "B5"))
        if cell.get("sequence_id")
    }
    recall_sequences = sorted(all_sequences - SYNTHESIS_APPLY_SEQUENCES)
    legacy_synthesis_sequences = sorted(all_sequences & SYNTHESIS_APPLY_SEQUENCES)
    if len(recall_sequences) != 18 or len(legacy_synthesis_sequences) != 4:
        raise SystemExit(
            "taxonomy invariant failed: "
            f"recall={len(recall_sequences)} legacy_synthesis={len(legacy_synthesis_sequences)}"
        )

    rows: list[dict[str, Any]] = []
    recall_row: dict[str, Any] = {
        "trap_class": "Recall-verbatim",
        "sequences": recall_sequences,
        "expected_cells": 54,
        "cells": 54,
        "source": "v1.0 confirmatory fold",
        "conditions": {},
    }
    for condition, condition_label in TAXONOMY_CONDITIONS:
        summary = taxonomy_count(mapping(conditions, condition), set(recall_sequences))
        recall_row["conditions"][condition] = {
            "label": condition_label,
            "pass_at_1": rate_json(summary),
        }
    rows.append(recall_row)

    synth_row: dict[str, Any] = {
        "trap_class": "Synthesis slice",
        "sequences": sorted(SYNTHESIS_SLICE_SEQUENCES),
        "expected_cells": 24,
        "cells": 24,
        "source": "DreamBench-SWE-Synth directional slice",
        "conditions": {},
    }
    for condition, condition_label in TAXONOMY_CONDITIONS:
        successes, n = SYNTHESIS_SLICE_COUNTS[condition]
        synth_row["conditions"][condition] = {
            "label": condition_label,
            "pass_at_1": rate_json(CountRate(successes, n)),
        }
    rows.append(synth_row)
    return rows


def taxonomy_count(condition_payload: Mapping[str, Any], sequences: set[str]) -> CountRate:
    cells = [
        cell
        for cell in iter_condition_cells(condition_payload)
        if str(cell.get("sequence_id") or "") in sequences
    ]
    successes = sum(1 for cell in cells if bool(cell.get("passed")))
    return CountRate(successes=successes, n=len(cells))


def build_ladder_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    ladder = mapping(payload, "ablation_ladder")
    rows: list[dict[str, Any]] = []
    for condition, tex_label, text_label in LADDER_ROWS:
        item = mapping(ladder, condition)
        per_seed = mapping(item, "per_seed_s3")
        pooled = count_rate_from_summary(mapping(item, "pooled_s3"))
        rows.append(
            {
                "condition": condition,
                "label_tex": tex_label,
                "label": text_label,
                "per_seed": per_seed_json(per_seed),
                "pooled_s3": rate_json(pooled),
            }
        )
    return rows


def build_pipeline_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    conditions = mapping(payload, "conditions")
    rows: list[dict[str, Any]] = []
    for condition, tex_label, component in PIPELINE_ABLATION_ROWS:
        item = mapping(conditions, condition)
        per_seed = mapping(item, "per_seed_s3")
        pooled = count_rate_from_summary(mapping(item, "pooled_s3"))
        rows.append(
            {
                "condition": condition,
                "label_tex": tex_label,
                "label": condition,
                "component": component,
                "per_seed": per_seed_json(per_seed),
                "pooled_s3": rate_json(pooled),
            }
        )
    return rows


def build_mcnemar_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    family = mapping(payload, "mcnemar_family")
    return [mcnemar_row_json(item, include_holm=True) for item in list(family.get("comparisons") or [])]


def build_supplemental_mcnemar_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    family = mapping(payload, "supplemental_mcnemar")
    return [mcnemar_row_json(item, include_holm=False) for item in list(family.get("comparisons") or [])]


def mcnemar_row_json(item: Mapping[str, Any], *, include_holm: bool) -> dict[str, Any]:
    comparison = str(item.get("comparison") or f"{item.get('first')} vs {item.get('second')}")
    paired_n = int(item.get("paired_n") or 0)
    b_first = int(item.get("b_first_wins") or 0)
    c_second = int(item.get("c_second_wins") or 0)
    diff = (b_first - c_second) / paired_n if paired_n else None
    rd_ci = paired_risk_difference_ci(b_first=b_first, c_second=c_second, paired_n=paired_n)
    discordant_ci = exact_binomial_ci(b_first, b_first + c_second)
    out = {
        "comparison": comparison,
        "label": MCNEMAR_LABELS_TEXT.get(comparison, comparison),
        "label_tex": MCNEMAR_LABELS_TEX.get(comparison, latex_escape(comparison)),
        "first": item.get("first"),
        "second": item.get("second"),
        "paired_n": paired_n,
        "b_first_wins": b_first,
        "c_second_wins": c_second,
        "discordant_n": int(item.get("discordant_n") or b_first + c_second),
        "paired_risk_difference": diff,
        "paired_risk_difference_ci_95": rd_ci,
        "discordant_win_fraction": {
            "numerator": b_first,
            "denominator": b_first + c_second,
            "value": b_first / (b_first + c_second) if (b_first + c_second) else None,
            "exact_binomial_ci_95": discordant_ci,
        },
        "mcnemar_exact_p": item.get("exact_p"),
        "missing_first_cells": item.get("missing_first_cells") or [],
        "missing_second_cells": item.get("missing_second_cells") or [],
    }
    if include_holm:
        out["holm_adjusted_p"] = item.get("holm_adjusted_p")
        out["holm_reject_0_05"] = bool(item.get("holm_reject_0_05"))
    return out


def iter_condition_cells(condition_payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    cells = condition_payload.get("s3_cells")
    if not isinstance(cells, Mapping):
        return []
    return [cell for cell in cells.values() if isinstance(cell, Mapping)]


def count_rate_from_summary(item: Mapping[str, Any]) -> CountRate:
    return CountRate(successes=int(item.get("passed") or 0), n=int(item.get("n") or 0))


def rate_json(count_rate: CountRate) -> dict[str, Any]:
    wilson = clean_ci(wilson_ci(count_rate.successes, count_rate.n))
    exact = clean_ci(exact_binomial_ci(count_rate.successes, count_rate.n))
    outcomes = [1] * count_rate.successes + [0] * max(0, count_rate.n - count_rate.successes)
    bootstrap = clean_ci(
        bootstrap_mean_ci(outcomes, f"{count_rate.successes}/{count_rate.n}")
        if count_rate.n
        else [None, None]
    )
    return {
        "numerator": count_rate.successes,
        "denominator": count_rate.n,
        "value": count_rate.rate,
        "wilson_ci_95": wilson,
        "exact_binomial_ci_95": exact,
        "bootstrap_ci_95": bootstrap,
        "display_ci_95": wilson,
        "display_ci_method": "Wilson score",
    }


def exact_binomial_ci(successes: int, n: int, alpha: float = ALPHA) -> list[float | None]:
    if n == 0:
        return [None, None]
    if successes < 0 or successes > n:
        raise ValueError(f"invalid binomial count {successes}/{n}")

    if successes == 0:
        lower = 0.0
    else:
        lower = binary_search_monotone(
            lo=0.0,
            hi=successes / n,
            target=alpha / 2.0,
            fn=lambda p: binomial_sf(successes, n, p),
            increasing=True,
        )

    if successes == n:
        upper = 1.0
    else:
        upper = binary_search_monotone(
            lo=successes / n,
            hi=1.0,
            target=alpha / 2.0,
            fn=lambda p: binomial_cdf(successes, n, p),
            increasing=False,
        )
    return [lower, upper]


def paired_risk_difference_ci(*, b_first: int, c_second: int, paired_n: int) -> list[float | None]:
    discordant = b_first + c_second
    if paired_n == 0:
        return [None, None]
    if discordant == 0:
        return [0.0, 0.0]
    lo, hi = exact_binomial_ci(b_first, discordant)
    assert isinstance(lo, float) and isinstance(hi, float)
    scale = discordant / paired_n
    return [scale * ((2.0 * lo) - 1.0), scale * ((2.0 * hi) - 1.0)]


def binomial_cdf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.comb(n, i) * (p**i) * ((1.0 - p) ** (n - i))
    return total


def binomial_sf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0 if k <= 0 else 0.0
    if p >= 1.0:
        return 1.0
    total = 0.0
    for i in range(k, n + 1):
        total += math.comb(n, i) * (p**i) * ((1.0 - p) ** (n - i))
    return total


def binary_search_monotone(
    *,
    lo: float,
    hi: float,
    target: float,
    fn: Any,
    increasing: bool,
    iterations: int = 80,
) -> float:
    low = lo
    high = hi
    for _ in range(iterations):
        mid = (low + high) / 2.0
        value = fn(mid)
        if increasing:
            if value < target:
                low = mid
            else:
                high = mid
        else:
            if value > target:
                low = mid
            else:
                high = mid
    return (low + high) / 2.0


def clean_ci(ci: list[float | None]) -> list[float | None]:
    return [clean_float(value) for value in ci]


def clean_float(value: float | None) -> float | None:
    if value is None:
        return None
    if abs(value) < 1e-15:
        return 0.0
    if abs(value - 1.0) < 1e-15:
        return 1.0
    return float(value)


def validate_sanity(condition_rows: list[dict[str, Any]], taxonomy_rows: list[dict[str, Any]]) -> None:
    by_condition = {row["condition"]: row for row in condition_rows}
    expected = {
        "B5": (48, 66, (0.610, 0.820)),
        "DF-hybrid": (52, 66, (0.675, 0.869)),
        "DF": (44, 65, (0.556, 0.778)),
        "B3": (23, 66, (0.245, 0.469)),
    }
    for condition, (successes, n, ci) in expected.items():
        pass_rate = by_condition[condition]["pass_at_1"]
        actual = (
            int(pass_rate["numerator"]),
            int(pass_rate["denominator"]),
            tuple(round(float(value), 3) for value in pass_rate["display_ci_95"]),
        )
        want = (successes, n, ci)
        if actual != want:
            raise SystemExit(f"sanity check failed for {condition}: got {actual}, expected {want}")

    synth = next(row for row in taxonomy_rows if row["trap_class"] == "Synthesis slice")
    b5 = synth["conditions"]["B5"]["pass_at_1"]
    actual_synth = (
        int(b5["numerator"]),
        int(b5["denominator"]),
        tuple(round(float(value), 3) for value in b5["display_ci_95"]),
    )
    expected_synth = (6, 24, (0.120, 0.449))
    if actual_synth != expected_synth:
        raise SystemExit(f"sanity check failed for synthesis B5: got {actual_synth}, expected {expected_synth}")


def render_final_tables_tex(
    *,
    condition_rows: list[dict[str, Any]],
    taxonomy_rows: list[dict[str, Any]],
    ladder_rows: list[dict[str, Any]],
    pipeline_rows: list[dict[str, Any]],
    mcnemar_rows: list[dict[str, Any]],
) -> str:
    parts = [
        "% Generated by scripts/generate_tables.py from analysis/fold/confirmatory.json.",
        render_confirmatory_table(condition_rows),
        render_taxonomy_table(taxonomy_rows),
        render_ladder_table(ladder_rows),
        render_mcnemar_table(mcnemar_rows),
        render_pipeline_table(pipeline_rows),
    ]
    return "\n\n".join(parts)


def render_confirmatory_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{lrrlr}",
        r"\toprule",
        r"Condition & S3 $n$ & Excl. & Pass@1 (95\% CI) & Warmup \\",
        r"\midrule",
    ]
    for row in rows[:9]:
        lines.append(confirmatory_line(row))
    lines.append(r"\midrule")
    for row in rows[9:]:
        lines.append(confirmatory_line(row))
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"}%",
            r"\caption{Confirmatory fold S3 pass@1 (22 sequences $\times$ 3 seeds).",
            r"  ``Excl.''\ counts validity-gate exclusions.  DF-strict has 11 exclusions and is not",
            r"  comparable as a complete condition; DF-strict-hybrid completed with 66 S3 records and",
            r"  0 exclusions.  B5-MEM0 is included as the pinned hosted-Mem0 baseline (66 S3 records,",
            r"  0 exclusions, pass@1 0.091).  Warmup is the pre-trap warm-up pass rate.",
            r"  Source: \texttt{CONFIRMATORY-FOLD.md}, Per-Condition S3 Pass Rates.}",
            r"\label{tab:confirmatoryBaselines}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def confirmatory_line(row: Mapping[str, Any]) -> str:
    return (
        f"{row['label_tex']} & {row['n_s3']} & {row['exclusions']}  & "
        f"{fmt_count_rate_ci(row['pass_at_1'])} & {fmt_rate(row['warmup']['value'])} \\\\"
    )


def render_taxonomy_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\scriptsize",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
            r"Trap class & Cells & B5 & B5-MEM0 & Probe typed-only & Probe raw-only & Probe hybrid \\",
        r"\midrule",
    ]
    for row in rows:
        cells = [row["trap_class"], str(row["cells"])]
        for condition, _ in TAXONOMY_CONDITIONS:
            cells.append(fmt_count_rate_ci(row["conditions"][condition]["pass_at_1"]))
        lines.append(" & ".join(cells) + r" \\")
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"}%",
            r"\caption{Descriptive S3 outcomes for the v1.0 recall-verbatim row and the separate",
            r"pre-registered DreamBench-SWE-Synth directional slice.  Probe typed-only has one excluded",
            r"recall-verbatim S3 cell.  The synthesis-slice row is the 8-trap slice ($n{=}24$ per",
            r"condition) and is not pooled with the 22-trap fold.}",
            r"\label{tab:taxonomyResults}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def render_ladder_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{lrrrl}",
        r"\toprule",
        r"Condition & Seed 1 & Seed 2 & Seed 3 & Pooled S3 (95\% CI) \\",
        r"\midrule",
    ]
    for row in rows:
        per_seed = row["per_seed"]
        lines.append(
            f"{row['label_tex']}         & "
            f"{fmt_count_rate(per_seed['1'])} & {fmt_count_rate(per_seed['2'])} & "
            f"{fmt_count_rate(per_seed['3'])} & {fmt_count_rate_ci(row['pooled_s3'])} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"}%",
            r"\caption{Ablation ladder: verbatim raw-evidence retention vs.\ typed consolidation pipeline,",
            r"  22 sequences $\times$ 3 seeds.",
            r"  B5 is the verbatim-event-memory reference; per-seed S3 is identical across seeds (pre-registered",
            r"  falsifier: PASS), consistent with absence of contamination drift.",
            r"  Probe typed-only = typed consolidation pipeline only, no raw-evidence capsule.",
            r"  Probe raw-only = raw-evidence capsule only, no typed pipeline.",
            r"  Probe hybrid = both.",
            r"  Source: \texttt{CONFIRMATORY-FOLD.md}, Ablation Ladder table.}",
            r"\label{tab:ladder}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def render_mcnemar_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{lrrrrlrr}",
        r"\toprule",
        r"Comparison & Paired $n$ & $b$ & $c$ & Disc. & Paired RD (95\% CI) & Exact $p$ & Holm $p$ \\",
        r"\midrule",
    ]
    for row in rows:
        holm = row.get("holm_adjusted_p")
        holm_cell = fmt_holm_p(holm, bool(row.get("holm_reject_0_05")))
        lines.append(
            f"{row['label_tex']}             & {row['paired_n']} & {row['b_first_wins']:>2} & "
            f"{row['c_second_wins']:>1} & {row['discordant_n']:>2} & "
            f"{fmt_signed_ci(row['paired_risk_difference'], row['paired_risk_difference_ci_95'])} & "
            f"{fmt_p_tex(row['mcnemar_exact_p'])} & {holm_cell} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"}%",
            r"\caption{Exact pooled McNemar family with Holm correction ($\alpha{=}0.05$).",
            r"  $b$ = first condition wins, $c$ = second condition wins, Disc.\ = discordant pairs.",
            r"  $^{**}$ Holm $p{<}0.001$.  All comparisons except reference-probe typed-only vs.\ B3 are",
            r"  non-significant after Holm correction in the pooled seed-trap-cell analysis.",
            r"  Source: \texttt{CONFIRMATORY-FOLD.md}, Exact McNemar Family table.}",
            r"\label{tab:mcnemar}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def render_pipeline_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{llrrrl}",
        r"\toprule",
        r"Condition & Component removed or changed & Seed 1 & Seed 2 & Seed 3 & Pooled S3 (95\% CI) \\",
        r"\midrule",
    ]
    for row in rows:
        per_seed = row["per_seed"]
        lines.append(
            f"{row['label_tex']} & {row['component']} & {fmt_count_rate(per_seed['1'])} & "
            f"{fmt_count_rate(per_seed['2'])} & {fmt_count_rate(per_seed['3'])} & "
            f"{fmt_count_rate_ci(row['pooled_s3'])} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"}%",
            r"\caption{Pipeline component ablations for S3 pass@1.  Source:",
            r"  \texttt{CONFIRMATORY-FOLD.md}, Per-Condition S3 Pass Rates.}",
            r"\label{tab:pipelineAblations}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def render_stats_tex(mcnemar_rows: list[dict[str, Any]], supplemental_rows: list[dict[str, Any]]) -> str:
    lines = [
        "% Generated by scripts/generate_tables.py from analysis/fold/confirmatory.json.",
        render_mcnemar_table(mcnemar_rows),
    ]
    if supplemental_rows:
        lines.append(render_supplemental_mcnemar_table(supplemental_rows))
    return "\n\n".join(lines)


def render_supplemental_mcnemar_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{lrrrrlr}",
        r"\toprule",
        r"Comparison & Paired $n$ & $b$ & $c$ & Disc. & Paired RD (95\% CI) & Exact $p$ \\",
        r"\midrule",
    ]
    for row in rows:
        lines.append(
            f"{row['label_tex']} & {row['paired_n']} & {row['b_first_wins']} & "
            f"{row['c_second_wins']} & {row['discordant_n']} & "
            f"{fmt_signed_ci(row['paired_risk_difference'], row['paired_risk_difference_ci_95'])} & "
            f"{fmt_p_tex(row['mcnemar_exact_p'])} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"}%",
            r"\caption{Supplemental exact McNemar comparison outside the pre-registered Holm family.}",
            r"\label{tab:mcnemar-supplemental-mem0}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def fmt_count_rate(item: Mapping[str, Any]) -> str:
    return f"{int(item['numerator'])}/{int(item['denominator'])} ({fmt_rate(item['value'])})"


def fmt_count_rate_ci(item: Mapping[str, Any]) -> str:
    return (
        f"{int(item['numerator'])}/{int(item['denominator'])} "
        f"({fmt_rate(item['value'])}) {fmt_ci(item['display_ci_95'])}"
    )


def fmt_rate(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):.3f}"


def fmt_signed(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):+.3f}"


def fmt_ci(ci: Any) -> str:
    if not (
        isinstance(ci, list)
        and len(ci) == 2
        and isinstance(ci[0], (int, float))
        and isinstance(ci[1], (int, float))
    ):
        return "[n/a, n/a]"
    return f"[{float(ci[0]):.3f}, {float(ci[1]):.3f}]"


def fmt_signed_ci(value: Any, ci: Any) -> str:
    return f"{fmt_signed(value)} {fmt_ci(ci)}"


def fmt_p_tex(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    p = float(value)
    if p < 1e-4:
        exponent = int(math.floor(math.log10(p)))
        mantissa = p / (10**exponent)
        return rf"${mantissa:.1f}{{\times}}10^{{{exponent}}}$"
    if p < 0.001:
        return f"{p:.4f}"
    return f"{p:.3f}"


def fmt_holm_p(value: Any, reject: bool) -> str:
    text = fmt_p_tex(value)
    if reject and isinstance(value, (int, float)) and float(value) < 0.001:
        return text + r"\,$^{**}$"
    return text


def mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = payload.get(key)
    if not isinstance(item, Mapping):
        raise SystemExit(f"missing object key: {key}")
    return item


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def latex_escape(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("_", r"\_")
        .replace("%", r"\%")
        .replace("&", r"\&")
    )


def print_summary(rendered: Mapping[str, Any], *, output_dir: Path, wrote: bool) -> None:
    final_tables = rendered["final_tables_json"]
    stats = rendered["stats_json"]
    print("=== DreamBench-SWE confirmatory table renderer ===")
    print("input=analysis/fold/confirmatory.json")
    print("live_results_scanned=no")
    print("pass_rate_ci=Wilson score 95%")
    print("paired_rd_ci=exact conditional matched-pair 95%")
    print("")
    print("=== Sanity rows ===")
    for condition in ("B5", "DF-hybrid", "DF", "B3"):
        row = final_tables[condition]
        p = row["pass_at_1_rate"]
        print(
            f"{condition}: {p['numerator']}/{p['denominator']}="
            f"{fmt_rate(p['value'])} {fmt_ci(p['display_ci_95'])}"
        )
    synth = next(row for row in stats["taxonomy_results"] if row["trap_class"] == "Synthesis slice")
    b5 = synth["conditions"]["B5"]["pass_at_1"]
    print(f"synth B5: {b5['numerator']}/{b5['denominator']}={fmt_rate(b5['value'])} {fmt_ci(b5['display_ci_95'])}")
    print("")
    print("=== McNemar paired RD ===")
    for row in stats["mcnemar_family"]:
        print(
            f"{row['comparison']}: n={row['paired_n']} b={row['b_first_wins']} c={row['c_second_wins']} "
            f"RD={fmt_signed_ci(row['paired_risk_difference'], row['paired_risk_difference_ci_95'])} "
            f"exact_p={row['mcnemar_exact_p']} holm_p={row.get('holm_adjusted_p')}"
        )
    if wrote:
        for name in ("final_tables.json", "final_tables.tex", "stats.json", "stats.tex"):
            print(f"wrote {output_dir / name}")
    else:
        print("no_write=yes")


if __name__ == "__main__":
    raise SystemExit(main())
