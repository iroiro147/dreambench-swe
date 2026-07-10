"""Unit tests for the confirmatory fold analyzer math helpers."""
from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import analyze_confirmatory  # noqa: E402


def test_mcnemar_exact_p_known_values() -> None:
    assert analyze_confirmatory.mcnemar_exact_p(5, 0) == 0.0625
    assert analyze_confirmatory.mcnemar_exact_p(0, 5) == 0.0625
    assert analyze_confirmatory.mcnemar_exact_p(9, 0) < 0.05
    assert analyze_confirmatory.mcnemar_exact_p(0, 0) == 1.0


def test_holm_adjust_known_values() -> None:
    adjusted = analyze_confirmatory.holm_adjust(
        {
            "smallest": 0.01,
            "largest": 0.04,
            "middle": 0.03,
        }
    )

    assert abs(adjusted["smallest"]["adjusted_p"] - 0.03) < 1e-12
    assert abs(adjusted["middle"]["adjusted_p"] - 0.06) < 1e-12
    assert abs(adjusted["largest"]["adjusted_p"] - 0.06) < 1e-12
    assert adjusted["smallest"]["reject_0_05"] is True
    assert adjusted["middle"]["reject_0_05"] is False
    assert adjusted["largest"]["reject_0_05"] is False


def test_real_mem0_is_included_without_changing_preregistered_family(tmp_path) -> None:
    run_dir = tmp_path / "20260704T000000Z-SLICE-unit"
    for condition, passed in (
        ("DF-hybrid", True),
        ("B5-MEM0", False),
        ("B5-MEM0-LIT", False),
    ):
        condition_dir = run_dir / condition
        condition_dir.mkdir(parents=True)
        (condition_dir / "results.json").write_text(
            json.dumps(
                {
                    "manifest": {"seed": 1},
                    "records": [
                        {
                            "oracle_id": "alpha-seq-s3",
                            "ordinal": 3,
                            "pass_at_1": passed,
                            "error_type": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    analysis = analyze_confirmatory.build_analysis(str(tmp_path))

    assert "B5-MEM0" in analysis["conditions_present"]
    assert "B5-MEM0-LIT" in analysis["conditions_present"]
    assert analysis["conditions"]["B5-MEM0"]["pooled_s3"] == {
        "passed": 0,
        "n": 1,
        "rate": 0.0,
    }
    assert analysis["conditions"]["B5-MEM0-LIT"]["pooled_s3"] == {
        "passed": 0,
        "n": 1,
        "rate": 0.0,
    }

    family_comparisons = [
        item["comparison"] for item in analysis["mcnemar_family"]["comparisons"]
    ]
    assert "DF-hybrid vs B5-MEM0" not in family_comparisons

    supplemental = analysis["supplemental_mcnemar"]["comparisons"]
    assert [item["comparison"] for item in supplemental] == [
        "DF-hybrid vs B5-MEM0",
        "DF-hybrid vs B5-MEM0-LIT",
    ]
    for item in supplemental:
        assert item["paired_n"] == 1
        assert item["b_first_wins"] == 1
        assert item["c_second_wins"] == 0
        assert item["exact_p"] == 1.0


def test_grouped_runs_count_non_s3_warmup_records(tmp_path) -> None:
    run_dir = tmp_path / "20260704T010203Z-SLICE-grouped"
    condition_dir = run_dir / "B5"
    condition_dir.mkdir(parents=True)
    (condition_dir / "results.json").write_text(
        json.dumps(
            {
                "manifest": {"seed": 1},
                "records": [
                    {"oracle_id": "alpha-s1", "ordinal": 1, "pass_at_1": True, "error_type": ""},
                    {"oracle_id": "alpha-s2", "ordinal": 2, "pass_at_1": True, "error_type": ""},
                    {"oracle_id": "alpha-s3", "ordinal": 3, "pass_at_1": False, "error_type": ""},
                    {"oracle_id": "beta-s1", "ordinal": 4, "pass_at_1": True, "error_type": ""},
                    {"oracle_id": "beta-s2", "ordinal": 5, "pass_at_1": False, "error_type": ""},
                    {"oracle_id": "beta-s3", "ordinal": 6, "pass_at_1": True, "error_type": ""},
                ],
            }
        ),
        encoding="utf-8",
    )

    analysis = analyze_confirmatory.build_analysis(str(tmp_path))
    summary = analysis["conditions"]["B5"]

    assert summary["pooled_s3"] == {"passed": 1, "n": 2, "rate": 0.5}
    assert summary["warmup"] == {"passed": 3, "n": 4, "rate": 0.75}
    assert analysis["metadata"]["warmup_definition"] == (
        "non-S3 records (oracle_id not ending '-s3' and ordinal != 3)"
    )


def test_falsifier_checks_use_frozen_v2_thresholds() -> None:
    summaries = {
        "B0": {
            "pooled_s3": {"passed": 21, "n": 180, "rate": 21 / 180},
        },
        "B5": {
            "hygiene": {
                "metrics_mean": {
                    "UsefulMemoryPrecision": 0.556,
                    "RepeatedErrorRate": 0.225,
                }
            },
        },
        "DF-hybrid": {
            "pooled_s3": {"passed": 95, "n": 180, "rate": 95 / 180},
            "per_seed_s3": {
                "1": {"passed": 31, "n": 60, "rate": 31 / 60},
                "2": {"passed": 32, "n": 60, "rate": 32 / 60},
                "3": {"passed": 32, "n": 60, "rate": 32 / 60},
            },
            "hygiene": {
                "metrics_mean": {
                    "UsefulMemoryPrecision": 0.264,
                    "RepeatedErrorRate": 0.142,
                    "RegressionAfterUpdate": 0.106,
                }
            },
        },
        "DF-strict-hybrid": {
            "pooled_s3": {"passed": 78, "n": 180, "rate": 78 / 180},
        },
    }

    checks = analyze_confirmatory.build_falsifier_checks(summaries)
    labels = "\n".join(item["check"] for item in checks)

    assert "DF-strict-hybrid pooled S3 >= 0.60" in labels
    assert "UsefulMemoryPrecision >= 0.45 and no more than 0.15 below B5" in labels
    assert "seed spread (max-min per-seed S3) <= 0.12 preferred / <= 0.16 maximum" in labels
    assert "RegressionAfterUpdate <= 0.08" in labels
    assert "B5 per-seed S3 identical" not in labels
    assert "0.55" not in labels
    assert "0.10" not in labels
    assert "0.136" not in labels
    assert "0.045" not in labels
