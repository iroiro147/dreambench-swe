#!/usr/bin/env python3
"""Build the v2 pass@1/cost frontier from folded or raw run artifacts.

The runner in ``src/experiments/run_bench.py`` records task outcomes per record
and records token, latency, and cost accounting in ``manifest``. This tool also
accepts already-flat per-record rows so fixtures and future fold exports can use
the same Pareto logic.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from experiments.run_bench import TOKEN_PRICE_PER_MILLION
except Exception:  # pragma: no cover - import is expected to work in-repo.
    TOKEN_PRICE_PER_MILLION = {
        "glm-latest": {"input": 0.0, "output": 0.0},
        "kimi-latest": {"input": 0.0, "output": 0.0},
        "grid:glm-latest": {"input": 0.0, "output": 0.0},
        "grid:kimi-latest": {"input": 0.0, "output": 0.0},
        "gpt-5.5": {"input": 5.0, "output": 30.0},
        "codex": {"input": 5.0, "output": 30.0},
        "stub": {"input": 0.0, "output": 0.0},
    }


EPSILON = 1e-12
DEFAULT_COST_METRIC = "CostPerSuccessfulTaskUSD"
PENDING = "[" + "V2-" + "PENDING]"


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "" or value == "n/a":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "pass", "passed", "success"}
    return False


def first_float(*values: Any) -> float | None:
    for value in values:
        parsed = coerce_float(value)
        if parsed is not None:
            return parsed
    return None


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or abs(denominator) <= EPSILON:
        return None
    return numerator / denominator


def round_clean(value: Any, digits: int = 12) -> float | None:
    parsed = coerce_float(value)
    if parsed is None:
        return None
    rounded = round(parsed, digits)
    if abs(rounded) <= EPSILON:
        return 0.0
    if abs(rounded - round(rounded)) <= EPSILON:
        return float(int(round(rounded)))
    return rounded


def estimate_model_cost(model: str | None, input_tokens: float, output_tokens: float = 0.0) -> float:
    price = TOKEN_PRICE_PER_MILLION.get(str(model or "stub"), {"input": 0.0, "output": 0.0})
    if isinstance(price, Mapping):
        input_price = float(price.get("input", 0.0) or 0.0)
        output_price = float(price.get("output", 0.0) or 0.0)
    else:
        input_price = float(price or 0.0)
        output_price = 0.0
    return (float(input_tokens) * input_price + float(output_tokens) * output_price) / 1_000_000.0


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _condition_from_record(record: Mapping[str, Any], fallback: str | None = None) -> str:
    value = (
        record.get("condition")
        or record.get("condition_id")
        or _nested(record, "manifest", "condition")
        or _nested(record, "task", "condition")
        or fallback
    )
    return str(value or "UNKNOWN")


def _record_success(record: Mapping[str, Any]) -> bool:
    return coerce_bool(record.get("final_passed") if "final_passed" in record else record.get("passed"))


def _record_pass_at_1(record: Mapping[str, Any]) -> bool:
    if "pass_at_1" in record:
        return coerce_bool(record.get("pass_at_1"))
    return _record_success(record)


def record_token_breakdown(record: Mapping[str, Any]) -> dict[str, float]:
    tokens = as_mapping(record.get("tokens") or record.get("token_breakdown"))
    usage = as_mapping(_nested(record, "agent_metadata", "usage") or record.get("usage"))
    agent_result = as_mapping(record.get("agent_result"))

    wake_input = first_float(tokens.get("wake_input"), record.get("wake_input_tokens"), record.get("input_tokens"), usage.get("prompt_tokens")) or 0.0
    wake_output = first_float(
        tokens.get("wake_output"),
        record.get("wake_output_tokens"),
        record.get("output_tokens"),
        usage.get("completion_tokens"),
    ) or 0.0
    usage_total = first_float(usage.get("total_tokens"))
    agent_tokens = first_float(agent_result.get("tokens"), record.get("agent_tokens"))
    wake = first_float(tokens.get("wake"), record.get("wake_tokens"), usage_total, agent_tokens)
    if wake is None:
        wake = wake_input + wake_output
    if wake_input == 0.0 and wake_output == 0.0 and wake > 0.0:
        wake_input = wake

    sleep = first_float(tokens.get("sleep"), record.get("sleep_tokens"), record.get("sleep_maintenance_tokens")) or 0.0
    judge = first_float(tokens.get("judge"), record.get("judge_tokens")) or 0.0
    total = first_float(tokens.get("total"), record.get("total_tokens"))
    if total is None:
        total = wake + sleep + judge
    admitted = first_float(tokens.get("admitted_memory_total"), record.get("admitted_memory_tokens")) or 0.0

    return {
        "wake_input": wake_input,
        "wake_output": wake_output,
        "wake": wake,
        "sleep": sleep,
        "judge": judge,
        "admitted_memory_total": admitted,
        "total": total,
    }


def record_latency_breakdown(record: Mapping[str, Any]) -> dict[str, float]:
    latency = as_mapping(record.get("latency_seconds") or record.get("latency"))
    wake = first_float(latency.get("wake"), record.get("wake_latency_seconds"), record.get("latency_seconds")) or 0.0
    sleep = first_float(latency.get("sleep"), record.get("sleep_latency_seconds"), record.get("sleep_maintenance_latency_seconds")) or 0.0
    judge = first_float(latency.get("judge"), record.get("judge_latency_seconds")) or 0.0
    total = first_float(latency.get("total"), record.get("total_latency_seconds"))
    if total is None:
        total = wake + sleep + judge
    return {"wake": wake, "sleep": sleep, "judge": judge, "total": total}


def record_cost_breakdown(record: Mapping[str, Any], tokens: Mapping[str, float]) -> dict[str, float]:
    cost = as_mapping(record.get("cost") or record.get("estimated_cost_breakdown"))
    model = str(record.get("model") or record.get("agent_model") or _nested(record, "manifest", "model") or "stub")
    wake_model = first_float(cost.get("wake_model"), cost.get("wake"), record.get("wake_model_cost"))
    if wake_model is None:
        wake_model = estimate_model_cost(model, tokens.get("wake_input", 0.0), tokens.get("wake_output", 0.0))

    sleep_model = str(record.get("sleep_model") or "stub")
    sleep = first_float(
        cost.get("sleep"),
        cost.get("sleep_maintenance"),
        record.get("sleep_cost"),
        record.get("sleep_maintenance_cost"),
        record.get("sleep_maintenance_cost_usd"),
    )
    if sleep is None:
        sleep = estimate_model_cost(sleep_model, tokens.get("sleep", 0.0), 0.0)

    judge = first_float(cost.get("judge"), record.get("judge_cost"), record.get("judge_cost_usd")) or 0.0
    total = first_float(record.get("estimated_cost"), record.get("estimated_cost_usd"), cost.get("total"))
    if total is None:
        total = wake_model + sleep + judge
    return {"wake_model": wake_model, "sleep": sleep, "judge": judge, "total": total}


@dataclass
class ConditionAccumulator:
    condition: str
    n_records: int = 0
    successful_tasks: int = 0
    pass_at_1_successes: int = 0
    tokens: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    latency_seconds: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    costs: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    source_files: set[str] = field(default_factory=set)

    def add_outcomes(self, records: Sequence[Mapping[str, Any]]) -> None:
        self.n_records += len(records)
        self.successful_tasks += sum(1 for record in records if _record_success(record))
        self.pass_at_1_successes += sum(1 for record in records if _record_pass_at_1(record))

    def add_record_accounting(self, record: Mapping[str, Any], *, source: str | None = None) -> None:
        self.add_outcomes([record])
        tokens = record_token_breakdown(record)
        latency = record_latency_breakdown(record)
        cost = record_cost_breakdown(record, tokens)
        for key, value in tokens.items():
            self.tokens[key] += float(value)
        for key, value in latency.items():
            self.latency_seconds[key] += float(value)
        for key, value in cost.items():
            self.costs[key] += float(value)
        if source:
            self.source_files.add(source)

    def add_manifest_accounting(
        self,
        manifest: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        *,
        source: str | None = None,
    ) -> None:
        self.add_outcomes(records)
        tokens = manifest_token_breakdown(manifest)
        latency = manifest_latency_breakdown(manifest)
        cost = manifest_cost_breakdown(manifest, tokens)
        for key, value in tokens.items():
            self.tokens[key] += float(value)
        for key, value in latency.items():
            self.latency_seconds[key] += float(value)
        for key, value in cost.items():
            self.costs[key] += float(value)
        if source:
            self.source_files.add(source)

    def add_summary(self, summary: Mapping[str, Any], *, source: str | None = None) -> None:
        self.n_records += int(
            first_float(
                summary.get("n_records"),
                summary.get("n"),
                summary.get("denominator"),
                _nested(summary, "pass_at_1", "denominator"),
                _nested(summary, "overall_task_success", "denominator"),
            )
            or 0.0
        )
        success_count = first_float(summary.get("successful_tasks"), summary.get("successes"))
        if success_count is None:
            success_count = first_float(_nested(summary, "overall_task_success", "numerator"))
        if success_count is not None:
            self.successful_tasks += int(success_count)
        pass_count = first_float(summary.get("pass_at_1_successes"), summary.get("pass_at_1_count"))
        if pass_count is None:
            pass_count = first_float(_nested(summary, "pass_at_1", "numerator"))
        if pass_count is not None:
            self.pass_at_1_successes += int(pass_count)
        token_breakdown = as_mapping(summary.get("token_breakdown"))
        self.tokens["wake"] += first_float(token_breakdown.get("wake"), summary.get("WakeTokens")) or 0.0
        self.tokens["sleep"] += first_float(token_breakdown.get("sleep"), summary.get("SleepTokens")) or 0.0
        self.tokens["judge"] += first_float(token_breakdown.get("judge"), summary.get("JudgeTokens")) or 0.0
        total_tokens = first_float(summary.get("total_tokens"), summary.get("TotalTokens"), token_breakdown.get("total"))
        if total_tokens is None:
            total_tokens = self.tokens["wake"] + self.tokens["sleep"] + self.tokens["judge"]
        self.tokens["total"] += total_tokens or 0.0
        sleep_cost = first_float(summary.get("sleep_maintenance_cost"), summary.get("SleepMaintenanceCostUSD"))
        if sleep_cost is not None:
            self.costs["sleep"] += sleep_cost
        estimated = first_float(summary.get("estimated_cost"), summary.get("EstimatedCostUSD"))
        if estimated is not None:
            self.costs["total"] += estimated
        cps = first_float(summary.get("cost_per_successful_task"), summary.get("CostPerSuccessfulTaskUSD"))
        if cps is not None and self.successful_tasks > 0 and "total" not in self.costs:
            self.costs["total"] += cps * self.successful_tasks
        if source:
            self.source_files.add(source)

    def to_row(self) -> dict[str, Any]:
        estimated_cost = self.costs.get("total")
        if estimated_cost is None:
            estimated_cost = sum(self.costs.get(key, 0.0) for key in ("wake_model", "sleep", "judge"))
        pass_at_1 = safe_div(float(self.pass_at_1_successes), float(self.n_records))
        row = {
            "condition": self.condition,
            "n_records": self.n_records,
            "successful_tasks": self.successful_tasks,
            "pass_at_1_successes": self.pass_at_1_successes,
            "PassAt1": round_clean(pass_at_1),
            "WakeTokens": round_clean(self.tokens.get("wake", 0.0)),
            "SleepTokens": round_clean(self.tokens.get("sleep", 0.0)),
            "JudgeTokens": round_clean(self.tokens.get("judge", 0.0)),
            "TotalTokens": round_clean(self.tokens.get("total", 0.0)),
            "SleepMaintenanceCostUSD": round_clean(self.costs.get("sleep", 0.0)),
            "EstimatedCostUSD": round_clean(estimated_cost),
            "CostPerSuccessfulTaskUSD": round_clean(safe_div(estimated_cost, float(self.successful_tasks))),
            "TotalLatencySeconds": round_clean(self.latency_seconds.get("total", 0.0)),
            "source_count": len(self.source_files),
            "source_files": sorted(self.source_files),
        }
        row["on_pareto_frontier"] = False
        return row


def manifest_token_breakdown(manifest: Mapping[str, Any]) -> dict[str, float]:
    tokens = as_mapping(manifest.get("tokens") or manifest.get("token_breakdown"))
    wake_input = first_float(tokens.get("wake_input")) or 0.0
    wake_output = first_float(tokens.get("wake_output")) or 0.0
    wake = first_float(tokens.get("wake"))
    if wake is None:
        wake = wake_input + wake_output
    sleep = first_float(tokens.get("sleep")) or 0.0
    judge = first_float(tokens.get("judge")) or 0.0
    total = first_float(tokens.get("total"))
    if total is None:
        total = wake + sleep + judge
    admitted = first_float(tokens.get("admitted_memory_total")) or 0.0
    return {
        "wake_input": wake_input,
        "wake_output": wake_output,
        "wake": wake,
        "sleep": sleep,
        "judge": judge,
        "admitted_memory_total": admitted,
        "total": total,
    }


def manifest_latency_breakdown(manifest: Mapping[str, Any]) -> dict[str, float]:
    latency = as_mapping(manifest.get("latency_seconds") or manifest.get("latency"))
    wake = first_float(latency.get("wake")) or 0.0
    sleep = first_float(latency.get("sleep")) or 0.0
    judge = first_float(latency.get("judge")) or 0.0
    total = first_float(latency.get("total"))
    if total is None:
        total = wake + sleep + judge
    return {"wake": wake, "sleep": sleep, "judge": judge, "total": total}


def manifest_cost_breakdown(manifest: Mapping[str, Any], tokens: Mapping[str, float]) -> dict[str, float]:
    breakdown = as_mapping(manifest.get("estimated_cost_breakdown") or manifest.get("cost_breakdown"))
    model = str(manifest.get("model") or "stub")
    wake_model = first_float(breakdown.get("wake_model"), breakdown.get("wake"))
    if wake_model is None:
        wake_model = estimate_model_cost(model, tokens.get("wake_input", 0.0), tokens.get("wake_output", 0.0))
    sleep = first_float(breakdown.get("sleep"), breakdown.get("sleep_maintenance"))
    if sleep is None:
        sleep = estimate_model_cost("stub", tokens.get("sleep", 0.0), 0.0)
    judge = first_float(breakdown.get("judge")) or 0.0
    total = first_float(manifest.get("estimated_cost"), breakdown.get("total"))
    if total is None:
        total = wake_model + sleep + judge
    return {"wake_model": wake_model, "sleep": sleep, "judge": judge, "total": total}


def _manifest_has_accounting(manifest: Mapping[str, Any]) -> bool:
    return bool(
        as_mapping(manifest.get("tokens"))
        or as_mapping(manifest.get("latency_seconds"))
        or as_mapping(manifest.get("estimated_cost_breakdown"))
        or coerce_float(manifest.get("estimated_cost")) is not None
    )


def _accumulator(accumulators: dict[str, ConditionAccumulator], condition: str) -> ConditionAccumulator:
    if condition not in accumulators:
        accumulators[condition] = ConditionAccumulator(condition)
    return accumulators[condition]


def build_cost_frontier_from_records(
    records: Sequence[Mapping[str, Any]],
    *,
    cost_metric: str = DEFAULT_COST_METRIC,
) -> dict[str, Any]:
    accumulators: dict[str, ConditionAccumulator] = {}
    for record in records:
        condition = _condition_from_record(record)
        _accumulator(accumulators, condition).add_record_accounting(record)
    rows = [accumulator.to_row() for accumulator in accumulators.values()]
    flag_pareto_frontier(rows, cost_metric=cost_metric)
    rows.sort(key=lambda row: _row_sort_key(row, cost_metric))
    return _analysis_payload(rows, source_kind="records", cost_metric=cost_metric)


def build_cost_frontier_from_payloads(
    payloads: Sequence[Mapping[str, Any]],
    *,
    cost_metric: str = DEFAULT_COST_METRIC,
) -> dict[str, Any]:
    accumulators: dict[str, ConditionAccumulator] = {}
    for payload in payloads:
        source = str(payload.get("_source_path") or "")
        if _looks_like_fold_summary(payload):
            for condition, summary in as_mapping(payload.get("conditions")).items():
                _accumulator(accumulators, str(condition)).add_summary(as_mapping(summary), source=source)
            continue

        records = [record for record in payload.get("records", []) if isinstance(record, Mapping)]
        manifest = as_mapping(payload.get("manifest"))
        fallback_condition = str(manifest.get("condition") or payload.get("condition") or "UNKNOWN")
        if records and _manifest_has_accounting(manifest):
            condition = fallback_condition
            _accumulator(accumulators, condition).add_manifest_accounting(manifest, records, source=source)
        else:
            for record in records:
                condition = _condition_from_record(record, fallback_condition)
                _accumulator(accumulators, condition).add_record_accounting(record, source=source)
    rows = [accumulator.to_row() for accumulator in accumulators.values()]
    flag_pareto_frontier(rows, cost_metric=cost_metric)
    rows.sort(key=lambda row: _row_sort_key(row, cost_metric))
    return _analysis_payload(rows, source_kind="payloads", cost_metric=cost_metric)


def _looks_like_fold_summary(payload: Mapping[str, Any]) -> bool:
    conditions = payload.get("conditions")
    if not isinstance(conditions, Mapping):
        return False
    return any(isinstance(item, Mapping) for item in conditions.values())


def _analysis_payload(rows: Sequence[Mapping[str, Any]], *, source_kind: str, cost_metric: str) -> dict[str, Any]:
    return {
        "metadata": {
            "source_kind": source_kind,
            "cost_metric": cost_metric,
            "price_map_source": "src/experiments/run_bench.py:TOKEN_PRICE_PER_MILLION",
            "sleep_cost_rule": "Use explicit sleep cost when present; otherwise price sleep tokens with run_bench's stub price.",
            "pending_note": PENDING if not rows else None,
        },
        "conditions": list(rows),
        "pareto_table_markdown": render_pareto_table(rows, cost_metric=cost_metric),
        "latex": render_latex_frontier(rows, cost_metric=cost_metric),
    }


def _row_sort_key(row: Mapping[str, Any], cost_metric: str) -> tuple[int, float, str]:
    cost = coerce_float(row.get(cost_metric))
    return (0 if cost is not None else 1, cost if cost is not None else math.inf, str(row.get("condition") or ""))


def flag_pareto_frontier(rows: Sequence[dict[str, Any]], *, cost_metric: str = DEFAULT_COST_METRIC) -> None:
    eligible = [
        row
        for row in rows
        if coerce_float(row.get("PassAt1")) is not None and coerce_float(row.get(cost_metric)) is not None
    ]
    for row in rows:
        row["on_pareto_frontier"] = False
    for row in eligible:
        row_pass = float(row["PassAt1"])
        row_cost = float(row[cost_metric])
        dominated = False
        for other in eligible:
            if other is row:
                continue
            other_pass = float(other["PassAt1"])
            other_cost = float(other[cost_metric])
            at_least_as_good = other_pass >= row_pass - EPSILON and other_cost <= row_cost + EPSILON
            strictly_better = other_pass > row_pass + EPSILON or other_cost < row_cost - EPSILON
            if at_least_as_good and strictly_better:
                dominated = True
                break
        row["on_pareto_frontier"] = not dominated


def fmt_number(value: Any, digits: int = 6) -> str:
    parsed = coerce_float(value)
    if parsed is None:
        return "n/a"
    if abs(parsed - round(parsed)) <= EPSILON:
        return str(int(round(parsed)))
    return f"{parsed:.{digits}f}".rstrip("0").rstrip(".")


def render_pareto_table(rows: Sequence[Mapping[str, Any]], *, cost_metric: str = DEFAULT_COST_METRIC) -> str:
    if not rows:
        return f"| Condition | Pass@1 | {cost_metric} | Pareto |\n|---|---:|---:|---|\n| {PENDING} | {PENDING} | {PENDING} | {PENDING} |"
    lines = [
        f"| Condition | Pass@1 | {cost_metric} | EstimatedCostUSD | TotalTokens | SleepMaintenanceCostUSD | Pareto |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("condition") or ""),
                    fmt_number(row.get("PassAt1"), 3),
                    fmt_number(row.get(cost_metric), 6),
                    fmt_number(row.get("EstimatedCostUSD"), 6),
                    fmt_number(row.get("TotalTokens"), 0),
                    fmt_number(row.get("SleepMaintenanceCostUSD"), 6),
                    "yes" if row.get("on_pareto_frontier") else "no",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def tex_escape(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("_", r"\_")
        .replace("%", r"\%")
        .replace("&", r"\&")
        .replace("#", r"\#")
    )


def render_latex_frontier(
    rows: Sequence[Mapping[str, Any]],
    *,
    cost_metric: str = DEFAULT_COST_METRIC,
    label: str = "fig:v2-cost-frontier",
) -> str:
    result_note = (
        "The plotted v2 numeric results are computed from the supplied fold artifact."
        if rows
        else f"Live v2 numeric results remain {PENDING} until the fold artifact is supplied."
    )
    caption = (
        "\\caption{Cost frontier for the v2 fold. Points plot Pass@1 against "
        f"{tex_escape(cost_metric)} computed from run artifacts; frontier points are nondominated "
        f"under higher Pass@1 and lower cost. {result_note}}}"
    )
    lines = [
        "% Cost-frontier figure caption and pgfplots coordinates.",
        caption,
        f"\\label{{{label}}}",
        "% PARETO_COORDINATES_BEGIN",
        f"% x={cost_metric}, y=PassAt1",
    ]
    eligible = [
        row
        for row in rows
        if coerce_float(row.get("PassAt1")) is not None and coerce_float(row.get(cost_metric)) is not None
    ]
    if not eligible:
        lines.extend([f"% {PENDING}", "% PARETO_COORDINATES_END"])
        return "\n".join(lines)

    lines.append("\\addplot+[only marks] coordinates {")
    for row in eligible:
        lines.append(
            f"  ({fmt_number(row.get(cost_metric), 9)},{fmt_number(row.get('PassAt1'), 9)})"
            f" % {tex_escape(str(row.get('condition') or ''))}"
        )
    lines.append("};")
    lines.append("\\addplot+[only marks,mark=*] coordinates {")
    for row in eligible:
        if row.get("on_pareto_frontier"):
            lines.append(
                f"  ({fmt_number(row.get(cost_metric), 9)},{fmt_number(row.get('PassAt1'), 9)})"
                f" % {tex_escape(str(row.get('condition') or ''))}"
            )
    lines.append("};")
    lines.append("% PARETO_COORDINATES_END")
    return "\n".join(lines)


def read_json_or_jsonl(path: Path) -> list[Mapping[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix == ".jsonl":
        out: list[Mapping[str, Any]] = []
        for line in text.splitlines():
            item = json.loads(line)
            if isinstance(item, Mapping):
                out.append(dict(item, _source_path=str(path)))
        return out
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return [dict(item, _source_path=str(path)) for item in parsed if isinstance(item, Mapping)]
    if isinstance(parsed, Mapping):
        return [dict(parsed, _source_path=str(path))]
    return []


def expand_input_paths(paths: Sequence[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(sorted(path.glob("**/results.json")))
            continue
        expanded.append(path)
    return expanded


def load_payloads(paths: Sequence[Path]) -> list[Mapping[str, Any]]:
    payloads: list[Mapping[str, Any]] = []
    for path in expand_input_paths(paths):
        payloads.extend(read_json_or_jsonl(path))
    return payloads


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the DreamForge v2 cost frontier.")
    parser.add_argument(
        "--input",
        nargs="*",
        type=Path,
        default=[],
        help="JSON, JSONL, raw results.json, folded summary JSON, or directories containing results.json.",
    )
    parser.add_argument(
        "--cost-metric",
        choices=("CostPerSuccessfulTaskUSD", "EstimatedCostUSD"),
        default=DEFAULT_COST_METRIC,
    )
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-tex", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payloads = load_payloads(args.input)
    if payloads:
        analysis = build_cost_frontier_from_payloads(payloads, cost_metric=args.cost_metric)
    else:
        analysis = _analysis_payload([], source_kind="none", cost_metric=args.cost_metric)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output_tex:
        args.output_tex.parent.mkdir(parents=True, exist_ok=True)
        args.output_tex.write_text(analysis["latex"] + "\n", encoding="utf-8")

    print(analysis["pareto_table_markdown"])
    print("")
    print(analysis["latex"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
