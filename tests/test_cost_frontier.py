"""Tests for scripts/cost_frontier.py."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cost_frontier  # noqa: E402


PENDING_MARKER = "[" + "V2-" + "PENDING]"


def _record(condition: str, *, passed: bool, cost: float, tokens: int = 100) -> dict:
    return {
        "condition": condition,
        "final_passed": passed,
        "pass_at_1": passed,
        "tokens": {"wake": tokens, "sleep": 5, "judge": 1, "total": tokens + 6},
        "estimated_cost": cost,
    }


def test_pareto_frontier_marks_only_nondominated_conditions() -> None:
    analysis = cost_frontier.build_cost_frontier_from_records(
        [
            _record("cheap-low", passed=True, cost=1.0),
            _record("cheap-low", passed=False, cost=1.0),
            _record("expensive-mid", passed=True, cost=2.0),
            _record("expensive-mid", passed=False, cost=2.0),
            _record("balanced-high", passed=True, cost=3.0),
            _record("balanced-high", passed=True, cost=3.0),
            _record("overcost-high", passed=True, cost=5.0),
            _record("overcost-high", passed=True, cost=5.0),
        ]
    )
    by_condition = {row["condition"]: row for row in analysis["conditions"]}

    assert by_condition["cheap-low"]["PassAt1"] == 0.5
    assert by_condition["cheap-low"]["CostPerSuccessfulTaskUSD"] == 2.0
    assert by_condition["balanced-high"]["PassAt1"] == 1.0
    assert by_condition["balanced-high"]["CostPerSuccessfulTaskUSD"] == 3.0
    assert by_condition["cheap-low"]["on_pareto_frontier"] is True
    assert by_condition["balanced-high"]["on_pareto_frontier"] is True
    assert by_condition["expensive-mid"]["on_pareto_frontier"] is False
    assert by_condition["overcost-high"]["on_pareto_frontier"] is False


def test_run_bench_manifest_fields_are_used_for_raw_results(tmp_path: Path) -> None:
    results_path = tmp_path / "results.json"
    payload = {
        "manifest": {
            "condition": "DF",
            "model": "gpt-5.5",
            "tokens": {
                "wake_input": 100,
                "wake_output": 10,
                "wake": 110,
                "sleep": 7,
                "judge": 13,
                "total": 130,
            },
            "latency_seconds": {"wake": 1.0, "sleep": 0.25, "judge": 0.75, "total": 2.0},
            "estimated_cost": 0.001,
            "estimated_cost_breakdown": {"wake_model": 0.0008, "sleep": 0.0002},
        },
        "records": [
            {"final_passed": True, "pass_at_1": True, "agent_metadata": {"usage": {"total_tokens": 999}}},
            {"final_passed": False, "pass_at_1": False, "agent_metadata": {"usage": {"total_tokens": 999}}},
        ],
    }
    results_path.write_text(json.dumps(payload), encoding="utf-8")

    analysis = cost_frontier.build_cost_frontier_from_payloads(cost_frontier.load_payloads([results_path]))
    row = analysis["conditions"][0]

    assert row["condition"] == "DF"
    assert row["n_records"] == 2
    assert row["pass_at_1_successes"] == 1
    assert row["PassAt1"] == 0.5
    assert row["WakeTokens"] == 110
    assert row["SleepTokens"] == 7
    assert row["JudgeTokens"] == 13
    assert row["TotalTokens"] == 130
    assert row["SleepMaintenanceCostUSD"] == 0.0002
    assert row["EstimatedCostUSD"] == 0.001
    assert row["CostPerSuccessfulTaskUSD"] == 0.001
    assert row["TotalLatencySeconds"] == 2
    assert "PARETO_COORDINATES_BEGIN" in analysis["latex"]


def test_latex_frontier_with_rows_has_no_pending_marker() -> None:
    latex = cost_frontier.render_latex_frontier(
        [
            {
                "condition": "DF-hybrid",
                "PassAt1": 0.7,
                "CostPerSuccessfulTaskUSD": 0.03,
                "on_pareto_frontier": True,
            }
        ]
    )

    assert PENDING_MARKER not in latex
    assert "computed from the supplied fold artifact" in latex
