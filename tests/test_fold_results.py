"""Regression tests for scripts/fold_results.py."""
from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import fold_results  # noqa: E402


def test_fold_marks_any_condition_with_sleep_error_suspect() -> None:
    path = fold_results.ROOT / "experiments" / "results" / "unit-sleep" / "B1" / "results.json"
    candidate = fold_results.Candidate(
        condition="B1",
        path=path,
        run_dir=path.parents[1],
        run_group="unit-sleep",
        sort_key=("unit-sleep", "unit-sleep", str(path)),
        data={
            "records": [
                {
                    "task": {
                        "seq_id": "seq-a",
                        "seq_type": "reviewer-preference",
                        "session_index": 3,
                    },
                    "final_passed": False,
                    "sleep_error": "RuntimeError: sleep failed",
                }
            ]
        },
        manifest={"seed": 1729},
        gate={},
        condition_gate={},
        dry_run=False,
        judge_models=(),
    )

    summary = fold_results.fold_condition("B1", [candidate], judge_model_filter=None)

    assert summary["sleep_error_count"] == 1
    assert summary["suspect"] is True
    assert summary["status"] == "SUSPECT"
