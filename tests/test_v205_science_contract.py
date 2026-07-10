"""Semantic lock for the frozen DreamBench-SWE v2 Paper A result."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SEQUENCE_SHA256 = "4966bad1e535aaa0165c8aa7f2cb6fefd1d4e4afca78accfd868591a40448743"
EXPECTED_FAMILY = [
    "DF-hybrid vs B5",
    "DF vs B5",
    "DF-raw-only vs B5",
    "DF-hybrid vs DF-raw-only",
    "DF-hybrid vs DF",
    "DF vs B3",
]


def load_json(rel: str) -> dict[str, object]:
    payload = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_confirmatory_sequence_and_primary_result_are_frozen() -> None:
    sequence = ROOT / "experiments/env/sequences_confirmatory_v2.jsonl"
    assert hashlib.sha256(sequence.read_bytes()).hexdigest() == EXPECTED_SEQUENCE_SHA256

    fold = load_json("analysis/fold/v2_fold.json")
    conditions = fold["conditions"]
    assert isinstance(conditions, dict)
    assert conditions["DF-hybrid"]["pooled_s3"] == {"n": 180, "passed": 95, "rate": 95 / 180}
    assert conditions["B5"]["pooled_s3"] == {"n": 180, "passed": 89, "rate": 89 / 180}

    clustered = load_json("analysis/fold/v2_confirmatory_clustered.json")
    primary = clustered["primary"]
    assert isinstance(primary, dict)
    assert primary["comparison"] == "DF-hybrid vs B5"
    assert primary["statistic"] == 6
    assert primary["permutation_p"] == 0.5184192657470703
    assert primary["holm_adjusted_p"] == 1.0
    assert primary["holm_reject_0_05"] is False


def test_all_six_clustered_comparisons_remain_non_rejecting() -> None:
    clustered = load_json("analysis/fold/v2_confirmatory_clustered.json")
    family = clustered["family"]
    assert isinstance(family, dict)
    comparisons = family["comparisons"]
    assert isinstance(comparisons, list)
    assert [item["comparison"] for item in comparisons] == EXPECTED_FAMILY
    assert [item["holm_reject_0_05"] for item in comparisons] == [False] * 6


def test_c9_c10_headroom_failures_and_admission_funnel_are_frozen() -> None:
    clustered = load_json("analysis/fold/v2_confirmatory_clustered.json")
    validity = clustered["stratum_validity"]
    assert isinstance(validity, dict)
    rows = validity["b0_headroom_rows"]
    assert isinstance(rows, list)
    by_stratum = {row["stratum"]: row for row in rows}
    assert by_stratum["C9"]["b0_passed"] == 12
    assert by_stratum["C9"]["b0_n"] == 12
    assert by_stratum["C9"]["b0_headroom_holds"] is False
    assert by_stratum["C10"]["b0_passed"] == 6
    assert by_stratum["C10"]["b0_n"] == 6
    assert by_stratum["C10"]["b0_headroom_holds"] is False
    assert validity["overall_pass"] is False

    funnel = load_json("analysis/fold/admission_funnel.json")
    totals = funnel["totals"]
    assert isinstance(totals, dict)
    assert {key: totals[key] for key in ("authored", "dry_valid", "live_valid", "admitted")} == {
        "authored": 30,
        "dry_valid": 30,
        "live_valid": 30,
        "admitted": 30,
    }
