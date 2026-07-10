"""Regression tests for multi-seed DreamBench-SWE v1.1 stats."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import stats_analysis  # noqa: E402


def _record(seq_id: str, passed: bool) -> dict:
    return {
        "task": {"sequence_id": seq_id, "seq_id": seq_id, "session_index": 3},
        "isolation_mode": "container",
        "started_from_previous_session": True,
        "previous_session_end_commit": f"{seq_id}-s2",
        "forbidden_fresh_base_ref_used": False,
        "final_passed": passed,
        "pass_at_1": passed,
        "sleep_error": None,
        "score": {"production_diff": ""},
        "memory_context": [],
        "memory_writes": [],
    }


def _write_result(results_root: Path, condition: str, seed: int, outcomes: dict[str, bool]) -> None:
    run_dir = results_root / f"20260703T0000{seed:02d}Z-SLICE-gpt-5.5-{condition.lower().replace('-', '')}{seed}"
    condition_dir = run_dir / condition
    condition_dir.mkdir(parents=True)
    payload = {
        "manifest": {"condition": condition, "seed": seed},
        "records": [_record(seq_id, passed) for seq_id, passed in sorted(outcomes.items())],
    }
    (condition_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")
    gate = {
        "acceptance_gates": {"continuation_audit_valid": True},
        "continuation_audit": {"valid": True},
    }
    (run_dir / "gate_report.json").write_text(json.dumps(gate), encoding="utf-8")


def test_build_stats_computes_multiseed_key_comparisons_and_corrections(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(stats_analysis, "BOOTSTRAP_B", 400)
    outcomes = {
        "DF": {
            1: {"seq-a": True, "seq-b": True},
            2: {"seq-a": True, "seq-b": False},
            3: {"seq-a": True, "seq-b": True},
        },
        "B5": {
            1: {"seq-a": True, "seq-b": False},
            2: {"seq-a": False, "seq-b": False},
            3: {"seq-a": True, "seq-b": False},
        },
        "B5-MEM0": {
            1: {"seq-a": True, "seq-b": True},
            2: {"seq-a": True, "seq-b": False},
            3: {"seq-a": False, "seq-b": True},
        },
        "A4": {
            1: {"seq-a": True, "seq-b": True},
            2: {"seq-a": True, "seq-b": True},
            3: {"seq-a": True, "seq-b": True},
        },
        "DF-strict": {
            1: {"seq-a": True, "seq-b": False},
            2: {"seq-a": True, "seq-b": False},
            3: {"seq-a": True, "seq-b": True},
        },
    }
    for condition, by_seed in outcomes.items():
        for seed, by_sequence in by_seed.items():
            _write_result(tmp_path, condition, seed, by_sequence)

    stats = stats_analysis.build_stats(
        results_root=tmp_path,
        output_dir=tmp_path / "out",
        conditions=("DF", "B5", "B5-MEM0", "A4", "DF-strict"),
        expected_seeds=(1, 2, 3),
    )

    assert stats["metadata"]["benchmark_version"] == "DreamBench-SWE v1.1"
    assert stats["conditions"]["DF"]["pooled"]["successes"] == 5
    assert stats["conditions"]["DF"]["pooled"]["n"] == 6
    assert stats["conditions"]["B5"]["label"].startswith("B5-Instance")
    assert stats["conditions"]["B5-MEM0"]["label"].startswith("B5-MEM0")

    df_b5 = stats["key_comparisons"]["DF_vs_B5-Instance"]
    assert df_b5["pooled"]["n_paired"] == 6
    assert df_b5["pooled"]["left_successes"] == 5
    assert df_b5["pooled"]["right_successes"] == 2
    assert df_b5["per_seed"]["1"]["mcnemar_exact_p"] is not None
    assert "holm_adjusted_p" in df_b5["pooled"]
    assert "bh_adjusted_p" in df_b5["pooled"]
    assert "matched_pairs_odds_ratio_haldane" in df_b5["pooled"]["effect_sizes"]

    corrections = stats["multiple_comparison_corrections"]
    assert set(corrections["holm"]) == {
        "DF_vs_B5-Instance",
        "DF_vs_B5-MEM0",
        "DF_vs_A4_DF-lite",
        "DF_vs_DF-strict",
    }
    assert stats["inter_seed_agreement"]["pairwise_spearman"]
