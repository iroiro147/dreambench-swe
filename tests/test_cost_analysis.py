"""Tests for the seed-1 cost analysis helpers."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cost_analysis  # noqa: E402


def _record(seq_id: str, session_index: int, *, pass_at_1: bool = False) -> dict:
    record = {
        "task": {"sequence_id": seq_id, "seq_id": seq_id, "session_index": session_index},
        "isolation_mode": "container",
        "final_passed": pass_at_1,
        "pass_at_1": pass_at_1,
        "sleep_error": None,
        "started_from_previous_session": session_index == 1,
        "previous_session_end_commit": None,
        "forbidden_fresh_base_ref_used": False,
        "agent_result": {"tokens": 10, "passed": pass_at_1, "error_type": None, "patch": "", "steps": 1},
        "agent_metadata": {"stderr": "", "stdout": "", "steps": []},
        "memory_context": [],
        "memory_writes": [],
        "score": {"production_diff": ""},
    }
    if session_index > 1:
        record["started_from_previous_session"] = True
        record["previous_session_end_commit"] = f"{seq_id}-s{session_index - 1}"
    return record


def test_build_cost_analysis_sums_manifest_and_gate_tokens(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260702T000000Z-SLICE-gpt-5.5-cost"
    condition_dir = run_dir / "DF"
    condition_dir.mkdir(parents=True)
    records = [_record("seq-a", 1), _record("seq-a", 2), _record("seq-a", 3, pass_at_1=True)]
    (condition_dir / "results.json").write_text(
        json.dumps(
            {
                "manifest": {"tokens": {"wake": 100, "sleep": 5, "judge": 200, "total": 305}},
                "records": records,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "gate_report.json").write_text(
        json.dumps(
            {
                "acceptance_gates": {"continuation_audit_valid": True},
                "continuation_audit": {"valid": True},
                "conditions": {
                    "DF": {
                        "judge": {"calls": 2, "usage": {"total_tokens": 200}},
                        "metrics": {"SleepCostShare": 0.0, "TotalTokens": 305.0},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = cost_analysis.build_cost_analysis(
        sequence_records=[{"seq_id": "seq-a", "sessions": [{"session_index": 3}], "events": [], "oracle_labels": []}],
        conditions=["DF"],
        results_root=tmp_path,
    )
    row = result["conditions"]["DF"]

    assert row["WakeTokens"] == 100
    assert row["SleepTokens"] == 5
    assert row["JudgeTokens"] == 200
    assert row["SleepJudgeTokens"] == 205
    assert row["TotalTokens"] == 305
    assert row["judge_call_count"] == 2
    assert row["CostPerPass1Success"] == 305
    assert row["TrueSleepJudgeTokenShare"] == 205 / 305
