"""Stdlib smoke test for scripts/run_smoke.py.

Runs with plain python3:  python3 tests/test_run_smoke.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_SMOKE = ROOT / "scripts" / "run_smoke.py"
RESULTS_JSON = ROOT / "experiments" / "results" / "smoke" / "results.json"
SMOKE_README = ROOT / "experiments" / "results" / "smoke" / "SMOKE_README.md"
WARNING = "SYNTHETIC SMOKE TEST — DETERMINISTIC STUB AGENT, NOT EXPERIMENTAL RESULTS, DO NOT CITE."

EXPECTED_METRIC_KEYS = {
    "TaskSuccess",
    "Pass@1",
    "RepeatedErrorRate",
    "StaleMemoryActivationRate",
    "HarmfulMemoryRate",
    "UsefulMemoryPrecision",
    "ProvenanceCompleteness",
    "ScopeAccuracy",
    "ContradictionRepairAccuracy",
    "TransferScore",
    "RegressionAfterUpdate",
    "MemoryBloat",
    "TotalTokens",
    "TotalLatency",
    "CostPerSuccessfulTask",
    "SleepCostShare",
}


def test_run_smoke_emits_expected_outputs() -> None:
    completed = subprocess.run(
        [sys.executable, str(RUN_SMOKE)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert WARNING in completed.stdout

    payload = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    assert payload["_warning"] == WARNING
    assert payload["run_type"] == "offline_synthetic_smoke"
    assert payload["fixture_task_count"] == 60
    assert set(payload["conditions"]) == {"B0-no-memory-stub", "DF-dreamforge-stub"}

    for condition_payload in payload["conditions"].values():
        assert set(condition_payload["metrics"]) == EXPECTED_METRIC_KEYS
        assert condition_payload["counts"]["tasks"] == 60
        for key in EXPECTED_METRIC_KEYS:
            assert isinstance(condition_payload["metrics"][key], float)

    assert payload["conditions"]["DF-dreamforge-stub"]["counts"]["retrieval_gate_decisions"] > 0
    assert payload["conditions"]["DF-dreamforge-stub"]["counts"]["sleep_writes"] > 0

    first_line = SMOKE_README.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == WARNING


def _run_all() -> bool:
    test_run_smoke_emits_expected_outputs()
    print("test_run_smoke.py: all tests passed")
    return True


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
