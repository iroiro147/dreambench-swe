#!/usr/bin/env python3
"""V2 clustered confirmatory analyzer.

The frozen primary test is a trap-clustered exact sign/permutation test over a
pre-specified condition pair in a 60 trap x 3 seed S3 grid.  The implementation
uses dynamic programming over trap sign flips, so the p-value is exact and does
not depend on system randomness.  A permutation seed is still accepted and
recorded to make the analysis invocation explicit and reproducible.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import analyze_confirmatory as confirmatory
except ImportError:  # pragma: no cover - used when executed as scripts/foo.py
    import analyze_confirmatory as confirmatory  # type: ignore


ALPHA = 0.05
EXPECTED_SEEDS = (1, 2, 3)
EXPECTED_TRAPS = 60
DEFAULT_FAMILY = tuple(confirmatory.MCNEMAR_FAMILY)
DEFAULT_PRIMARY_PAIR = DEFAULT_FAMILY[0]
STRATUM_BASELINE_CONDITIONS = tuple("B%d" % index for index in range(8))
STRATUM_VALIDITY_STRATA = ("C2", "C3", "C5", "C6", "C7", "C9")
STRATUM_B0_HEADROOM_STRATA = STRATUM_VALIDITY_STRATA + ("C10",)
STRATUM_FLOOR_THRESHOLD = 0.15
B0_HEADROOM_PREFERRED = 0.40
B0_HEADROOM_MAXIMUM = 0.50
STRATUM_PASS_KEYS = (
    "s3_pass_at_1",
    "pass_at_1",
    "passed",
    "s3_passed",
    "success",
    "final_passed",
)


Pair = tuple[str, str]
CellMap = dict[tuple[int, str], bool]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    analysis = json.loads(Path(args.analysis_json).read_text(encoding="utf-8"))
    family = tuple(tuple(item) for item in args.family) if args.family else DEFAULT_FAMILY
    primary_pair = tuple(args.primary_pair) if args.primary_pair else DEFAULT_PRIMARY_PAIR
    report = analyze_v2(
        analysis,
        primary_pair=primary_pair,
        family=family,
        permutation_seed=args.permutation_seed,
        require_complete_grid=not args.allow_incomplete_grid,
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output_json:
        Path(args.output_json).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the DreamBench-SWE v2 clustered confirmatory analysis."
    )
    parser.add_argument(
        "--analysis-json",
        required=True,
        help="Path to a confirmatory analysis JSON with conditions.<name>.s3_cells.",
    )
    parser.add_argument(
        "--permutation-seed",
        required=True,
        type=int,
        help="Recorded deterministic seed for the permutation analysis invocation.",
    )
    parser.add_argument(
        "--primary-pair",
        nargs=2,
        metavar=("FIRST", "SECOND"),
        default=None,
        help="Primary pre-specified condition pair. Default: first pair in the family.",
    )
    parser.add_argument(
        "--family",
        nargs=2,
        metavar=("FIRST", "SECOND"),
        action="append",
        default=None,
        help="Pre-specified Holm family pair. Repeat for each pair; default is the confirmatory family.",
    )
    parser.add_argument(
        "--allow-incomplete-grid",
        action="store_true",
        help="Do not enforce the frozen 60 trap x 3 seed paired grid.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write the JSON analysis.",
    )
    return parser.parse_args(argv)


def analyze_v2(
    analysis: dict[str, Any],
    *,
    primary_pair: Pair = DEFAULT_PRIMARY_PAIR,
    family: Sequence[Pair] = DEFAULT_FAMILY,
    permutation_seed: int,
    expected_traps: int = EXPECTED_TRAPS,
    expected_seeds: Sequence[int] = EXPECTED_SEEDS,
    require_complete_grid: bool = True,
    alpha: float = ALPHA,
) -> dict[str, Any]:
    """Return the v2 clustered analysis for a pre-specified family."""
    normalized_family = normalize_family(family)
    normalized_primary = normalize_pair(primary_pair)
    if normalized_primary not in normalized_family:
        raise ValueError("primary_pair must be present in family")

    seeds = tuple(int(seed) for seed in expected_seeds)
    condition_names = sorted({name for pair in normalized_family for name in pair})
    cells_by_condition = {
        condition: condition_cells(analysis, condition)
        for condition in condition_names
    }

    comparisons = []
    for pair in normalized_family:
        comparisons.append(
            analyze_pair(
                pair,
                cells_by_condition[pair[0]],
                cells_by_condition[pair[1]],
                expected_traps=expected_traps,
                expected_seeds=seeds,
                require_complete_grid=require_complete_grid,
            )
        )

    apply_family_adjustments(comparisons, alpha=alpha)
    comparison_by_label = {item["comparison"]: item for item in comparisons}
    primary_label = comparison_label(*normalized_primary)
    primary = comparison_by_label[primary_label]

    return {
        "metadata": {
            "analysis": "DreamBench-SWE confirmatory v2 clustered analysis",
            "v2_live_results": "complete",
            "results_status": "complete",
            "primary_pair": list(normalized_primary),
            "family": [list(pair) for pair in normalized_family],
            "expected_traps": expected_traps,
            "expected_seeds": list(seeds),
            "permutation_seed": int(permutation_seed),
            "primary_test": (
                "trap-clustered exact sign/permutation test over trap scores; "
                "statistic is abs(sum_t (#first-only seeds - #second-only seeds))"
            ),
            "permutation_engine": (
                "exact dynamic programming over all trap label sign flips; no random "
                "or operating-system entropy is used"
            ),
            "secondary": "pooled exact McNemar over seed-trap cells for contrast",
            "alpha": alpha,
        },
        "primary": primary,
        "family": {
            "comparisons": comparisons,
        },
        "stratum_validity": compute_stratum_validity(analysis),
    }


def compute_stratum_validity(
    fold: Mapping[str, Any],
    *,
    strata: Sequence[str] = STRATUM_VALIDITY_STRATA,
    baseline_conditions: Sequence[str] = STRATUM_BASELINE_CONDITIONS,
    b0_headroom_strata: Sequence[str] = STRATUM_B0_HEADROOM_STRATA,
    floor_threshold: float = STRATUM_FLOOR_THRESHOLD,
    b0_headroom_preferred: float = B0_HEADROOM_PREFERRED,
    b0_headroom_maximum: float = B0_HEADROOM_MAXIMUM,
) -> dict[str, Any]:
    """Return the A7/F7 STRATUM-VALIDITY table for a v2 fold."""
    cells = stratum_cells(fold)
    rows = [
        stratum_validity_row(
            cells,
            stratum,
            baseline_conditions=baseline_conditions,
            floor_threshold=floor_threshold,
            b0_headroom_preferred=b0_headroom_preferred,
            b0_headroom_maximum=b0_headroom_maximum,
        )
        for stratum in strata
    ]
    headroom_rows = [
        b0_headroom_row(
            cells,
            stratum,
            b0_headroom_preferred=b0_headroom_preferred,
            b0_headroom_maximum=b0_headroom_maximum,
        )
        for stratum in b0_headroom_strata
    ]
    c10_headroom = next(
        (row for row in headroom_rows if row["stratum"] == "C10"),
        b0_headroom_row(
            [],
            "C10",
            b0_headroom_preferred=b0_headroom_preferred,
            b0_headroom_maximum=b0_headroom_maximum,
        ),
    )
    return {
        "table_name": "STRATUM-VALIDITY",
        "description": (
            "A7/F7 validity gate over anti-hoarding strata: max condition pass rate, "
            "B0-B7 spread, and B0 headroom including C10 abstention traps."
        ),
        "baseline_conditions": list(baseline_conditions),
        "strata": list(strata),
        "thresholds": {
            "max_condition_pass_rate_minimum": floor_threshold,
            "spread_minimum_exclusive": 0.0,
            "b0_headroom_preferred": b0_headroom_preferred,
            "b0_headroom_maximum": b0_headroom_maximum,
        },
        "rows": rows,
        "b0_headroom_rows": headroom_rows,
        "c10_b0_headroom_holds": c10_headroom["b0_headroom_holds"],
        "overall_pass": all(row["status"] == "PASS" for row in rows)
        and c10_headroom["b0_headroom_holds"],
        "floor_tied_strata": [
            row["stratum"]
            for row in rows
            if "floor-tie" in row["flags"]
        ],
        "failed_strata": [
            row["stratum"]
            for row in rows
            if row["status"] != "PASS"
        ],
    }


def stratum_validity_row(
    cells: Sequence[dict[str, Any]],
    stratum: str,
    *,
    baseline_conditions: Sequence[str],
    floor_threshold: float,
    b0_headroom_preferred: float,
    b0_headroom_maximum: float,
) -> dict[str, Any]:
    condition_rates = {
        condition: summarize_stratum_cells(cells, condition=condition, stratum=stratum)
        for condition in baseline_conditions
    }
    observed = {
        condition: summary["rate"]
        for condition, summary in condition_rates.items()
        if summary["rate"] is not None
    }
    missing = [
        condition
        for condition in baseline_conditions
        if condition not in observed
    ]
    max_rate = max(observed.values()) if observed else None
    min_rate = min(observed.values()) if observed else None
    spread = max_rate - min_rate if max_rate is not None and min_rate is not None else None
    max_conditions = [
        condition
        for condition in baseline_conditions
        if condition_rates[condition]["rate"] == max_rate
    ] if max_rate is not None else []
    min_conditions = [
        condition
        for condition in baseline_conditions
        if condition_rates[condition]["rate"] == min_rate
    ] if min_rate is not None else []
    max_threshold_holds = max_rate is not None and max_rate >= floor_threshold
    spread_holds = spread is not None and spread > 0.0
    all_at_or_near_floor = max_rate is not None and max_rate < floor_threshold
    headroom = b0_headroom_row(
        cells,
        stratum,
        b0_headroom_preferred=b0_headroom_preferred,
        b0_headroom_maximum=b0_headroom_maximum,
    )
    flags = []
    if spread == 0.0 or all_at_or_near_floor:
        flags.append("floor-tie")
    if missing:
        flags.append("missing-baselines")
    if not max_threshold_holds:
        flags.append("max-below-0.15")
    if not spread_holds:
        flags.append("no-spread")
    if not headroom["b0_headroom_holds"]:
        flags.append("b0-headroom-fail")
    status = (
        "PASS"
        if not missing
        and max_threshold_holds
        and spread_holds
        and headroom["b0_headroom_holds"]
        else "FAIL"
    )
    return {
        "stratum": stratum,
        "status": status,
        "max_condition": max_conditions[0] if max_conditions else None,
        "max_conditions": max_conditions,
        "max_condition_pass_rate": max_rate,
        "min_condition": min_conditions[0] if min_conditions else None,
        "min_conditions": min_conditions,
        "min_condition_pass_rate": min_rate,
        "spread": spread,
        "max_condition_threshold_holds": max_threshold_holds,
        "spread_holds": spread_holds,
        "b0_headroom_holds": headroom["b0_headroom_holds"],
        "b0_headroom_preferred_holds": headroom["b0_headroom_preferred_holds"],
        "b0_pass_rate": headroom["b0_pass_rate"],
        "b0_passed": headroom["b0_passed"],
        "b0_n": headroom["b0_n"],
        "missing_baseline_conditions": missing,
        "flags": flags,
        "condition_pass_rates": condition_rates,
    }


def b0_headroom_row(
    cells: Sequence[dict[str, Any]],
    stratum: str,
    *,
    b0_headroom_preferred: float,
    b0_headroom_maximum: float,
) -> dict[str, Any]:
    summary = summarize_stratum_cells(cells, condition="B0", stratum=stratum)
    rate = summary["rate"]
    return {
        "stratum": stratum,
        "b0_passed": summary["passed"],
        "b0_n": summary["n"],
        "b0_pass_rate": rate,
        "b0_headroom_preferred_holds": rate is not None and rate <= b0_headroom_preferred,
        "b0_headroom_holds": rate is not None and rate <= b0_headroom_maximum,
    }


def summarize_stratum_cells(
    cells: Sequence[dict[str, Any]],
    *,
    condition: str,
    stratum: str,
) -> dict[str, Any]:
    values = [
        int(cell["passed"])
        for cell in cells
        if cell["condition"] == condition and cell["construct_label"] == stratum
    ]
    passed = sum(values)
    n = len(values)
    return {
        "passed": passed,
        "n": n,
        "rate": passed / n if n else None,
    }


def stratum_cells(fold: Mapping[str, Any]) -> list[dict[str, Any]]:
    conditions = fold.get("conditions")
    if isinstance(conditions, Mapping):
        cells: list[dict[str, Any]] = []
        for raw_condition, condition_payload in sorted(conditions.items()):
            if isinstance(condition_payload, Mapping):
                cells.extend(
                    stratum_cells_from_condition(
                        canonical_stratum_condition(str(raw_condition)),
                        condition_payload,
                        fold,
                    )
                )
        return cells

    records = fold.get("records") or fold.get("cells") or fold.get("s3_cells")
    if isinstance(records, list):
        return [
            cell
            for record in records
            for cell in [stratum_cell_from_record(record, fold)]
            if cell is not None
        ]
    return []


def stratum_cells_from_condition(
    condition: str,
    condition_payload: Mapping[str, Any],
    fold: Mapping[str, Any],
) -> list[dict[str, Any]]:
    backbone_maps = condition_payload.get("backbones") or condition_payload.get("by_backbone")
    if isinstance(backbone_maps, Mapping):
        cells: list[dict[str, Any]] = []
        for backbone_payload in backbone_maps.values():
            if isinstance(backbone_payload, Mapping):
                cells.extend(stratum_cells_from_condition(condition, backbone_payload, fold))
        return cells

    records = condition_payload.get("records") or condition_payload.get("cells")
    if isinstance(records, list):
        return [
            cell
            for record in records
            for cell in [
                stratum_cell_from_record(
                    record,
                    fold,
                    default_condition=condition,
                )
            ]
            if cell is not None
        ]

    traps = (
        condition_payload.get("traps")
        or condition_payload.get("trap_results")
        or condition_payload.get("s3_by_trap")
        or condition_payload.get("s3_cells")
    )
    if not isinstance(traps, Mapping):
        return []

    cells: list[dict[str, Any]] = []
    for raw_key, raw_payload in sorted(traps.items()):
        key = str(raw_key)
        if ":" in key and key.startswith("seed"):
            seed, trap_id = parse_cell_key(key)
            passed = coerce_stratum_pass(raw_payload)
            if passed is None:
                continue
            cells.append(
                make_stratum_cell(
                    condition=condition,
                    trap_id=trap_id,
                    seed=str(seed),
                    payload=raw_payload,
                    passed=passed,
                )
            )
            continue

        if not isinstance(raw_payload, Mapping):
            continue
        trap_id = str(raw_payload.get("trap_id") or raw_payload.get("seq_id") or key)
        seed_records = raw_payload.get("seeds") or raw_payload.get("per_seed") or raw_payload.get("seed_results")
        if isinstance(seed_records, Mapping):
            for raw_seed, seed_payload in sorted(seed_records.items(), key=lambda item: str(item[0])):
                passed = coerce_stratum_pass(seed_payload)
                if passed is None:
                    continue
                cells.append(
                    make_stratum_cell(
                        condition=condition,
                        trap_id=trap_id,
                        seed=str(raw_seed),
                        payload=seed_payload,
                        passed=passed,
                        trap_payload=raw_payload,
                    )
                )
            continue

        passed = coerce_stratum_pass(raw_payload)
        if passed is None:
            continue
        seed = raw_payload.get("seed") or raw_payload.get("random_seed") or "pooled"
        cells.append(
            make_stratum_cell(
                condition=condition,
                trap_id=trap_id,
                seed=str(seed),
                payload=raw_payload,
                passed=passed,
            )
        )
    return cells


def stratum_cell_from_record(
    record: Any,
    fold: Mapping[str, Any],
    *,
    default_condition: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    condition_value = record.get("condition") or default_condition
    trap_value = record.get("trap_id") or record.get("seq_id") or record.get("sequence_id")
    passed = coerce_stratum_pass(record)
    if condition_value is None or trap_value is None or passed is None:
        return None
    return make_stratum_cell(
        condition=canonical_stratum_condition(str(condition_value)),
        trap_id=str(trap_value),
        seed=str(record.get("seed") or record.get("random_seed") or "pooled"),
        payload=record,
        passed=passed,
        fold=fold,
    )


def make_stratum_cell(
    *,
    condition: str,
    trap_id: str,
    seed: str,
    payload: Any,
    passed: int,
    trap_payload: Mapping[str, Any] | None = None,
    fold: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "condition": canonical_stratum_condition(condition),
        "trap_id": trap_id,
        "seed": seed,
        "passed": passed,
        "construct_label": construct_label_for_cell(payload, trap_id, trap_payload=trap_payload, fold=fold),
    }


def construct_label_for_cell(
    payload: Any,
    trap_id: str,
    *,
    trap_payload: Mapping[str, Any] | None = None,
    fold: Mapping[str, Any] | None = None,
) -> str:
    for source in (payload, trap_payload):
        if isinstance(source, Mapping):
            value = source.get("construct_label") or source.get("construct") or source.get("construct_id")
            if value:
                return normalize_construct_label(str(value))
    if fold is not None:
        trap_metadata = fold.get("traps") or fold.get("trap_metadata") or fold.get("sequences")
        if isinstance(trap_metadata, Mapping):
            item = trap_metadata.get(trap_id)
            if isinstance(item, Mapping):
                value = item.get("construct_label") or item.get("construct") or item.get("construct_id")
                if value:
                    return normalize_construct_label(str(value))
    return infer_construct_label_from_trap_id(trap_id)


def normalize_construct_label(value: str) -> str:
    text = value.upper().replace(" ", "").replace("_", "")
    if text.startswith("CONSTRUCT"):
        text = text.replace("CONSTRUCT", "C", 1)
    return text


def infer_construct_label_from_trap_id(trap_id: str) -> str:
    for part in trap_id.replace("_", "-").split("-"):
        label = normalize_construct_label(part)
        if label.startswith("C") and label[1:].isdigit():
            return label
    return "UNKNOWN"


def coerce_stratum_pass(payload: Any) -> int | None:
    if isinstance(payload, bool):
        return 1 if payload else 0
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return int(payload) if float(payload) in (0.0, 1.0) else None
    if not isinstance(payload, Mapping):
        return None
    for key in STRATUM_PASS_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value) if float(value) in (0.0, 1.0) else None
    return None


def canonical_stratum_condition(condition: str) -> str:
    aliases = {
        "DF": "DF-typed-only",
        "DF-typed": "DF-typed-only",
        "DF_TYPED_ONLY": "DF-typed-only",
    }
    return aliases.get(condition, condition)


def normalize_family(family: Sequence[Pair]) -> tuple[Pair, ...]:
    normalized = tuple(normalize_pair(pair) for pair in family)
    if not normalized:
        raise ValueError("family must contain at least one pair")
    if len(set(normalized)) != len(normalized):
        raise ValueError("family contains duplicate pairs")
    return normalized


def normalize_pair(pair: Sequence[str]) -> Pair:
    if len(pair) != 2:
        raise ValueError("condition pair must contain exactly two names")
    first, second = str(pair[0]), str(pair[1])
    if not first or not second:
        raise ValueError("condition names must be non-empty")
    return first, second


def condition_cells(analysis: dict[str, Any], condition: str) -> CellMap:
    conditions = analysis.get("conditions")
    if not isinstance(conditions, dict) or condition not in conditions:
        raise KeyError("missing condition in analysis: %s" % condition)
    summary = conditions[condition]
    if not isinstance(summary, dict):
        raise TypeError("condition summary is not an object: %s" % condition)
    raw_cells = summary.get("s3_cells")
    if not isinstance(raw_cells, dict):
        raise KeyError("missing s3_cells for condition: %s" % condition)

    cells: CellMap = {}
    for key, cell in raw_cells.items():
        seed, trap = parse_cell_key(str(key))
        if isinstance(cell, dict):
            if "passed" not in cell:
                raise KeyError("cell is missing passed field: %s %s" % (condition, key))
            passed = bool(cell["passed"])
        else:
            passed = bool(cell)
        cells[(seed, trap)] = passed
    return cells


def parse_cell_key(key: str) -> tuple[int, str]:
    seed_part, trap = key.split(":", 1)
    if not seed_part.startswith("seed") or not trap:
        raise ValueError("unexpected cell key: %s" % key)
    return int(seed_part[4:]), trap


def analyze_pair(
    pair: Pair,
    first_cells: CellMap,
    second_cells: CellMap,
    *,
    expected_traps: int,
    expected_seeds: Sequence[int],
    require_complete_grid: bool,
) -> dict[str, Any]:
    first, second = pair
    traps = sorted({trap for _, trap in first_cells} | {trap for _, trap in second_cells})
    seed_set = tuple(int(seed) for seed in expected_seeds)
    if require_complete_grid:
        validate_complete_grid(
            pair,
            first_cells,
            second_cells,
            traps,
            expected_traps=expected_traps,
            expected_seeds=seed_set,
        )

    clustered = trap_clustered_exact_sign_permutation(first_cells, second_cells, traps, seed_set)
    pooled = pooled_mcnemar(first_cells, second_cells, traps, seed_set)
    per_seed = per_seed_sensitivity(first_cells, second_cells, traps, seed_set)
    majority = majority_collapse_sensitivity(first_cells, second_cells, traps, seed_set)

    return {
        "comparison": comparison_label(first, second),
        "first": first,
        "second": second,
        "grid": {
            "trap_count": clustered["clusters"],
            "expected_traps": expected_traps,
            "expected_seeds": list(seed_set),
            "paired_seed_cells": clustered["paired_seed_cells"],
            "complete_60x3": clustered["clusters"] == expected_traps
            and all(value == len(seed_set) for value in clustered["paired_seed_count_by_trap"].values()),
            "missing_first_cells": missing_cell_keys(first_cells, second_cells),
            "missing_second_cells": missing_cell_keys(second_cells, first_cells),
        },
        "primary_test": clustered,
        "sensitivity": {
            "per_seed": per_seed,
            "majority_collapse": majority,
        },
        "secondary": {
            "pooled_mcnemar": pooled,
        },
    }


def validate_complete_grid(
    pair: Pair,
    first_cells: CellMap,
    second_cells: CellMap,
    traps: Sequence[str],
    *,
    expected_traps: int,
    expected_seeds: Sequence[int],
) -> None:
    expected_seed_set = set(expected_seeds)
    unexpected_seeds = sorted(
        ({seed for seed, _ in first_cells} | {seed for seed, _ in second_cells}) - expected_seed_set
    )
    if unexpected_seeds:
        raise ValueError("%s has unexpected seeds: %s" % (comparison_label(*pair), unexpected_seeds))
    paired_seed_counts = paired_seed_count_by_trap(first_cells, second_cells, traps, expected_seeds)
    incomplete = {
        trap: count
        for trap, count in paired_seed_counts.items()
        if count != len(expected_seeds)
    }
    if len(traps) != expected_traps or incomplete:
        raise ValueError(
            "%s must have a complete %d trap x %d seed paired grid; found %d traps and incomplete counts %s"
            % (
                comparison_label(*pair),
                expected_traps,
                len(expected_seeds),
                len(traps),
                dict(sorted(incomplete.items())),
            )
        )


def trap_clustered_exact_sign_permutation(
    first_cells: CellMap,
    second_cells: CellMap,
    traps: Sequence[str],
    seeds: Sequence[int],
) -> dict[str, Any]:
    cluster_scores = []
    paired_counts = {}
    totals = empty_counts()
    for trap in traps:
        score = 0
        paired_in_trap = 0
        for seed in seeds:
            key = (seed, trap)
            if key not in first_cells or key not in second_cells:
                continue
            paired_in_trap += 1
            first_passed = first_cells[key]
            second_passed = second_cells[key]
            if first_passed and not second_passed:
                score += 1
                totals["b_first_wins"] += 1
            elif second_passed and not first_passed:
                score -= 1
                totals["c_second_wins"] += 1
            elif first_passed and second_passed:
                totals["both_pass"] += 1
            else:
                totals["both_fail"] += 1
        if paired_in_trap:
            cluster_scores.append(score)
            paired_counts[trap] = paired_in_trap

    signed = sum(cluster_scores)
    statistic = abs(signed)
    p_value = exact_signflip_p(cluster_scores)
    return {
        "test": "trap_clustered_exact_sign_permutation",
        "clusters": len(cluster_scores),
        "paired_seed_cells": sum(paired_counts.values()),
        "b_first_wins": totals["b_first_wins"],
        "c_second_wins": totals["c_second_wins"],
        "discordant_n": totals["b_first_wins"] + totals["c_second_wins"],
        "both_pass": totals["both_pass"],
        "both_fail": totals["both_fail"],
        "signed_statistic": signed,
        "statistic": statistic,
        "permutation_p": p_value,
        "cluster_scores": cluster_scores,
        "cluster_score_counts": dict(sorted(Counter(cluster_scores).items())),
        "paired_seed_count_by_trap": dict(sorted(paired_counts.items())),
        "paired_seed_count_counts": dict(sorted(Counter(paired_counts.values()).items())),
    }


def pooled_mcnemar(
    first_cells: CellMap,
    second_cells: CellMap,
    traps: Sequence[str],
    seeds: Sequence[int],
) -> dict[str, Any]:
    counts = empty_counts()
    paired_n = 0
    for trap in traps:
        for seed in seeds:
            key = (seed, trap)
            if key not in first_cells or key not in second_cells:
                continue
            paired_n += 1
            increment_pair_counts(counts, first_cells[key], second_cells[key])
    p_value = confirmatory.mcnemar_exact_p(counts["b_first_wins"], counts["c_second_wins"])
    return {
        "test": "pooled_exact_mcnemar",
        "paired_n": paired_n,
        **counts,
        "discordant_n": counts["b_first_wins"] + counts["c_second_wins"],
        "exact_p": p_value,
    }


def per_seed_sensitivity(
    first_cells: CellMap,
    second_cells: CellMap,
    traps: Sequence[str],
    seeds: Sequence[int],
) -> list[dict[str, Any]]:
    results = []
    for seed in seeds:
        counts = empty_counts()
        paired_n = 0
        for trap in traps:
            key = (seed, trap)
            if key not in first_cells or key not in second_cells:
                continue
            paired_n += 1
            increment_pair_counts(counts, first_cells[key], second_cells[key])
        results.append(
            {
                "seed": seed,
                "paired_n": paired_n,
                **counts,
                "discordant_n": counts["b_first_wins"] + counts["c_second_wins"],
                "exact_p": confirmatory.mcnemar_exact_p(
                    counts["b_first_wins"],
                    counts["c_second_wins"],
                ),
            }
        )
    return results


def majority_collapse_sensitivity(
    first_cells: CellMap,
    second_cells: CellMap,
    traps: Sequence[str],
    seeds: Sequence[int],
) -> dict[str, Any]:
    counts = empty_counts()
    used_traps = []
    excluded_traps = []
    for trap in traps:
        first_votes = [first_cells[(seed, trap)] for seed in seeds if (seed, trap) in first_cells]
        second_votes = [second_cells[(seed, trap)] for seed in seeds if (seed, trap) in second_cells]
        first_majority = complete_seed_majority(first_votes, seeds)
        second_majority = complete_seed_majority(second_votes, seeds)
        if first_majority is None or second_majority is None:
            excluded_traps.append(trap)
            continue
        used_traps.append(trap)
        increment_pair_counts(counts, first_majority, second_majority)
    return {
        "test": "trap_majority_collapse_exact_mcnemar",
        "trap_n": len(used_traps),
        **counts,
        "discordant_n": counts["b_first_wins"] + counts["c_second_wins"],
        "exact_p": confirmatory.mcnemar_exact_p(counts["b_first_wins"], counts["c_second_wins"]),
        "excluded_traps": excluded_traps,
    }


def apply_family_adjustments(comparisons: list[dict[str, Any]], *, alpha: float) -> None:
    clustered_p = {
        item["comparison"]: item["primary_test"]["permutation_p"]
        for item in comparisons
    }
    pooled_p = {
        item["comparison"]: item["secondary"]["pooled_mcnemar"]["exact_p"]
        for item in comparisons
    }
    majority_p = {
        item["comparison"]: item["sensitivity"]["majority_collapse"]["exact_p"]
        for item in comparisons
    }
    clustered_holm = holm_adjust(clustered_p, alpha=alpha)
    pooled_holm = holm_adjust(pooled_p, alpha=alpha)
    majority_holm = holm_adjust(majority_p, alpha=alpha)

    per_seed_p: dict[int, dict[str, float]] = {}
    for item in comparisons:
        for seed_item in item["sensitivity"]["per_seed"]:
            per_seed_p.setdefault(seed_item["seed"], {})[item["comparison"]] = seed_item["exact_p"]
    per_seed_holm = {
        seed: holm_adjust(values, alpha=alpha)
        for seed, values in per_seed_p.items()
    }

    for item in comparisons:
        label = item["comparison"]
        attach_holm(item["primary_test"], clustered_holm[label])
        attach_holm(item["secondary"]["pooled_mcnemar"], pooled_holm[label])
        attach_holm(item["sensitivity"]["majority_collapse"], majority_holm[label])
        item["statistic"] = item["primary_test"]["statistic"]
        item["permutation_p"] = item["primary_test"]["permutation_p"]
        item["holm_adjusted_p"] = item["primary_test"]["holm_adjusted_p"]
        item["holm_reject_0_05"] = item["primary_test"]["holm_reject_0_05"]
        for seed_item in item["sensitivity"]["per_seed"]:
            attach_holm(seed_item, per_seed_holm[seed_item["seed"]][label])


def holm_adjust(p_values: dict[str, float], *, alpha: float) -> dict[str, dict[str, Any]]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, dict[str, Any]] = {}
    running_max = 0.0
    m = len(ordered)
    for index, (key, p_value) in enumerate(ordered):
        raw_adjusted = min(1.0, (m - index) * float(p_value))
        running_max = max(running_max, raw_adjusted)
        adjusted[key] = {
            "raw_p": float(p_value),
            "holm_adjusted_p": running_max,
            "holm_reject_0_05": running_max <= alpha,
        }
    return adjusted


def attach_holm(target: dict[str, Any], holm: dict[str, Any]) -> None:
    target["holm_adjusted_p"] = holm["holm_adjusted_p"]
    target["holm_reject_0_05"] = holm["holm_reject_0_05"]


def exact_signflip_p(scores: Sequence[int]) -> float:
    if not scores:
        return 1.0
    observed = abs(sum(scores))
    if observed == 0:
        return 1.0
    distribution: Counter[int] = Counter({0: 1})
    for score in scores:
        next_distribution: Counter[int] = Counter()
        for current, count in distribution.items():
            next_distribution[current + score] += count
            next_distribution[current - score] += count
        distribution = next_distribution
    total = sum(distribution.values())
    extreme = sum(count for value, count in distribution.items() if abs(value) >= observed)
    return extreme / float(total)


def paired_seed_count_by_trap(
    first_cells: CellMap,
    second_cells: CellMap,
    traps: Sequence[str],
    seeds: Sequence[int],
) -> dict[str, int]:
    out = {}
    for trap in traps:
        out[trap] = sum(
            1
            for seed in seeds
            if (seed, trap) in first_cells and (seed, trap) in second_cells
        )
    return out


def complete_seed_majority(votes: Sequence[bool], seeds: Sequence[int]) -> bool | None:
    if len(votes) != len(seeds):
        return None
    return sum(1 for value in votes if value) >= (len(seeds) // 2 + 1)


def empty_counts() -> dict[str, int]:
    return {
        "b_first_wins": 0,
        "c_second_wins": 0,
        "both_pass": 0,
        "both_fail": 0,
    }


def increment_pair_counts(counts: dict[str, int], first_passed: bool, second_passed: bool) -> None:
    if first_passed and not second_passed:
        counts["b_first_wins"] += 1
    elif second_passed and not first_passed:
        counts["c_second_wins"] += 1
    elif first_passed and second_passed:
        counts["both_pass"] += 1
    else:
        counts["both_fail"] += 1


def missing_cell_keys(reference: CellMap, other: CellMap) -> list[str]:
    return [
        cell_key(seed, trap)
        for seed, trap in sorted(set(other) - set(reference))
    ]


def cell_key(seed: int, trap: str) -> str:
    return "seed%d:%s" % (seed, trap)


def comparison_label(first: str, second: str) -> str:
    return "%s vs %s" % (first, second)


if __name__ == "__main__":
    raise SystemExit(main())
