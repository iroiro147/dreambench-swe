#!/usr/bin/env python3
"""BARRIER-2 uniform offline re-score with validity gates.

The table is only clean if every included record comes from a passing gate
report and passes per-record continuation/sleep/Pass@1 checks. Invalid inputs
are excluded loudly and make the script exit nonzero rather than silently
publishing a clean-looking table.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import argparse
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(os.environ.get("DREAMFORGE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "src"))
from dream_memory import slice_scorer  # noqa: E402
from experiments import contamination_scan  # noqa: E402

SEQUENCES_PATH = ROOT / "experiments" / "env" / "sequences.jsonl"
RESULTS_ROOT = ROOT / "experiments" / "results"
EXPECTED_S3 = int(os.environ.get("BARRIER2_EXPECTED_S3", "22"))
EXPECTED_SEEDS = tuple(
    int(item.strip())
    for item in os.environ.get("BARRIER2_EXPECTED_SEEDS", "1,2,3").split(",")
    if item.strip()
)
HEADLINE_PASS_AT_1 = os.environ.get("BARRIER2_HEADLINE", "pass_at_1").lower() in {"pass@1", "pass_at_1"}
OUTPUT_DIR = ROOT / "analysis" / "fold"
CANARY_PROOF_PATH = OUTPUT_DIR / "CANARY-PROOF.txt"
BENCHMARK_VERSION = "DreamBench-SWE v1.1"
CONDS = [
    "B0",
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "B6",
    "B7",
    "B5-MEM0",
    "DF",
    "DF-strict",
    "A0",
    "A2",
    "A4",
    "A5",
    "A6",
    "A11",
]

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
    "DF": "DF \\sys{} full",
    "DF-strict": "DF-strict retrieval",
    "A0": "A0 Episodic-only",
    "A2": "A2 No contradiction repair",
    "A4": "A4 No counterfactual replay",
    "A5": "A5 No stale suppression",
    "A6": "A6 No retrieval gate",
    "A11": "A11 Forced consolidation",
}

ABLATION_REMOVED = {
    "DF": "None (full system)",
    "A0": "Typed consolidation and maintenance (episodic-only)",
    "A2": "Contradiction repair",
    "A4": "Counterfactual replay",
    "A5": "Stale suppression / forgetting",
    "A6": "Retrieval gate",
    "A11": "Consolidation utility/risk gating",
}

HYG = [
    ("StaleMemoryActivationRate", "lo"),
    ("RepeatedErrorRate", "lo"),
    ("HarmfulMemoryRate", "lo"),
    ("RegressionAfterUpdate", "lo"),
    ("ContradictionRepairAccuracy", "hi"),
    ("HumanFeedbackUseAccuracy", "hi"),
    ("UsefulMemoryPrecision", "hi"),
    ("ScopeAccuracy", "hi"),
    ("TransferScore", "hi"),
]

_CONTAINER_READ_CONTEXT = re.compile(
    r"\b(cat|sed|rg|grep|find|awk|head|tail|ls|stat)\b|open\(|read_text|test -[efr]|exec\\n|succeeded in|/bin/(?:ba)?sh -lc",
    re.IGNORECASE,
)
_CONTAINER_HIDDEN_CONTEXT = re.compile(
    r"experiments/env/(?:oracles|refsol|sequences\.jsonl)|<REVIEWER_ONLY_(?:ORACLES|REFSOL)>|experiments/secrets\.env|logs/codex|\.py-diff",
    re.IGNORECASE,
)
_CONTAINER_WORK_CONTEXT = re.compile(r"\bin /work\b|/work/", re.IGNORECASE)


def load_sequences(path: Path = SEQUENCES_PATH) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _gate_report_path(condition_dir: Path) -> Path | None:
    candidates = [condition_dir / "gate_report.json", condition_dir.parent / "gate_report.json"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _gate_report_errors(condition_dir: Path) -> list[str]:
    path = _gate_report_path(condition_dir)
    if path is None:
        return []
    try:
        gate = _read_json(path)
    except Exception as exc:
        return [f"unreadable_gate_report:{type(exc).__name__}"]

    errors: list[str] = []
    gates = gate.get("acceptance_gates") if isinstance(gate.get("acceptance_gates"), Mapping) else {}
    for gate_name in ("continuation_audit_valid",):
        if gate_name in gates and gates.get(gate_name) is not True:
            errors.append(f"failing_gate:{gate_name}")

    continuation = gate.get("continuation_audit") if isinstance(gate.get("continuation_audit"), Mapping) else {}
    if "valid" in continuation and continuation.get("valid") is not True:
        errors.append("failing_gate:continuation_audit.valid")
    return errors


def _task(record: Mapping[str, Any]) -> Mapping[str, Any]:
    task = record.get("task")
    return task if isinstance(task, Mapping) else {}


def _sequence_id(record: Mapping[str, Any]) -> str:
    task = _task(record)
    return str(task.get("sequence_id") or task.get("seq_id") or record.get("sequence_id") or "")


def _session_index(record: Mapping[str, Any]) -> int:
    task = _task(record)
    try:
        return int(task.get("session_index") or record.get("session_index") or 0)
    except (TypeError, ValueError):
        return 0


def _seed(record: Mapping[str, Any], payload: Mapping[str, Any] | None = None) -> int:
    task = _task(record)
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


def _record_key(record: Mapping[str, Any]) -> tuple[str, int]:
    return (_sequence_id(record), _session_index(record))


def _record_seed_key(record: Mapping[str, Any], payload: Mapping[str, Any] | None = None) -> tuple[int, str, int]:
    sequence_id, session_index = _record_key(record)
    return (_seed(record, payload), sequence_id, session_index)


def _record_validity_errors(record: Mapping[str, Any], *, headline_pass_at_1: bool = HEADLINE_PASS_AT_1) -> list[str]:
    errors: list[str] = []
    session_index = _session_index(record)
    error_type = str(record.get("error_type") or "")
    if record.get("sleep_error"):
        errors.append("sleep_error")
    if str(record.get("isolation_mode") or "") == "unavailable":
        errors.append("isolation_mode_unavailable")
    if error_type == "isolation_unavailable" or error_type.startswith("task_exception:"):
        errors.append(error_type)
    if session_index > 1:
        if record.get("started_from_previous_session") is not True:
            errors.append("invalid_continuation:started_from_previous_session")
        if not record.get("previous_session_end_commit"):
            errors.append("invalid_continuation:previous_session_end_commit")
        if record.get("forbidden_fresh_base_ref_used") is True:
            errors.append("invalid_continuation:forbidden_fresh_base_ref_used")
    return errors


def _sequence_map(sequence_records: list[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    return {
        str(sequence.get("seq_id") or sequence.get("sequence_id") or ""): sequence
        for sequence in sequence_records or []
        if isinstance(sequence, Mapping)
    }


def _record_contamination(record: Mapping[str, Any], sequence: Mapping[str, Any] | None) -> dict[str, Any]:
    is_container = str(record.get("isolation_mode") or "") == "container"
    if is_container:
        if sequence is None:
            result = {
                "contaminated": record.get("contaminated") is True,
                "evidence": list(record.get("contamination_evidence") or []),
            }
        else:
            result = contamination_scan.scan_record(record, sequence)
        return _container_contamination_result(result)
    if record.get("contaminated") is True:
        return {
            "contaminated": True,
            "evidence": list(record.get("contamination_evidence") or []),
        }
    if sequence is None:
        return {"contaminated": False, "evidence": []}
    result = contamination_scan.scan_record(record, sequence)
    return result


def _container_contamination_result(result: Mapping[str, Any]) -> dict[str, Any]:
    evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
    read_evidence = [
        item
        for item in evidence
        if isinstance(item, Mapping) and _container_evidence_indicates_hidden_read(item)
    ]
    return {"contaminated": bool(read_evidence), "evidence": read_evidence}


def _container_evidence_indicates_hidden_read(item: Mapping[str, Any]) -> bool:
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


def canary_proof_status(path: Path = CANARY_PROOF_PATH) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "bytes": path.stat().st_size if exists else 0,
    }


def _contamination_reason(result: Mapping[str, Any]) -> str:
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return "contaminated"
    first = evidence[0] if isinstance(evidence[0], Mapping) else {}
    match = str(first.get("match") or "")[:180]
    field = str(first.get("field") or "")
    return f"contaminated:{field}:{match}"


def union_records(
    cond: str,
    *,
    results_root: Path = RESULTS_ROOT,
    headline_pass_at_1: bool = HEADLINE_PASS_AT_1,
    sequence_records: list[Mapping[str, Any]] | None = None,
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    by_key: dict[tuple[int, str, int], tuple[float, Mapping[str, Any]]] = {}
    excluded: list[dict[str, Any]] = []
    sequences = _sequence_map(sequence_records)
    pattern = str(results_root / "*-gpt-5.5-*" / cond)
    for condition_dir in sorted(Path(path) for path in glob.glob(pattern)):
        results_path = condition_dir / "results.json"
        if not results_path.exists():
            continue
        gate_errors = _gate_report_errors(condition_dir)
        if gate_errors:
            excluded.append(
                {
                    "condition": cond,
                    "path": str(results_path),
                    "scope": "result_dir",
                    "reason": ",".join(gate_errors),
                }
            )
            continue
        try:
            payload = _read_json(results_path)
            records = payload.get("records") or []
        except Exception as exc:
            excluded.append(
                {
                    "condition": cond,
                    "path": str(results_path),
                    "scope": "result_dir",
                    "reason": f"unreadable_results:{type(exc).__name__}",
                }
            )
            continue
        mtime = results_path.stat().st_mtime
        for record in records:
            if not isinstance(record, Mapping):
                continue
            seed_key = _record_seed_key(record, payload)
            seed, sequence_id, session_index = seed_key
            if not sequence_id or not session_index:
                excluded.append(
                    {
                        "condition": cond,
                        "path": str(results_path),
                        "scope": "record",
                        "reason": "missing_sequence_or_session",
                    }
                )
                continue
            record_errors = _record_validity_errors(record, headline_pass_at_1=headline_pass_at_1)
            if record_errors:
                excluded.append(
                    {
                        "condition": cond,
                        "path": str(results_path),
                        "scope": "record",
                        "seed": seed,
                        "sequence_id": sequence_id,
                        "session_index": session_index,
                        "reason": ",".join(record_errors),
                    }
                )
                continue
            contamination = _record_contamination(record, sequences.get(sequence_id))
            if contamination.get("contaminated"):
                excluded.append(
                    {
                        "condition": cond,
                        "path": str(results_path),
                        "scope": "record",
                        "seed": seed,
                        "sequence_id": sequence_id,
                        "session_index": session_index,
                        "reason": _contamination_reason(contamination),
                        "contamination_evidence": contamination.get("evidence") or [],
                    }
                )
                continue
            if seed_key not in by_key or mtime > by_key[seed_key][0]:
                annotated = dict(record)
                annotated["_dreambench_seed"] = seed
                annotated["_source_results_path"] = str(results_path)
                annotated["_source_condition"] = cond
                by_key[seed_key] = (mtime, annotated)
    return [value[1] for value in by_key.values()], excluded


def build_rows(
    *,
    sequences: list[dict[str, Any]],
    conditions: list[str] = CONDS,
    expected_s3: int = EXPECTED_S3,
    expected_seeds: tuple[int, ...] = EXPECTED_SEEDS,
    results_root: Path = RESULTS_ROOT,
    headline_pass_at_1: bool = HEADLINE_PASS_AT_1,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, Any]] = []
    coverage_errors: list[dict[str, Any]] = []
    expected_ids = {str(sequence.get("seq_id") or sequence.get("sequence_id") or "") for sequence in sequences}
    for condition in conditions:
        records, condition_excluded = union_records(
            condition,
            results_root=results_root,
            headline_pass_at_1=headline_pass_at_1,
            sequence_records=sequences,
        )
        excluded.extend(condition_excluded)
        contaminated_count = sum(1 for item in condition_excluded if str(item.get("reason") or "").startswith("contaminated"))
        s3 = [record for record in records if _session_index(record) == 3]
        s3_by_seed: dict[int, list[Mapping[str, Any]]] = {
            seed: [record for record in s3 if _seed(record) == seed] for seed in expected_seeds
        }
        per_seed = {
            seed: _task_count_summary(seed_records, expected_ids=expected_ids, expected_s3=expected_s3)
            for seed, seed_records in s3_by_seed.items()
        }
        s3_ids = {_sequence_id(record) for record in s3}
        n3 = len(s3)
        pass_at_1_count = sum(1 for record in s3 if record.get("pass_at_1"))
        final_passed_count = sum(1 for record in s3 if record.get("final_passed"))
        p3 = pass_at_1_count if headline_pass_at_1 else final_passed_count
        sleep_err = sum(1 for record in records if record.get("sleep_error"))
        if n3 < expected_s3:
            coverage_errors.append(
                {
                    "condition": condition,
                    "reason": "incomplete_s3_coverage",
                    "n_s3": n3,
                    "expected_s3": expected_s3,
                    "missing_sequence_ids": sorted(expected_ids - s3_ids),
                }
            )
        for seed, summary in per_seed.items():
            if summary["n3"] < expected_s3:
                coverage_errors.append(
                    {
                        "condition": condition,
                        "seed": seed,
                        "reason": "incomplete_s3_coverage_for_seed",
                        "n_s3": summary["n3"],
                        "expected_s3": expected_s3,
                        "missing_sequence_ids": summary["missing_sequence_ids"],
                    }
                )
        pooled_expected = expected_s3 * len(expected_seeds)
        pooled_partial = n3 < pooled_expected
        if pooled_partial:
            coverage_errors.append(
                {
                    "condition": condition,
                    "reason": "incomplete_pooled_s3_coverage",
                    "n_s3": n3,
                    "expected_s3": pooled_expected,
                    "missing_seed_sequence_ids": _missing_seed_sequence_ids(per_seed),
                }
            )
        hygiene_metrics = _hygiene_metrics_for_records(
            records=records,
            sequences=sequences,
            condition=condition,
            expected_seeds=expected_seeds,
        )
        hyg = {name: payload["value"] if payload["value"] != "n/a" else None for name, payload in hygiene_metrics.items()}
        dens = {name: payload["denominator"] for name, payload in hygiene_metrics.items()}
        rows[condition] = {
            "benchmark_version": BENCHMARK_VERSION,
            "expected_seeds": list(expected_seeds),
            "per_seed": per_seed,
            "pooled": {
                "n3": n3,
                "pass_at_1_count": pass_at_1_count,
                "final_passed_count": final_passed_count,
                "pass_at_1_rate": (pass_at_1_count / n3 if n3 else None),
                "final_passed_rate": (final_passed_count / n3 if n3 else None),
            },
            "n3": n3,
            "p3": p3,
            "succ": (p3 / n3 if n3 else None),
            "pass_at_1_count": pass_at_1_count,
            "pass_at_1_rate": (pass_at_1_count / n3 if n3 else None),
            "final_passed_count": final_passed_count,
            "final_passed_rate": (final_passed_count / n3 if n3 else None),
            "sleeperr": sleep_err,
            "contaminated_count": contaminated_count,
            "hyg": hyg,
            "dens": dens,
            "hygiene_metrics": hygiene_metrics,
            "partial": pooled_partial,
            "partial_note": f"partial n={n3}/{pooled_expected}" if pooled_partial else "",
            "missing_sequence_ids": sorted(expected_ids - s3_ids),
            "records": records,
        }
    return rows, excluded, coverage_errors


def _task_count_summary(
    records: list[Mapping[str, Any]],
    *,
    expected_ids: set[str],
    expected_s3: int,
) -> dict[str, Any]:
    seen_ids = {_sequence_id(record) for record in records}
    n3 = len(records)
    pass_at_1_count = sum(1 for record in records if record.get("pass_at_1"))
    final_passed_count = sum(1 for record in records if record.get("final_passed"))
    return {
        "n3": n3,
        "expected_s3": expected_s3,
        "pass_at_1_count": pass_at_1_count,
        "final_passed_count": final_passed_count,
        "pass_at_1_rate": (pass_at_1_count / n3 if n3 else None),
        "final_passed_rate": (final_passed_count / n3 if n3 else None),
        "partial": n3 < expected_s3,
        "missing_sequence_ids": sorted(expected_ids - seen_ids),
    }


def _missing_seed_sequence_ids(per_seed: Mapping[int, Mapping[str, Any]]) -> list[str]:
    missing: list[str] = []
    for seed, summary in sorted(per_seed.items()):
        for sequence_id in summary.get("missing_sequence_ids") or []:
            missing.append(f"s{seed}:{sequence_id}")
    return missing


def _hygiene_metrics_for_records(
    *,
    records: list[Mapping[str, Any]],
    sequences: list[dict[str, Any]],
    condition: str,
    expected_seeds: tuple[int, ...],
) -> dict[str, dict[str, Any]]:
    totals = {name: {"numerator": 0, "denominator": 0, "value": "n/a"} for name, _ in HYG}
    for seed in expected_seeds:
        seed_records = [record for record in records if _seed(record) == seed]
        if not seed_records:
            continue
        report = slice_scorer.rescore_from_records(
            records=seed_records,
            sequence_records=sequences,
            condition=condition,
        )
        metrics = (report.get("aggregate") or {}).get("metrics") or {}
        for name, _ in HYG:
            payload = _metric_payload(metrics.get(name, {}))
            totals[name]["numerator"] += int(payload["numerator"] or 0)
            totals[name]["denominator"] += int(payload["denominator"] or 0)
    for name in totals:
        denominator = totals[name]["denominator"]
        if denominator > 0:
            totals[name]["value"] = totals[name]["numerator"] / denominator
    return totals


def _metric_payload(metric: Any) -> dict[str, Any]:
    metric = metric if isinstance(metric, Mapping) else {}
    denominator = int(metric.get("denominator") or 0)
    numerator = int(metric.get("numerator") or 0)
    if denominator <= 0:
        return {"value": "n/a", "numerator": numerator, "denominator": denominator}
    return {"value": metric.get("value"), "numerator": numerator, "denominator": denominator}


def _fmt_rate(value: Any) -> str:
    return f"{value:.3f}" if isinstance(value, float) else "  -- "


def _fmt_count_rate(summary: Mapping[str, Any]) -> str:
    n3 = int(summary.get("n3") or 0)
    passed = int(summary.get("pass_at_1_count") or 0)
    rate = summary.get("pass_at_1_rate")
    if isinstance(rate, float):
        return f"{passed}/{n3} ({rate:.2f})"
    return f"{passed}/{n3} (n/a)"


def print_report(
    rows: Mapping[str, Mapping[str, Any]],
    *,
    conditions: list[str] = CONDS,
    excluded: list[dict[str, Any]],
    coverage_errors: list[dict[str, Any]],
    headline_pass_at_1: bool = HEADLINE_PASS_AT_1,
    expected_seeds: tuple[int, ...] = EXPECTED_SEEDS,
    expected_s3: int = EXPECTED_S3,
    canary_proof_path: Path = CANARY_PROOF_PATH,
) -> None:
    suspect = bool(excluded or coverage_errors)
    status = "SUSPECT_NON_CLEAN" if suspect else "CLEAN"
    contaminated_count = sum(1 for item in excluded if str(item.get("reason") or "").startswith("contaminated"))
    print(f"=== {BENCHMARK_VERSION} VALIDITY STATUS: {status} contaminated_count={contaminated_count} ===")
    proof = canary_proof_status(canary_proof_path)
    print(
        "=== HERMETICITY PROOF: "
        f"container_record_scan=enabled canary_proof_exists={str(proof['exists']).lower()} "
        f"canary_proof_bytes={proof['bytes']} path={proof['path']} ==="
    )
    if excluded:
        print("=== SUSPECT/EXCLUDED INPUTS ===")
        for item in excluded:
            location = item.get("path")
            seed = item.get("seed")
            seq = item.get("sequence_id")
            sess = item.get("session_index")
            record = f" s{seed}:{seq} S{sess}" if seed and seq and sess else (f" {seq} S{sess}" if seq and sess else "")
            print(f"EXCLUDED {item.get('condition')} {item.get('scope')}{record}: {item.get('reason')} :: {location}")
    if coverage_errors:
        print("=== COVERAGE FAILURES ===")
        for item in coverage_errors:
            missing = item.get("missing_sequence_ids", item.get("missing_seed_sequence_ids", []))
            print(
                "SUSPECT "
                f"{item['condition']}"
                f"{' seed=' + str(item['seed']) if 'seed' in item else ''}: "
                f"{item['reason']} n_S3={item['n_s3']} "
                f"expected={item['expected_s3']} missing={missing}"
            )

    headline = "Pass@1" if headline_pass_at_1 else "TaskSuccess"
    seed_headers = [f"s{seed}" for seed in expected_seeds]
    print(f"\n=== {headline} per seed and pooled (S3 trap) - validity-gated union, {BENCHMARK_VERSION} ===")
    print(f"{'cond':<10}" + "".join(f"{header:>16}" for header in seed_headers) + f"{'pooled':>16}{'sleep_err':>11}{'contam':>8}{'note':>14}")
    for condition in conditions:
        row = rows[condition]
        seed_cells = "".join(f"{_fmt_count_rate(row['per_seed'].get(seed, {})):>16}" for seed in expected_seeds)
        print(
            f"{condition:<10}{seed_cells}{_fmt_count_rate(row['pooled']):>16}"
            f"{row['sleeperr']:>11}{row.get('contaminated_count', 0):>8}{str(row.get('partial_note') or ''):>14}"
        )

    print("\n=== HYGIENE (deterministic label scorer, offline re-score) ===")
    condition_width = max(9, *(len(condition) + 1 for condition in conditions))
    header = f"{'metric':<28}{'dir':>4}" + "".join(f"{condition:>{condition_width}}" for condition in conditions)
    print(header)
    for name, direction in HYG:
        line = f"{name:<28}{('v' if direction == 'lo' else '^'):>4}"
        for condition in conditions:
            line += f"{_fmt_rate(rows[condition]['hyg'].get(name)):>{condition_width}}"
        print(line)

    print("\n=== DF vs BEST BASELINE (B0-B7) per hygiene metric ===")
    baselines = [condition for condition in conditions if condition.startswith("B")]
    df_wins = df_ties = df_loses = 0
    for name, direction in HYG:
        df_value = rows["DF"]["hyg"].get(name)
        baseline_values = [rows[condition]["hyg"].get(name) for condition in baselines if rows[condition]["hyg"].get(name) is not None]
        if df_value is None or not baseline_values:
            print(f"  {name:<28} DF={df_value} (no baseline data)")
            continue
        best = min(baseline_values) if direction == "lo" else max(baseline_values)
        better = (df_value <= best) if direction == "lo" else (df_value >= best)
        strict = (df_value < best) if direction == "lo" else (df_value > best)
        tag = "WIN " if strict else ("TIE " if better else "LOSE")
        if strict:
            df_wins += 1
        elif better:
            df_ties += 1
        else:
            df_loses += 1
        print(f"  {name:<28}{('v' if direction == 'lo' else '^')}  DF={df_value:.3f}  best_baseline={best:.3f}  -> {tag}")
    print(f"\nDF hygiene vs best-baseline: WIN={df_wins}  TIE={df_ties}  LOSE={df_loses}  (of {len(HYG)})")

    print("\n=== denominator sanity ===")
    zero = [(condition, name) for condition in conditions for name, _ in HYG if not rows[condition]["dens"].get(name)]
    print("zero-denominator cells:", zero if zero else "NONE")
    if zero:
        print("note: UsefulMemoryPrecision is undefined when a condition retrieved no memories for those opportunities.")
    print(
        f"\ncondition coverage (pooled n_S3, expect {len(expected_seeds) * expected_s3}):",
        {condition: rows[condition]["n3"] for condition in conditions},
    )


def write_final_outputs(
    rows: Mapping[str, Mapping[str, Any]],
    *,
    conditions: list[str] = CONDS,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "final_tables.json"
    tex_path = output_dir / "final_tables.tex"
    notes_path = output_dir / "FOLD-NOTES.md"
    json_path.write_text(json.dumps(_final_json(rows, conditions=conditions), indent=2) + "\n", encoding="utf-8")
    tex_path.write_text(render_final_tex(rows, conditions=conditions) + "\n", encoding="utf-8")
    notes_path.write_text(
        render_fold_notes(rows, conditions=conditions, canary_proof_path=output_dir / "CANARY-PROOF.txt") + "\n",
        encoding="utf-8",
    )
    return json_path, tex_path, notes_path


def _final_json(rows: Mapping[str, Mapping[str, Any]], *, conditions: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "metadata": {
            "benchmark_version": BENCHMARK_VERSION,
            "warning": "DreamBench-SWE v1.1 folds must not be merged with v1.0-era seed-1 folds.",
            "conditions": conditions,
            "expected_seeds": list(EXPECTED_SEEDS),
            "headline": "Pass@1",
        },
        "conditions": {},
    }
    for condition in conditions:
        row = rows[condition]
        out["conditions"][condition] = {
            "label": CONDITION_LABELS.get(condition, condition),
            "n_S3": row["n3"],
            "partial": bool(row.get("partial")),
            "partial_note": row.get("partial_note") or "",
            "per_seed": row["per_seed"],
            "pooled": row["pooled"],
            "pass_at_1_rate": {
                "value": row["pass_at_1_rate"],
                "numerator": row["pass_at_1_count"],
                "denominator": row["n3"],
            },
            "final_passed_rate": {
                "value": row["final_passed_rate"],
                "numerator": row["final_passed_count"],
                "denominator": row["n3"],
            },
            "hygiene_metrics": row["hygiene_metrics"],
        }
    return out


def render_final_tex(rows: Mapping[str, Mapping[str, Any]], *, conditions: list[str] = CONDS) -> str:
    lines: list[str] = []
    lines.extend(_render_main_task_table(rows, conditions=conditions))
    lines.append("")
    lines.extend(_render_hygiene_table(rows, conditions=conditions))
    lines.append("")
    lines.extend(_render_ablation_table(rows))
    return "\n".join(lines)


def _render_main_task_table(rows: Mapping[str, Mapping[str, Any]], *, conditions: list[str]) -> list[str]:
    lines = [
        "% Paper Table: main task results for paper/sections/07_results.tex",
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lccc}",
        "\\toprule",
        "Condition & $n$ & Pass@1 & final\\_passed \\\\",
        "\\midrule",
    ]
    for condition in conditions:
        row = rows[condition]
        values = [
            CONDITION_LABELS.get(condition, condition),
            _fmt_n(row),
            _fmt_tex_rate(row["pass_at_1_rate"]),
            _fmt_tex_rate(row["final_passed_rate"]),
        ]
        if condition == "DF":
            values = [_bold(value) for value in values]
        lines.append(" & ".join(values) + r" \\")
    lines.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "\\caption{DreamBench-SWE v1.1 multi-seed hermetic container task results. Pass@1 excludes timeout-then-pass records; final\\_passed reports the final executable oracle result.}",
            "\\label{tab:main-results-final}",
            "\\end{table}",
        ]
    )
    return lines


def _render_hygiene_table(rows: Mapping[str, Mapping[str, Any]], *, conditions: list[str]) -> list[str]:
    headers = [name for name, _ in HYG]
    lines = [
        "% Paper Table: memory-hygiene metrics for paper/sections/07_results.tex",
        "\\begin{table}[t]",
        "\\centering",
        "\\scriptsize",
        "\\resizebox{\\textwidth}{!}{%",
        "\\begin{tabular}{l" + "c" * len(headers) + "}",
        "\\toprule",
        "Condition & " + " & ".join(_metric_label(name) for name in headers) + r" \\",
        "\\midrule",
    ]
    for condition in conditions:
        row = rows[condition]
        values = [CONDITION_LABELS.get(condition, condition)]
        values.extend(_fmt_metric(row["hygiene_metrics"][name]) for name in headers)
        if condition == "DF":
            values = [_bold(value) for value in values]
        lines.append(" & ".join(values) + r" \\")
    lines.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "}%",
            "\\caption{DreamBench-SWE v1.1 multi-seed hermetic container hygiene metrics. Values are offline deterministic re-scores from result records; n/a means denominator zero.}",
            "\\label{tab:hygiene-final}",
            "\\end{table}",
        ]
    )
    return lines


def _render_ablation_table(rows: Mapping[str, Mapping[str, Any]]) -> list[str]:
    conditions = ["DF", "A0", "A2", "A4", "A5", "A6", "A11"]
    key_metrics = ["ContradictionRepairAccuracy", "HumanFeedbackUseAccuracy", "TransferScore", "ScopeAccuracy", "RegressionAfterUpdate"]
    lines = [
        "% Paper Table: operation ablations for paper/sections/07_results.tex",
        "\\begin{table}[t]",
        "\\centering",
        "\\scriptsize",
        "\\resizebox{\\textwidth}{!}{%",
        "\\begin{tabular}{llcccccccc}",
        "\\toprule",
        "Condition & Component removed & $n$ & Pass@1 & final\\_passed & Contra. repair & HF use & Transfer & Scope acc. & Regression \\\\",
        "\\midrule",
    ]
    for condition in conditions:
        row = rows[condition]
        values = [
            CONDITION_LABELS.get(condition, condition),
            ABLATION_REMOVED[condition],
            _fmt_n(row),
            _fmt_tex_rate(row["pass_at_1_rate"]),
            _fmt_tex_rate(row["final_passed_rate"]),
        ]
        values.extend(_fmt_metric(row["hygiene_metrics"][name]) for name in key_metrics)
        if condition == "DF":
            values = [_bold(value) for value in values]
        lines.append(" & ".join(values) + r" \\")
    lines.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "}%",
            "\\caption{DreamBench-SWE v1.1 multi-seed ablations. Partial cells should not be compared as complete 66-sequence conditions.}",
            "\\label{tab:ablations-final}",
            "\\end{table}",
        ]
    )
    return lines


def render_fold_notes(
    rows: Mapping[str, Mapping[str, Any]],
    *,
    conditions: list[str] = CONDS,
    canary_proof_path: Path = CANARY_PROOF_PATH,
) -> str:
    df = rows["DF"]
    baselines = [condition for condition in conditions if condition.startswith("B")]
    best_baseline = max(baselines, key=lambda condition: rows[condition]["pass_at_1_rate"] or -1)
    best = rows[best_baseline]
    margin = (df["pass_at_1_rate"] or 0.0) - (best["pass_at_1_rate"] or 0.0)
    df_fails = _failed_s3_cells(df)
    b0 = rows["B0"]
    a11 = rows["A11"]
    a11_expected = _expected_pooled_s3(a11)
    canary = canary_proof_status(canary_proof_path)
    b0_pass_note = (
        f"- Pass@1 and final_passed differ for B0: Pass@1 { _fmt_fraction(b0['pass_at_1_count'], b0['n3']) } "
        f"but final_passed {_fmt_fraction(b0['final_passed_count'], b0['n3'])}. Timeout-then-pass records are excluded from Pass@1."
        if b0["pass_at_1_count"] != b0["final_passed_count"]
        else f"- Pass@1 and final_passed match for B0 in this fold: {_fmt_fraction(b0['pass_at_1_count'], b0['n3'])}."
    )
    a11_note = (
        f"- A11 is partial: n={a11['n3']}/{a11_expected} S3 cells, Pass@1 "
        f"{_fmt_fraction(a11['pass_at_1_count'], a11['n3'])} = {_fmt_tex_rate(a11['pass_at_1_rate'])}. "
        "Do not pad missing cells or compare it as a complete condition."
        if a11.get("partial")
        else f"- A11 is complete for the configured seed set: n={a11['n3']} S3 cells, Pass@1 "
        f"{_fmt_fraction(a11['pass_at_1_count'], a11['n3'])} = {_fmt_tex_rate(a11['pass_at_1_rate'])}."
    )
    lines = [
        "# FOLD NOTES",
        "",
        "## HONEST NOTES",
        "",
        (
            f"- DF Pass@1 is {_fmt_fraction(df['pass_at_1_count'], df['n3'])} = "
            f"{_fmt_tex_rate(df['pass_at_1_rate'])}. The best baseline is {best_baseline} at "
            f"{_fmt_fraction(best['pass_at_1_count'], best['n3'])} = {_fmt_tex_rate(best['pass_at_1_rate'])}; "
            f"margin = +{df['pass_at_1_count'] - best['pass_at_1_count']}/{df['n3']} = +{margin:.3f}."
        ),
        b0_pass_note,
        (
            "- Hygiene artifact: no-memory baselines are not comparable on retrieval-driven hygiene. "
            "B0 has no retrieved memories, so UsefulMemoryPrecision is n/a and retrieval-coupled stale/harmful activation can be trivially zero. "
            "In this scorer, RepeatedErrorRate and RegressionAfterUpdate are behavior-level labels and are nonzero for B0; do not frame apparent no-memory wins as real memory-hygiene wins. "
            "Frame DF's meaningful hygiene evidence on memory-use metrics: ContradictionRepairAccuracy, HumanFeedbackUseAccuracy, TransferScore, and ScopeAccuracy."
        ),
        a11_note,
        "- Multi-seed scope: pooled rates are the union of v1.1 seed/sequence cells. Do not merge these with v1.0-era seed-1 folds.",
        (
            f"- Hermeticity gate note: container records are scanned for hidden-path/token evidence. "
            f"Canary proof artifact exists={str(canary['exists']).lower()} "
            f"({canary['bytes']} bytes at {_display_path(Path(canary['path']))})."
        ),
        (
            f"- DF fails {len(df_fails)} pooled S3 cells in the actual records: "
            + ", ".join(df_fails)
            + "."
        ),
    ]
    return "\n".join(lines)


def _display_path(path: Path) -> str:
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _expected_pooled_s3(row: Mapping[str, Any]) -> int:
    per_seed = row.get("per_seed") if isinstance(row.get("per_seed"), Mapping) else {}
    total = 0
    for summary in per_seed.values():
        if isinstance(summary, Mapping):
            total += int(summary.get("expected_s3") or 0)
    return total or int(row.get("n3") or 0)


def _failed_s3_cells(row: Mapping[str, Any]) -> list[str]:
    records = row.get("records") or []
    return sorted(
        f"s{_seed(record)}:{_sequence_id(record)}"
        for record in records
        if isinstance(record, Mapping) and _session_index(record) == 3 and not record.get("pass_at_1")
    )


def _fmt_tex_rate(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, float) else "n/a"


def _fmt_fraction(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}"


def _fmt_n(row: Mapping[str, Any]) -> str:
    if row.get("partial"):
        return f"{row['n3']} (partial)"
    return str(row["n3"])


def _fmt_metric(metric: Mapping[str, Any]) -> str:
    value = metric.get("value")
    if value == "n/a" or value is None:
        return "n/a"
    return _fmt_tex_rate(value)


def _metric_label(name: str) -> str:
    labels = {
        "StaleMemoryActivationRate": "Stale",
        "RepeatedErrorRate": "Repeated",
        "HarmfulMemoryRate": "Harmful",
        "RegressionAfterUpdate": "Regression",
        "ContradictionRepairAccuracy": "Contra. repair",
        "HumanFeedbackUseAccuracy": "HF use",
        "UsefulMemoryPrecision": "Useful prec.",
        "ScopeAccuracy": "Scope acc.",
        "TransferScore": "Transfer",
    }
    return labels.get(name, name)


def _bold(value: str) -> str:
    return f"\\textbf{{{value}}}"


def _parse_int_csv(value: str) -> tuple[int, ...]:
    seeds: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        seeds.append(int(item))
    if not seeds:
        raise argparse.ArgumentTypeError("expected at least one seed")
    return tuple(seeds)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{BENCHMARK_VERSION} validity-gated multi-seed fold.")
    parser.add_argument("--sequences", type=Path, default=SEQUENCES_PATH)
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--conditions", default=",".join(CONDS), help="Comma-separated condition ids to fold.")
    parser.add_argument("--expected-s3", type=int, default=EXPECTED_S3)
    parser.add_argument("--expected-seeds", type=_parse_int_csv, default=EXPECTED_SEEDS)
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args(argv)


def _parse_conditions(value: str) -> list[str]:
    conditions = [item.strip() for item in value.split(",") if item.strip()]
    if not conditions:
        raise SystemExit("--conditions must include at least one condition")
    return conditions


def _headline_rate(rows: Mapping[str, Mapping[str, Any]], condition: str, metric: str = "pass_at_1_rate") -> str:
    row = rows.get(condition)
    if not row:
        return "n/a"
    return _fmt_tex_rate(row.get(metric))


def _headline_partial(rows: Mapping[str, Mapping[str, Any]], condition: str) -> str:
    row = rows.get(condition)
    if not row:
        return "not_folded"
    return str(row.get("partial_note") or "complete")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(ROOT)
    sequences = load_sequences(args.sequences)
    conditions = _parse_conditions(args.conditions)
    rows, excluded, coverage_errors = build_rows(
        sequences=sequences,
        conditions=conditions,
        expected_s3=args.expected_s3,
        expected_seeds=args.expected_seeds,
        results_root=args.results_root,
    )
    print_report(
        rows,
        conditions=conditions,
        excluded=excluded,
        coverage_errors=coverage_errors,
        expected_seeds=args.expected_seeds,
        expected_s3=args.expected_s3,
        canary_proof_path=args.output_dir / "CANARY-PROOF.txt",
    )
    no_write = args.no_write or os.environ.get("BARRIER2_NO_WRITE", "").lower() in {"1", "true", "yes"}
    print("\n=== STDOUT SUMMARY ===")
    if no_write:
        print("Skipped writing final fold outputs because BARRIER2_NO_WRITE=1")
    else:
        json_path, tex_path, notes_path = write_final_outputs(rows, conditions=conditions, output_dir=args.output_dir)
        print(f"Wrote {json_path}")
        print(f"Wrote {tex_path}")
        print(f"Wrote {notes_path}")
    print(
        "headline: "
        f"DF Pass@1={_headline_rate(rows, 'DF')} "
        f"B5-Instance Pass@1={_headline_rate(rows, 'B5')} "
        f"B5-MEM0 Pass@1={_headline_rate(rows, 'B5-MEM0')} "
        f"B0 Pass@1={_headline_rate(rows, 'B0')} "
        f"B0 final_passed={_headline_rate(rows, 'B0', 'final_passed_rate')} "
        f"A0 Pass@1={_headline_rate(rows, 'A0')} "
        f"A11={_headline_partial(rows, 'A11')}"
    )
    print("ablations_present=yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
