#!/usr/bin/env python3
"""Render DreamBench-SWE v2 fold result tables from a frozen fold JSON.

The v2 fold input is expected to contain validity-gated S3 pass@1 cells keyed by
condition, trap, seed, construct label, and wake backbone.  This script does not
inspect live result directories.  Missing rows are rendered with an explicit
pending sentinel so paper drafts cannot accidentally imply unseen results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "analysis" / "fold" / "v2_fold.json"
DEFAULT_OUTPUT = ROOT / "analysis" / "fold" / "v2_tables.tex"
DEFAULT_SEQUENCE_RECORDS = ROOT / "experiments" / "env" / "sequences_confirmatory_v2.jsonl"

PENDING = "[" + "V2-" + "PENDING]"
NOT_RUN = "not run"
BOOTSTRAP_B = 2_000
BOOTSTRAP_SEED = 20260705

BASELINE_ORDER = ("B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7")
MAIN_LADDER_ORDER = BASELINE_ORDER + (
    "DF-typed-only",
    "DF-raw-only",
    "DF-hybrid",
    "DF-strict",
    "DF-strict-hybrid",
)
TRANSFER_ORDER = ("B0", "B3", "B5", "DF-typed-only", "DF-raw-only", "DF-hybrid")
HEADLINE_EXCLUDED_CONDITIONS = frozenset({"B5-MEM0", "B5-MEM0-LIT"})
CONSTRUCT_ORDER = tuple(f"C{index}" for index in range(1, 11))
SMALL_N_TRAP_THRESHOLD = 6

CONDITION_ALIASES = {
    "DF": "DF-typed-only",
    "DF-typed": "DF-typed-only",
    "DF_TYPED_ONLY": "DF-typed-only",
}

CONDITION_LABELS = {
    "B0": "B0 No memory",
    "B1": "B1 Raw episodic",
    "B2": "B2 Vector traces",
    "B3": "B3 Reflection-vector",
    "B4": "B4 Untyped summary",
    "B5": "B5 Instance memory",
    "B6": "B6 Subtask memory",
    "B7": "B7 Task tracker",
    "B5-MEM0": "B5-MEM0 Hosted Mem0",
    "DF-typed-only": "Probe typed-only",
    "DF-raw-only": "Probe raw-only",
    "DF-hybrid": "Probe hybrid",
    "DF-strict": "Probe strict",
    "DF-strict-hybrid": "Probe strict-hybrid",
}

BACKBONE_LABELS = {
    "codex": "Codex / GPT-5.5",
    "glm": "GLM-5.2",
}

EXISTING_TRAP_CONSTRUCTS = {
    # Provenance: analysis/investigation-evidence/AUTHORING-WORKLIST.md,
    # "Existing Trap Construct Map". These are frozen design labels for the
    # 22 carried v1 traps and the 8 synth-pilot traps, whose JSON records
    # predate inline construct_label metadata.
    "config-convention-sections": "C1",
    "config-freeze-provenance": "C1",
    "config-reviewer-coerce-tag": "C1",
    "config-reviewer-dupkey": "C1",
    "config-reviewer-strict-csv": "C1",
    "config-stale-merge": "C1",
    "config-stale-schema-id": "C1",
    "expr-arity-contract": "C1",
    "expr-reviewer-divzero": "C1",
    "expr-stale-registry-stability": "C1",
    "todo-archive-bucket": "C1",
    "todo-convention-aggregate": "C1",
    "todo-convention-summary": "C1",
    "todo-dedupe-keeplowest": "C1",
    "todo-flaky-duetiebreak": "C1",
    "todo-flaky-monotonic-ids": "C1",
    "todo-reviewer-export-dialect": "C1",
    "todo-reviewer-idformat": "C1",
    "expr-convention-opnaming": "C8",
    "expr-floordiv-category": "C8",
    "expr-generated-category": "C8",
    "expr-generated-precgroup": "C8",
    "synth-config-schema-audit-code": "C7",
    "synth-config-csv-width-delta": "C7",
    "synth-config-dupsec-code": "C6",
    "synth-expr-bitwise-help": "C7",
    "synth-expr-precedence-derive": "C4",
    "synth-expr-help-epoch": "C3",
    "synth-todo-overdue-handoff": "C7",
    "synth-todo-report-channel": "C3",
}

CONSTRUCT_LABELS = {
    "C1": "C1 Verbatim retention",
    "C2": "C2 Retrieval precision",
    "C3": "C3 Staleness/supersession",
    "C4": "C4 Update propagation",
    "C5": "C5 Scope discipline",
    "C6": "C6 Contradiction/provenance",
    "C7": "C7 Cross-session synthesis",
    "C8": "C8 Procedural/source-of-truth",
    "C9": "C9 Spurious-lesson rejection",
    "C10": "C10 Abstention",
}

PASS_KEYS = (
    "s3_pass_at_1",
    "pass_at_1",
    "passed",
    "s3_passed",
    "success",
    "final_passed",
)


@dataclass(frozen=True)
class Cell:
    condition: str
    trap_id: str
    seed: str
    pass_at_1: int
    construct_label: str
    construct_name: str
    backbone: str


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = read_json(args.input)
    sequence_metadata = load_sequence_metadata(args.sequence_records)
    rendered = build_rendered(payload, sequence_metadata=sequence_metadata)
    if args.no_write:
        print(rendered["tex"])
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered["tex"] + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render DreamBench-SWE v2 paper tables.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--sequence-records",
        type=Path,
        default=DEFAULT_SEQUENCE_RECORDS,
        help="Frozen JSONL sequence metadata used to fill construct labels absent from folded S3 cells.",
    )
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args(argv)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_sequence_metadata(path: Path | None) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    if path is None or not path.exists():
        return metadata
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, Mapping):
            continue
        sequence_id = sequence_id_from_payload(record)
        if sequence_id is None:
            continue
        construct = (
            record.get("construct_label")
            or record.get("construct")
            or record.get("construct_id")
            or EXISTING_TRAP_CONSTRUCTS.get(sequence_id)
        )
        item: dict[str, str] = {}
        if construct:
            item["construct_label"] = normalize_construct_label(str(construct))
        family = record.get("family_label") or record.get("seq_type")
        if family:
            item["family_label"] = str(family)
        metadata[sequence_id] = item
    return metadata


def build_rendered(
    payload: Mapping[str, Any],
    *,
    sequence_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    cells = parse_cells(payload, sequence_metadata=sequence_metadata)
    primary_key = primary_backbone_key(payload, cells)
    primary_cells = cells_for_backbone(cells, primary_key) if primary_key else cells
    t1 = build_main_ladder_rows(primary_cells)
    t2 = build_construct_rows(primary_cells)
    t3 = build_backbone_rows(cells)
    tex = "\n\n".join(
        [
            "% Generated by scripts/generate_tables_v2.py from a frozen v2 fold JSON.",
            render_t1_main_ladder(t1),
            render_t2_construct_strata(t2),
            render_t3_backbone_comparison(t3),
        ]
    )
    return {
        "cells": cells,
        "primary_backbone": primary_key,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "tex": tex,
    }


def parse_cells(
    payload: Mapping[str, Any],
    *,
    sequence_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> list[Cell]:
    conditions = payload.get("conditions")
    if isinstance(conditions, Mapping):
        return parse_condition_mapping(conditions, payload, sequence_metadata=sequence_metadata)

    records = payload.get("records") or payload.get("cells") or payload.get("s3_cells")
    if isinstance(records, list):
        return [
            cell
            for cell in (
                cell_from_record(item, payload, sequence_metadata=sequence_metadata)
                for item in records
            )
            if cell is not None
        ]

    raise ValueError("v2 fold JSON must contain a conditions mapping or a records/cells list")


def parse_condition_mapping(
    conditions: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    sequence_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> list[Cell]:
    cells: list[Cell] = []
    for raw_condition, condition_payload in sorted(conditions.items()):
        if not isinstance(condition_payload, Mapping):
            continue
        condition = canonical_condition(str(raw_condition))
        backbone_maps = condition_payload.get("backbones") or condition_payload.get("by_backbone")
        if isinstance(backbone_maps, Mapping):
            for raw_backbone, backbone_payload in sorted(backbone_maps.items()):
                if isinstance(backbone_payload, Mapping):
                    cells.extend(
                        parse_condition_backbone(
                            condition,
                            str(raw_backbone),
                            backbone_payload,
                            payload,
                            sequence_metadata=sequence_metadata,
                        )
                    )
            continue
        backbone = str(condition_payload.get("backbone") or payload.get("backbone") or payload.get("wake_model") or "pooled")
        cells.extend(
            parse_condition_backbone(
                condition,
                backbone,
                condition_payload,
                payload,
                sequence_metadata=sequence_metadata,
            )
        )
    return cells


def parse_condition_backbone(
    condition: str,
    backbone: str,
    condition_payload: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    sequence_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> list[Cell]:
    records = condition_payload.get("records") or condition_payload.get("cells")
    if isinstance(records, list):
        return [
            cell
            for cell in (
                cell_from_record(
                    item,
                    payload,
                    default_condition=condition,
                    default_backbone=backbone,
                    sequence_metadata=sequence_metadata,
                )
                for item in records
            )
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

    cells: list[Cell] = []
    for raw_trap_id, trap_payload in sorted(traps.items()):
        if not isinstance(trap_payload, Mapping):
            continue
        trap_id = str(
            trap_payload.get("trap_id")
            or trap_payload.get("seq_id")
            or trap_payload.get("sequence_id")
            or raw_trap_id
        )
        construct = construct_label(trap_payload, trap_id=trap_id, sequence_metadata=sequence_metadata)
        construct_name = str(trap_payload.get("construct_name") or "")
        seed_records = trap_payload.get("seeds") or trap_payload.get("per_seed") or trap_payload.get("seed_results")
        if isinstance(seed_records, Mapping):
            for raw_seed, seed_payload in sorted(seed_records.items(), key=lambda item: str(item[0])):
                pass_value = coerce_pass(seed_payload)
                if pass_value is None:
                    continue
                seed_backbone = backbone_from_payload(
                    seed_payload,
                    default=backbone_from_payload(trap_payload, default=backbone),
                )
                cells.append(
                    Cell(
                        condition=condition,
                        trap_id=trap_id,
                        seed=str(raw_seed),
                        pass_at_1=pass_value,
                        construct_label=construct,
                        construct_name=construct_name,
                        backbone=seed_backbone,
                    )
                )
        else:
            pass_value = coerce_pass(trap_payload)
            seed = trap_payload.get("seed")
            if pass_value is not None and seed is not None:
                cells.append(
                    Cell(
                        condition=condition,
                        trap_id=trap_id,
                        seed=str(seed),
                        pass_at_1=pass_value,
                        construct_label=construct,
                        construct_name=construct_name,
                        backbone=backbone_from_payload(trap_payload, default=backbone),
                    )
                )
    return cells


def cell_from_record(
    record: Any,
    payload: Mapping[str, Any],
    *,
    default_condition: str | None = None,
    default_backbone: str | None = None,
    sequence_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> Cell | None:
    if not isinstance(record, Mapping):
        return None
    condition_value = record.get("condition") or default_condition
    trap_value = record.get("trap_id") or record.get("seq_id") or record.get("sequence_id")
    seed_value = record.get("seed") or record.get("random_seed")
    pass_value = coerce_pass(record)
    if condition_value is None or trap_value is None or seed_value is None or pass_value is None:
        return None
    return Cell(
        condition=canonical_condition(str(condition_value)),
        trap_id=str(trap_value),
        seed=str(seed_value),
        pass_at_1=pass_value,
        construct_label=construct_label(record, trap_id=str(trap_value), sequence_metadata=sequence_metadata),
        construct_name=str(record.get("construct_name") or ""),
        backbone=backbone_from_payload(record, default=default_backbone or str(payload.get("backbone") or "pooled")),
    )


def build_main_ladder_rows(cells: Sequence[Cell]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_condition = group_by_condition(cells)
    headline_conditions = set(by_condition) - HEADLINE_EXCLUDED_CONDITIONS
    ordered = list(MAIN_LADDER_ORDER) + sorted(headline_conditions - set(MAIN_LADDER_ORDER))
    for condition in ordered:
        condition_cells = by_condition.get(condition, [])
        summary = summarize_cells(condition_cells, label=f"t1:{condition}")
        rows.append(
            {
                "condition": condition,
                "label": CONDITION_LABELS.get(condition, condition),
                "backbones": sorted({cell.backbone for cell in condition_cells}),
                "summary": summary,
            }
        )
    return rows


def build_construct_rows(cells: Sequence[Cell]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for construct in CONSTRUCT_ORDER:
        construct_cells = [
            cell
            for cell in cells
            if cell.construct_label == construct and cell.condition not in HEADLINE_EXCLUDED_CONDITIONS
        ]
        b5 = summarize_cells(
            [cell for cell in construct_cells if cell.condition == "B5"],
            label=f"t2:{construct}:B5",
        )
        hybrid = summarize_cells(
            [cell for cell in construct_cells if cell.condition == "DF-hybrid"],
            label=f"t2:{construct}:DF-hybrid",
        )
        summaries = {
            condition: summarize_cells(items, label=f"t2:{construct}:{condition}")
            for condition, items in group_by_condition(construct_cells).items()
        }
        best_condition, best_summary = best_observed_condition(summaries)
        trap_n = len({cell.trap_id for cell in construct_cells})
        rows.append(
            {
                "construct": construct,
                "label": construct_display_label(construct, construct_cells),
                "trap_n": trap_n,
                "cell_n": len(construct_cells),
                "b5": b5,
                "df_hybrid": hybrid,
                "best_condition": best_condition,
                "best": best_summary,
                "small_n": trap_n > 0 and trap_n < SMALL_N_TRAP_THRESHOLD,
            }
        )
    return rows


def build_backbone_rows(cells: Sequence[Cell]) -> dict[str, Any]:
    by_condition = group_by_condition(cells)
    eligible_conditions = set(by_condition) - HEADLINE_EXCLUDED_CONDITIONS
    condition_order = list(TRANSFER_ORDER) + sorted(eligible_conditions - set(TRANSFER_ORDER))
    rows = []
    rates_by_backbone: dict[str, dict[str, float]] = {"codex": {}, "glm": {}}
    for condition in condition_order:
        condition_cells = by_condition.get(condition, [])
        codex = summarize_cells(
            [cell for cell in condition_cells if normalized_backbone(cell.backbone) == "codex"],
            label=f"t3:{condition}:codex",
        )
        glm = summarize_cells(
            [cell for cell in condition_cells if normalized_backbone(cell.backbone) == "glm"],
            label=f"t3:{condition}:glm",
        )
        if codex["rate"] is not None:
            rates_by_backbone["codex"][condition] = float(codex["rate"])
        if glm["rate"] is not None:
            rates_by_backbone["glm"][condition] = float(glm["rate"])
        rows.append(
            {
                "condition": condition,
                "label": CONDITION_LABELS.get(condition, condition),
                "codex": codex,
                "glm": glm,
                "delta_glm_minus_codex": (
                    float(glm["rate"]) - float(codex["rate"])
                    if glm["rate"] is not None and codex["rate"] is not None
                    else None
                ),
            }
        )
    ordering = ladder_ordering_check(rates_by_backbone["codex"], rates_by_backbone["glm"])
    return {
        "rows": rows,
        "ordering": ordering,
        "has_glm": any(row["glm"].get("n") for row in rows),
    }


def primary_backbone_key(payload: Mapping[str, Any], cells: Sequence[Cell]) -> str | None:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    configured = (
        payload.get("primary_backbone")
        or payload.get("primary_wake_model")
        or metadata.get("primary_backbone")
        or metadata.get("primary_wake_model")
    )
    if configured:
        key = normalized_backbone(str(configured))
        if any(normalized_backbone(cell.backbone) == key for cell in cells):
            return key
    if any(normalized_backbone(cell.backbone) == "codex" for cell in cells):
        return "codex"
    observed = sorted({normalized_backbone(cell.backbone) for cell in cells})
    if len(observed) == 1:
        return observed[0]
    return None


def cells_for_backbone(cells: Sequence[Cell], backbone_key: str) -> list[Cell]:
    return [cell for cell in cells if normalized_backbone(cell.backbone) == backbone_key]


def summarize_cells(cells: Sequence[Cell], *, label: str) -> dict[str, Any]:
    values = [cell.pass_at_1 for cell in cells]
    trap_ids = sorted({cell.trap_id for cell in cells})
    successes = sum(values)
    n = len(values)
    rate = successes / n if n else None
    return {
        "successes": successes,
        "n": n,
        "trap_n": len(trap_ids),
        "rate": rate,
        "clustered_ci_95": clustered_bootstrap_ci(cells, label=label),
    }


def clustered_bootstrap_ci(cells: Sequence[Cell], *, label: str) -> list[float | None]:
    by_trap: dict[str, list[int]] = {}
    for cell in cells:
        by_trap.setdefault(cell.trap_id, []).append(cell.pass_at_1)
    trap_ids = sorted(by_trap)
    if not trap_ids:
        return [None, None]
    observed = [value for trap_id in trap_ids for value in by_trap[trap_id]]
    if len(trap_ids) == 1:
        mean = sum(observed) / len(observed)
        return [mean, mean]

    rng = random.Random(derived_seed(label))
    means: list[float] = []
    for _ in range(BOOTSTRAP_B):
        total = 0
        count = 0
        for _ in trap_ids:
            sampled = by_trap[rng.choice(trap_ids)]
            total += sum(sampled)
            count += len(sampled)
        means.append(total / count if count else 0.0)
    means.sort()
    return [percentile(means, 0.025), percentile(means, 0.975)]


def ladder_ordering_check(codex_rates: Mapping[str, float], glm_rates: Mapping[str, float]) -> dict[str, Any]:
    common = [condition for condition in TRANSFER_ORDER if condition in codex_rates and condition in glm_rates]
    common.extend(sorted((set(codex_rates) & set(glm_rates)) - set(common)))
    reversed_pairs: list[str] = []
    for index, left in enumerate(common):
        for right in common[index + 1 :]:
            codex_sign = sign(codex_rates[left] - codex_rates[right])
            glm_sign = sign(glm_rates[left] - glm_rates[right])
            if codex_sign != 0 and glm_sign != 0 and codex_sign != glm_sign:
                reversed_pairs.append(f"{left}/{right}")
    return {
        "common_conditions": common,
        "preserved": bool(common) and not reversed_pairs,
        "reversed_pairs": reversed_pairs,
    }


def render_t1_main_ladder(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\begin{tabular}{llrrll}",
        r"\toprule",
        r"Condition & Wake backbone(s) & Traps & Cells & Pass@1 & Clustered 95\% CI \\",
        r"\midrule",
    ]
    for row in rows:
        summary = row["summary"]
        lines.append(
            " & ".join(
                [
                    latex_escape(str(row["label"])),
                    latex_escape(backbone_list(row["backbones"])),
                    fmt_int_or_pending(summary["trap_n"]),
                    fmt_int_or_pending(summary["n"]),
                    fmt_count_rate(summary),
                    fmt_ci(summary["clustered_ci_95"]),
                ]
            )
            + r" \\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\caption{V2 main ablation ladder S3 Pass@1 for the primary wake backbone. Hosted live-memory baselines are excluded from this headline table and reserved for failure analysis. Intervals are trap-cluster bootstrap 95\% CIs; seeds are repeated observations within traps.}",
            r"\label{tab:v2-main-ladder}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def render_t2_construct_strata(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\scriptsize",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{lrrllll}",
        r"\toprule",
        r"Construct & Traps & Cells & B5 & Probe hybrid & Best observed & Small-n \\",
        r"\midrule",
    ]
    for row in rows:
        best = row["best"]
        best_condition = row["best_condition"]
        best_condition_label = CONDITION_LABELS.get(best_condition, best_condition)
        best_label = (
            f"{best_condition_label} {fmt_count_rate(best)}"
            if best_condition_label is not None and best is not None
            else PENDING
        )
        lines.append(
            " & ".join(
                [
                    latex_escape(str(row["label"])),
                    fmt_int_or_pending(row["trap_n"]),
                    fmt_int_or_pending(row["cell_n"]),
                    fmt_count_rate(row["b5"]),
                    fmt_count_rate(row["df_hybrid"]),
                    latex_escape(best_label),
                    "yes" if row["small_n"] else ("no" if row["trap_n"] else PENDING),
                ]
            )
            + r" \\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"}%",
            rf"\caption{{V2 per-construct S3 strata C1--C10, descriptive only. Hosted live-memory baselines are excluded from headline construct summaries. Small-n is flagged when a construct has fewer than {SMALL_N_TRAP_THRESHOLD} trap clusters with observed cells.}}",
            r"\label{tab:v2-construct-strata}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def render_t3_backbone_comparison(table: Mapping[str, Any]) -> str:
    if not table.get("has_glm"):
        return (
            "% V2 cross-backbone table omitted: the GLM-5.2 backbone was scoped out, "
            "so no GLM S3 cells are present in the confirmatory fold."
        )
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\begin{tabular}{llll}",
        r"\toprule",
        r"Condition & Codex & GLM & $\Delta$ GLM--Codex \\",
        r"\midrule",
    ]
    for row in table["rows"]:
        lines.append(
            " & ".join(
                [
                    latex_escape(str(row["label"])),
                    fmt_count_rate_or_not_run(row["codex"]),
                    fmt_count_rate_or_not_run(row["glm"]),
                    fmt_signed_or_not_run(row["delta_glm_minus_codex"]),
                ]
            )
            + r" \\"
        )
    ordering = table["ordering"]
    reversed_pairs = ", ".join(ordering["reversed_pairs"]) if ordering["reversed_pairs"] else "none"
    status = "yes" if ordering["preserved"] else NOT_RUN if not ordering["common_conditions"] else "no"
    lines.extend(
        [
            r"\midrule",
            rf"Ladder ordering preserved & \multicolumn{{3}}{{l}}{{{status}; reversed pairs: {latex_escape(reversed_pairs)}}} \\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\caption{V2 cross-backbone S3 comparison. The ordering check flags strict pairwise condition reversals between Codex and GLM among conditions observed on both backbones; it is a transfer diagnostic, not a pooled headline test.}",
            r"\label{tab:v2-cross-backbone}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines)


def best_observed_condition(summaries: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, Mapping[str, Any] | None]:
    candidates = [
        (condition, summary)
        for condition, summary in summaries.items()
        if summary.get("rate") is not None
    ]
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: (float(item[1]["rate"]), item[0]))


def group_by_condition(cells: Sequence[Cell]) -> dict[str, list[Cell]]:
    grouped: dict[str, list[Cell]] = {}
    for cell in cells:
        grouped.setdefault(cell.condition, []).append(cell)
    return grouped


def construct_display_label(construct: str, cells: Sequence[Cell]) -> str:
    for cell in cells:
        if cell.construct_name:
            return f"{construct} {cell.construct_name}"
    return CONSTRUCT_LABELS.get(construct, construct)


def construct_label(
    payload: Mapping[str, Any],
    *,
    trap_id: str | None = None,
    sequence_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    value = payload.get("construct_label") or payload.get("construct") or payload.get("construct_id")
    sequence_id = sequence_id_from_payload(payload) or strip_seed_prefix(trap_id)
    if not value and sequence_id and sequence_metadata:
        item = sequence_metadata.get(sequence_id)
        if isinstance(item, Mapping):
            value = item.get("construct_label")
    if not value and sequence_id:
        value = EXISTING_TRAP_CONSTRUCTS.get(sequence_id)
    if value:
        return normalize_construct_label(str(value))
    return infer_construct_label_from_id(sequence_id or trap_id or "")


def normalize_construct_label(value: str) -> str:
    text = value.upper().replace(" ", "").replace("_", "")
    if text.startswith("CONSTRUCT"):
        text = text.replace("CONSTRUCT", "C", 1)
    return text


def backbone_from_payload(payload: Any, *, default: str) -> str:
    if isinstance(payload, Mapping):
        value = payload.get("backbone") or payload.get("wake_model") or payload.get("model")
        if value:
            return str(value)
        for key in ("source_path", "result_path", "log_path", "path"):
            value = payload.get(key)
            normalized = normalized_backbone(str(value)) if value else ""
            if normalized in {"codex", "glm"}:
                return normalized
    return str(default)


def sequence_id_from_payload(payload: Mapping[str, Any]) -> str | None:
    for key in ("sequence_id", "seq_id", "trap_id"):
        value = payload.get(key)
        if value:
            return strip_oracle_suffix(str(value))
    oracle_id = payload.get("oracle_id")
    if oracle_id:
        return strip_oracle_suffix(str(oracle_id))
    return None


def strip_seed_prefix(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(":", 1)[1] if value.startswith("seed") and ":" in value else value


def strip_oracle_suffix(value: str) -> str:
    if value.endswith("-s1") or value.endswith("-s2") or value.endswith("-s3"):
        return value[:-3]
    return value


def infer_construct_label_from_id(identifier: str) -> str:
    for part in identifier.replace("_", "-").split("-"):
        label = normalize_construct_label(part)
        if label.startswith("C") and label[1:].isdigit():
            return label
    return "UNKNOWN"


def normalized_backbone(backbone: str) -> str:
    lowered = backbone.lower()
    if "glm" in lowered:
        return "glm"
    if "codex" in lowered or "gpt-5.5" in lowered or "gpt5.5" in lowered:
        return "codex"
    return lowered


def coerce_pass(payload: Any) -> int | None:
    if isinstance(payload, bool):
        return 1 if payload else 0
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        if float(payload) in (0.0, 1.0):
            return int(payload)
    if not isinstance(payload, Mapping):
        return None
    for key in PASS_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) in (0.0, 1.0):
            return int(value)
    return None


def canonical_condition(condition: str) -> str:
    return CONDITION_ALIASES.get(condition, condition)


def percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires non-empty values")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    weight = pos - lo
    return float(sorted_values[lo]) * (1.0 - weight) + float(sorted_values[hi]) * weight


def derived_seed(label: str) -> int:
    digest = hashlib.sha256(f"{BOOTSTRAP_SEED}:{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def sign(value: float, *, epsilon: float = 1e-12) -> int:
    if value > epsilon:
        return 1
    if value < -epsilon:
        return -1
    return 0


def backbone_list(backbones: Sequence[str]) -> str:
    if not backbones:
        return PENDING
    labels = {
        BACKBONE_LABELS.get(normalized_backbone(backbone), backbone)
        for backbone in backbones
    }
    return ", ".join(sorted(labels))


def fmt_int_or_pending(value: Any) -> str:
    if isinstance(value, int) and value > 0:
        return str(value)
    return PENDING


def fmt_count_rate(summary: Mapping[str, Any] | None) -> str:
    if not summary or not summary.get("n"):
        return PENDING
    return f"{int(summary['successes'])}/{int(summary['n'])} ({fmt_rate(summary['rate'])})"


def fmt_count_rate_or_not_run(summary: Mapping[str, Any] | None) -> str:
    if not summary or not summary.get("n"):
        return NOT_RUN
    return fmt_count_rate(summary)


def fmt_rate(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return PENDING
    return f"{float(value):.3f}"


def fmt_ci(ci: Any) -> str:
    if not (
        isinstance(ci, list)
        and len(ci) == 2
        and isinstance(ci[0], (int, float))
        and isinstance(ci[1], (int, float))
    ):
        return PENDING
    return f"[{float(ci[0]):.3f}, {float(ci[1]):.3f}]"


def fmt_signed(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return PENDING
    return f"{float(value):+.3f}"


def fmt_signed_or_not_run(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return NOT_RUN
    return fmt_signed(value)


def latex_escape(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("_", r"\_")
        .replace("%", r"\%")
        .replace("&", r"\&")
    )


if __name__ == "__main__":
    raise SystemExit(main())
