#!/usr/bin/env python3
"""Clustered sensitivity analysis for the confirmatory McNemar family.

This script reads the frozen confirmatory fold artifact and recomputes the
pre-registered five-comparison McNemar family under seed/trap clustering
checks. It uses only the Python standard library.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIRMATORY_JSON = ROOT / "analysis" / "fold" / "confirmatory.json"
CONFIRMATORY_REPORT = ROOT / "analysis" / "investigation-evidence" / "CONFIRMATORY-FOLD.md"
OUTPUT_REPORT = ROOT / "analysis" / "investigation-evidence" / "CLUSTERED-STATS.md"

ALPHA = 0.05
EXPECTED_SEEDS = (1, 2, 3)


def main() -> int:
    analysis, fold_report_text = load_sources()
    report = build_report(analysis, fold_report_text)
    OUTPUT_REPORT.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def load_sources() -> tuple[dict[str, Any], str]:
    analysis = json.loads(CONFIRMATORY_JSON.read_text(encoding="utf-8"))
    fold_report_text = CONFIRMATORY_REPORT.read_text(encoding="utf-8")
    return analysis, fold_report_text


def build_report(analysis: dict[str, Any], fold_report_text: str) -> str:
    if "## Exact McNemar Family" not in fold_report_text:
        raise RuntimeError("CONFIRMATORY-FOLD.md does not contain the published Exact McNemar Family section")

    family = family_comparisons(analysis)
    cell_maps = {
        condition: condition_cells(analysis, condition)
        for comparison in family
        for condition in comparison[:2]
    }
    pooled = pooled_results(analysis, family, cell_maps)
    per_seed = per_seed_results(family, cell_maps)
    trap_collapse = trap_collapse_results(family, cell_maps)
    clustered = clustered_results(family, cell_maps)

    lines: list[str] = []
    lines.append("# Clustered Statistical Sensitivity")
    lines.append("")
    lines.append("- Sources read: `analysis/fold/confirmatory.json` and `analysis/investigation-evidence/CONFIRMATORY-FOLD.md`.")
    lines.append("- The published pooled McNemar family in `CONFIRMATORY-FOLD.md` is cross-checked below from `confirmatory.json`.")
    lines.append("- Family: %s." % ", ".join(comparison_label(first, second) for first, second in family))
    lines.append("- Outcome: S3 `pass_at_1`, paired by `(seed, oracle_id/trap)`.")
    lines.append("- `b` means the first condition passes and the second fails; `c` means the second condition passes and the first fails.")
    lines.append("- `DF` is the typed-only DreamForge condition in the confirmatory fold.")
    lines.append("- Missingness: `DF` is missing `seed3:expr-stale-registry-stability` after the validity gate.")
    lines.append("")
    lines.append("## Test Definitions")
    lines.append("")
    lines.append("- Per-seed McNemar: exact two-sided McNemar within each seed; Holm adjustment is applied separately within each seed's five-comparison family.")
    lines.append("- Trap-level majority collapse: require all three seed outcomes per condition for a trap, majority-vote each condition, then run exact two-sided McNemar over complete trap pairs; Holm adjustment is across the five collapsed comparisons.")
    lines.append("- Trap-clustered sign/permutation test: for each trap, compute `d_t = #first-only seeds - #second-only seeds` over available paired seeds, then enumerate all `2^22` trap label swaps by exact sign-flip dynamic programming; the two-sided statistic is `abs(sum_t d_t)`. Holm adjustment is across the five clustered comparisons.")
    lines.append("")
    render_pooled_table(lines, pooled)
    lines.append("")
    render_per_seed_table(lines, per_seed)
    lines.append("")
    render_trap_collapse_table(lines, trap_collapse)
    lines.append("")
    render_clustered_table(lines, clustered)
    lines.append("")
    render_verdict_table(lines, family, pooled, per_seed, trap_collapse, clustered)
    lines.append("")
    render_plain_verdict(lines, pooled, per_seed, trap_collapse, clustered)
    return "\n".join(lines)


def family_comparisons(analysis: dict[str, Any]) -> list[tuple[str, str]]:
    comparisons = analysis["mcnemar_family"]["comparisons"]
    return [(str(item["first"]), str(item["second"])) for item in comparisons]


def condition_cells(analysis: dict[str, Any], condition: str) -> dict[tuple[int, str], bool]:
    raw_cells = analysis["conditions"][condition]["s3_cells"]
    out: dict[tuple[int, str], bool] = {}
    for key, cell in raw_cells.items():
        seed, trap = parse_cell_key(key)
        out[(seed, trap)] = bool(cell["passed"])
    return out


def parse_cell_key(key: str) -> tuple[int, str]:
    seed_part, trap = key.split(":", 1)
    if not seed_part.startswith("seed"):
        raise ValueError("unexpected cell key: %s" % key)
    return int(seed_part[4:]), trap


def comparison_label(first: str, second: str) -> str:
    return "%s vs %s" % (first, second)


def pooled_results(
    analysis: dict[str, Any],
    family: list[tuple[str, str]],
    cell_maps: dict[str, dict[tuple[int, str], bool]],
) -> dict[str, dict[str, Any]]:
    by_label: dict[str, dict[str, Any]] = {}
    json_by_label = {
        item["comparison"]: item
        for item in analysis["mcnemar_family"]["comparisons"]
    }
    for first, second in family:
        label = comparison_label(first, second)
        keys = sorted(set(cell_maps[first]) & set(cell_maps[second]))
        counts = paired_counts(cell_maps[first], cell_maps[second], keys)
        json_item = json_by_label[label]
        by_label[label] = {
            **counts,
            "exact_p": mcnemar_exact_p(counts["b"], counts["c"]),
            "json_exact_p": float(json_item["exact_p"]),
            "holm_p": float(json_item["holm_adjusted_p"]),
            "holm_reject": bool(json_item["holm_reject_0_05"]),
            "missing_first_cells": list(json_item.get("missing_first_cells") or []),
            "missing_second_cells": list(json_item.get("missing_second_cells") or []),
        }
    return by_label


def per_seed_results(
    family: list[tuple[str, str]],
    cell_maps: dict[str, dict[tuple[int, str], bool]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {comparison_label(first, second): {} for first, second in family}
    p_by_seed: dict[int, dict[str, float]] = {seed: {} for seed in EXPECTED_SEEDS}

    for first, second in family:
        label = comparison_label(first, second)
        paired = sorted(set(cell_maps[first]) & set(cell_maps[second]))
        for seed in EXPECTED_SEEDS:
            keys = [key for key in paired if key[0] == seed]
            counts = paired_counts(cell_maps[first], cell_maps[second], keys)
            p_value = mcnemar_exact_p(counts["b"], counts["c"])
            out[label][str(seed)] = {
                **counts,
                "exact_p": p_value,
            }
            p_by_seed[seed][label] = p_value

    for seed in EXPECTED_SEEDS:
        adjusted = holm_adjust(p_by_seed[seed])
        for label, item in adjusted.items():
            out[label][str(seed)]["holm_p"] = item["adjusted_p"]
            out[label][str(seed)]["holm_reject"] = item["reject_0_05"]
    return out


def trap_collapse_results(
    family: list[tuple[str, str]],
    cell_maps: dict[str, dict[tuple[int, str], bool]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    p_values: dict[str, float] = {}

    for first, second in family:
        label = comparison_label(first, second)
        first_cells = cell_maps[first]
        second_cells = cell_maps[second]
        traps = sorted({trap for _, trap in first_cells} | {trap for _, trap in second_cells})
        b = 0
        c = 0
        both_pass = 0
        both_fail = 0
        used_traps: list[str] = []
        excluded_traps: list[str] = []
        for trap in traps:
            first_votes = [first_cells[(seed, trap)] for seed in EXPECTED_SEEDS if (seed, trap) in first_cells]
            second_votes = [second_cells[(seed, trap)] for seed in EXPECTED_SEEDS if (seed, trap) in second_cells]
            first_majority = complete_three_seed_majority(first_votes)
            second_majority = complete_three_seed_majority(second_votes)
            if first_majority is None or second_majority is None:
                excluded_traps.append(trap)
                continue
            used_traps.append(trap)
            if first_majority and not second_majority:
                b += 1
            elif second_majority and not first_majority:
                c += 1
            elif first_majority and second_majority:
                both_pass += 1
            else:
                both_fail += 1
        p_value = mcnemar_exact_p(b, c)
        out[label] = {
            "n": len(used_traps),
            "b": b,
            "c": c,
            "discordant_n": b + c,
            "both_pass": both_pass,
            "both_fail": both_fail,
            "exact_p": p_value,
            "excluded_traps": excluded_traps,
        }
        p_values[label] = p_value

    adjusted = holm_adjust(p_values)
    for label, item in adjusted.items():
        out[label]["holm_p"] = item["adjusted_p"]
        out[label]["holm_reject"] = item["reject_0_05"]
    return out


def clustered_results(
    family: list[tuple[str, str]],
    cell_maps: dict[str, dict[tuple[int, str], bool]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    p_values: dict[str, float] = {}

    for first, second in family:
        label = comparison_label(first, second)
        first_cells = cell_maps[first]
        second_cells = cell_maps[second]
        traps = sorted({trap for _, trap in first_cells} | {trap for _, trap in second_cells})
        cluster_scores: list[int] = []
        paired_seed_counts: list[int] = []
        excluded_traps: list[str] = []
        b_total = 0
        c_total = 0
        paired_n = 0
        for trap in traps:
            score = 0
            paired_in_trap = 0
            for seed in EXPECTED_SEEDS:
                key = (seed, trap)
                if key not in first_cells or key not in second_cells:
                    continue
                paired_in_trap += 1
                paired_n += 1
                first_passed = first_cells[key]
                second_passed = second_cells[key]
                if first_passed and not second_passed:
                    score += 1
                    b_total += 1
                elif second_passed and not first_passed:
                    score -= 1
                    c_total += 1
            if paired_in_trap:
                cluster_scores.append(score)
                paired_seed_counts.append(paired_in_trap)
            else:
                excluded_traps.append(trap)
        p_value = exact_cluster_signflip_p(cluster_scores)
        out[label] = {
            "clusters": len(cluster_scores),
            "paired_n": paired_n,
            "b": b_total,
            "c": c_total,
            "observed_score": sum(cluster_scores),
            "abs_observed_score": abs(sum(cluster_scores)),
            "exact_p": p_value,
            "score_counts": dict(sorted(Counter(cluster_scores).items())),
            "paired_seed_count_counts": dict(sorted(Counter(paired_seed_counts).items())),
            "excluded_traps": excluded_traps,
        }
        p_values[label] = p_value

    adjusted = holm_adjust(p_values)
    for label, item in adjusted.items():
        out[label]["holm_p"] = item["adjusted_p"]
        out[label]["holm_reject"] = item["reject_0_05"]
    return out


def complete_three_seed_majority(votes: list[bool]) -> bool | None:
    if len(votes) != len(EXPECTED_SEEDS):
        return None
    return sum(1 for value in votes if value) >= 2


def paired_counts(
    first_cells: dict[tuple[int, str], bool],
    second_cells: dict[tuple[int, str], bool],
    keys: list[tuple[int, str]],
) -> dict[str, int]:
    b = 0
    c = 0
    both_pass = 0
    both_fail = 0
    for key in keys:
        first_passed = first_cells[key]
        second_passed = second_cells[key]
        if first_passed and not second_passed:
            b += 1
        elif second_passed and not first_passed:
            c += 1
        elif first_passed and second_passed:
            both_pass += 1
        else:
            both_fail += 1
    return {
        "n": len(keys),
        "b": b,
        "c": c,
        "discordant_n": b + c,
        "both_pass": both_pass,
        "both_fail": both_fail,
    }


def mcnemar_exact_p(b: int, c: int) -> float:
    discordant = int(b) + int(c)
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, i) for i in range(min(int(b), int(c)) + 1))
    return min(1.0, 2.0 * tail / float(2**discordant))


def exact_cluster_signflip_p(scores: list[int]) -> float:
    if not scores:
        return 1.0
    observed = abs(sum(scores))
    counts: Counter[int] = Counter({0: 1})
    for score in scores:
        next_counts: Counter[int] = Counter()
        for current, count in counts.items():
            next_counts[current + score] += count
            next_counts[current - score] += count
        counts = next_counts
    total = sum(counts.values())
    extreme = sum(count for value, count in counts.items() if abs(value) >= observed)
    return extreme / total


def holm_adjust(p_values: dict[str, float]) -> dict[str, dict[str, Any]]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    adjusted: dict[str, dict[str, Any]] = {}
    running_max = 0.0
    for index, (key, p_value) in enumerate(ordered):
        raw_adjusted = min(1.0, (m - index) * p_value)
        running_max = max(running_max, raw_adjusted)
        adjusted[key] = {
            "raw_p": p_value,
            "adjusted_p": running_max,
            "reject_0_05": running_max <= ALPHA,
        }
    return adjusted


def render_pooled_table(lines: list[str], pooled: dict[str, dict[str, Any]]) -> None:
    lines.append("## Published Pooled McNemar Cross-Check")
    lines.append("")
    lines.append(markdown_row(["Comparison", "paired n", "b", "c", "exact p", "Holm p", "Holm 0.05", "missing"]))
    lines.append(markdown_separator(8))
    for label, item in pooled.items():
        missing = []
        if item["missing_first_cells"]:
            missing.append("first missing: %s" % ", ".join(item["missing_first_cells"]))
        if item["missing_second_cells"]:
            missing.append("second missing: %s" % ", ".join(item["missing_second_cells"]))
        lines.append(markdown_row([
            label,
            str(item["n"]),
            str(item["b"]),
            str(item["c"]),
            fmt_p(item["exact_p"]),
            fmt_p(item["holm_p"]),
            yes_no(item["holm_reject"]),
            "; ".join(missing) if missing else "-",
        ]))


def render_per_seed_table(lines: list[str], per_seed: dict[str, dict[str, Any]]) -> None:
    lines.append("## Per-Seed Exact McNemar")
    lines.append("")
    lines.append(markdown_row(["Comparison", "seed", "paired n", "b", "c", "exact p", "seed-family Holm p", "Holm 0.05"]))
    lines.append(markdown_separator(8))
    for label, seed_items in per_seed.items():
        for seed in sorted(seed_items, key=int):
            item = seed_items[seed]
            lines.append(markdown_row([
                label,
                seed,
                str(item["n"]),
                str(item["b"]),
                str(item["c"]),
                fmt_p(item["exact_p"]),
                fmt_p(item["holm_p"]),
                yes_no(item["holm_reject"]),
            ]))


def render_trap_collapse_table(lines: list[str], trap_collapse: dict[str, dict[str, Any]]) -> None:
    lines.append("## Trap-Level Majority Collapse")
    lines.append("")
    lines.append(markdown_row(["Comparison", "trap n", "b", "c", "exact p", "Holm p", "Holm 0.05", "excluded traps"]))
    lines.append(markdown_separator(8))
    for label, item in trap_collapse.items():
        excluded = ", ".join(item["excluded_traps"]) if item["excluded_traps"] else "-"
        lines.append(markdown_row([
            label,
            str(item["n"]),
            str(item["b"]),
            str(item["c"]),
            fmt_p(item["exact_p"]),
            fmt_p(item["holm_p"]),
            yes_no(item["holm_reject"]),
            excluded,
        ]))


def render_clustered_table(lines: list[str], clustered: dict[str, dict[str, Any]]) -> None:
    lines.append("## Trap-Clustered Exact Sign/Permutation Test")
    lines.append("")
    lines.append(markdown_row([
        "Comparison",
        "clusters",
        "paired cells",
        "b",
        "c",
        "observed b-c",
        "cluster score counts",
        "paired seeds/cluster",
        "clustered p",
        "Holm p",
        "Holm 0.05",
    ]))
    lines.append(markdown_separator(11))
    for label, item in clustered.items():
        lines.append(markdown_row([
            label,
            str(item["clusters"]),
            str(item["paired_n"]),
            str(item["b"]),
            str(item["c"]),
            str(item["observed_score"]),
            fmt_counts(item["score_counts"]),
            fmt_counts(item["paired_seed_count_counts"]),
            fmt_p(item["exact_p"]),
            fmt_p(item["holm_p"]),
            yes_no(item["holm_reject"]),
        ]))


def render_verdict_table(
    lines: list[str],
    family: list[tuple[str, str]],
    pooled: dict[str, dict[str, Any]],
    per_seed: dict[str, dict[str, Any]],
    trap_collapse: dict[str, dict[str, Any]],
    clustered: dict[str, dict[str, Any]],
) -> None:
    lines.append("## Verdict Table")
    lines.append("")
    lines.append(markdown_row([
        "Comparison",
        "pooled Holm",
        "all per-seed Holm",
        "per-seed detail",
        "trap-collapse Holm",
        "clustered Holm",
        "conclusion vs pooled",
    ]))
    lines.append(markdown_separator(7))
    for first, second in family:
        label = comparison_label(first, second)
        pooled_reject = pooled[label]["holm_reject"]
        seed_rejects = [per_seed[label][str(seed)]["holm_reject"] for seed in EXPECTED_SEEDS]
        all_seed_reject = all(seed_rejects)
        trap_reject = trap_collapse[label]["holm_reject"]
        cluster_reject = clustered[label]["holm_reject"]
        variant_rejects = (all_seed_reject, trap_reject, cluster_reject)
        if all(value == pooled_reject for value in variant_rejects):
            conclusion = "unchanged"
        else:
            changed = []
            if all_seed_reject != pooled_reject:
                changed.append("per-seed-all")
            if trap_reject != pooled_reject:
                changed.append("trap-collapse")
            if cluster_reject != pooled_reject:
                changed.append("clustered")
            conclusion = "changes in %s" % ", ".join(changed)
        lines.append(markdown_row([
            label,
            yes_no(pooled_reject),
            yes_no(all_seed_reject),
            ", ".join("s%d=%s" % (seed, yes_no(per_seed[label][str(seed)]["holm_reject"])) for seed in EXPECTED_SEEDS),
            yes_no(trap_reject),
            yes_no(cluster_reject),
            conclusion,
        ]))


def render_plain_verdict(
    lines: list[str],
    pooled: dict[str, dict[str, Any]],
    per_seed: dict[str, dict[str, Any]],
    trap_collapse: dict[str, dict[str, Any]],
    clustered: dict[str, dict[str, Any]],
) -> None:
    lines.append("## Plain Verdict")
    lines.append("")
    df_b3 = "DF vs B3"
    hybrid_b5 = "DF-hybrid vs B5"

    lines.append("- `DF` typed-only vs `B3`: pooled Holm significance does not fully survive clustering sensitivity. It is significant in the original pooled test (Holm p=%s) and in the exact trap-clustered sign/permutation test (Holm p=%s), but it is not significant in all three per-seed tests (seed Holm p values: %s) and it is not significant after complete 3-seed trap-majority collapse (Holm p=%s; raw collapse p=%s)."
                 % (
                     fmt_p(pooled[df_b3]["holm_p"]),
                     fmt_p(clustered[df_b3]["holm_p"]),
                     ", ".join("s%d=%s" % (seed, fmt_p(per_seed[df_b3][str(seed)]["holm_p"])) for seed in EXPECTED_SEEDS),
                     fmt_p(trap_collapse[df_b3]["holm_p"]),
                     fmt_p(trap_collapse[df_b3]["exact_p"]),
                 ))
    lines.append("- `DF-hybrid` vs `B5`: no significance appears under any variant. Pooled Holm p=%s, per-seed Holm p values are %s, trap-collapse Holm p=%s, and clustered Holm p=%s."
                 % (
                     fmt_p(pooled[hybrid_b5]["holm_p"]),
                     ", ".join("s%d=%s" % (seed, fmt_p(per_seed[hybrid_b5][str(seed)]["holm_p"])) for seed in EXPECTED_SEEDS),
                     fmt_p(trap_collapse[hybrid_b5]["holm_p"]),
                     fmt_p(clustered[hybrid_b5]["holm_p"]),
                 ))
    lines.append("- No comparison that was non-significant in the pooled Holm family becomes significant after these clustered/seed sensitivity checks.")


def markdown_row(values: list[str]) -> str:
    return "| " + " | ".join(escape_md(str(value)) for value in values) + " |"


def markdown_separator(width: int) -> str:
    return "| " + " | ".join("---" for _ in range(width)) + " |"


def escape_md(value: str) -> str:
    return value.replace("|", "\\|")


def fmt_p(value: float | None) -> str:
    if value is None:
        return "NA"
    if value == 0:
        return "0"
    return "%.12g" % value


def fmt_counts(counts: dict[Any, Any]) -> str:
    if not counts:
        return "-"
    return ", ".join("%s:%s" % (key, counts[key]) for key in sorted(counts))


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


if __name__ == "__main__":
    raise SystemExit(main())
