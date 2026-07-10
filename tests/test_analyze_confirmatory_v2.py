"""Tests for the v2 clustered confirmatory analyzer."""
from __future__ import annotations

import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import analyze_confirmatory_v2  # noqa: E402


FAMILY = (("SIGNAL", "BASE"), ("NULL", "BASE"))
PRIMARY = ("SIGNAL", "BASE")


def make_60x3_fixture() -> dict:
    cells = {"BASE": {}, "SIGNAL": {}, "NULL": {}}
    for trap_index in range(60):
        trap = "trap-%02d" % (trap_index + 1)
        signal_passes = trap_index < 30
        for seed in (1, 2, 3):
            key = "seed%d:%s" % (seed, trap)
            cells["BASE"][key] = {"passed": False}
            cells["NULL"][key] = {"passed": False}
            cells["SIGNAL"][key] = {"passed": signal_passes}
    return {
        "conditions": {
            condition: {"s3_cells": condition_cells}
            for condition, condition_cells in cells.items()
        }
    }


def make_stratum_validity_fixture() -> dict:
    cells = {condition: {} for condition in analyze_confirmatory_v2.STRATUM_BASELINE_CONDITIONS}
    for condition in cells:
        for index in range(10):
            cells[condition]["seed1:v2-c2-discriminating-%02d" % index] = {
                "passed": condition == "B3" and index < 2,
                "construct_label": "C2",
            }
            cells[condition]["seed1:v2-c3-floor-tied-%02d" % index] = {
                "passed": False,
                "construct_label": "C3",
            }

    for condition in cells:
        for index in range(4):
            cells[condition]["seed1:v2-c10-abstention-%02d" % index] = {
                "passed": False,
                "construct_label": "C10",
            }

    return {
        "conditions": {
            condition: {"s3_cells": condition_cells}
            for condition, condition_cells in cells.items()
        }
    }


def run_fixture() -> dict:
    return analyze_confirmatory_v2.analyze_v2(
        make_60x3_fixture(),
        primary_pair=PRIMARY,
        family=FAMILY,
        permutation_seed=12345,
    )


def comparison(report: dict, label: str) -> dict:
    return {
        item["comparison"]: item
        for item in report["family"]["comparisons"]
    }[label]


def test_clustered_primary_detects_known_signal_and_known_null() -> None:
    report = run_fixture()

    assert report["metadata"]["v2_live_results"] == "complete"
    assert report["metadata"]["results_status"] == "complete"
    assert report["metadata"]["permutation_seed"] == 12345
    assert report["primary"]["comparison"] == "SIGNAL vs BASE"
    assert report["stratum_validity"]["table_name"] == "STRATUM-VALIDITY"

    signal = comparison(report, "SIGNAL vs BASE")
    null = comparison(report, "NULL vs BASE")

    assert signal["grid"]["complete_60x3"] is True
    assert signal["primary_test"]["clusters"] == 60
    assert signal["primary_test"]["paired_seed_cells"] == 180
    assert signal["primary_test"]["signed_statistic"] == 90
    assert signal["primary_test"]["statistic"] == 90
    assert signal["primary_test"]["permutation_p"] == 2**-29
    assert signal["primary_test"]["holm_adjusted_p"] == 2**-28
    assert signal["primary_test"]["holm_reject_0_05"] is True
    assert signal["primary_test"]["cluster_score_counts"] == {0: 30, 3: 30}

    assert null["primary_test"]["statistic"] == 0
    assert null["primary_test"]["permutation_p"] == 1.0
    assert null["primary_test"]["holm_adjusted_p"] == 1.0
    assert null["primary_test"]["holm_reject_0_05"] is False
    assert null["primary_test"]["cluster_score_counts"] == {0: 60}


def test_secondary_and_sensitivity_counts_match_fixture() -> None:
    report = run_fixture()
    signal = comparison(report, "SIGNAL vs BASE")
    null = comparison(report, "NULL vs BASE")

    pooled = signal["secondary"]["pooled_mcnemar"]
    assert pooled["paired_n"] == 180
    assert pooled["b_first_wins"] == 90
    assert pooled["c_second_wins"] == 0
    assert pooled["exact_p"] == 2**-89
    assert pooled["holm_adjusted_p"] == 2**-88

    for seed_item in signal["sensitivity"]["per_seed"]:
        assert seed_item["paired_n"] == 60
        assert seed_item["b_first_wins"] == 30
        assert seed_item["c_second_wins"] == 0
        assert seed_item["exact_p"] == 2**-29
        assert seed_item["holm_adjusted_p"] == 2**-28
        assert seed_item["holm_reject_0_05"] is True

    majority = signal["sensitivity"]["majority_collapse"]
    assert majority["trap_n"] == 60
    assert majority["b_first_wins"] == 30
    assert majority["c_second_wins"] == 0
    assert majority["exact_p"] == 2**-29
    assert majority["holm_adjusted_p"] == 2**-28
    assert majority["holm_reject_0_05"] is True

    assert null["secondary"]["pooled_mcnemar"]["exact_p"] == 1.0
    assert null["sensitivity"]["majority_collapse"]["exact_p"] == 1.0
    assert all(item["exact_p"] == 1.0 for item in null["sensitivity"]["per_seed"])


def test_rejects_incomplete_frozen_grid() -> None:
    fixture = make_60x3_fixture()
    del fixture["conditions"]["SIGNAL"]["s3_cells"]["seed3:trap-60"]

    with pytest.raises(ValueError, match="complete 60 trap x 3 seed paired grid"):
        analyze_confirmatory_v2.analyze_v2(
            fixture,
            primary_pair=PRIMARY,
            family=FAMILY,
            permutation_seed=12345,
        )


def test_primary_pair_must_be_in_prespecified_family() -> None:
    with pytest.raises(ValueError, match="primary_pair must be present in family"):
        analyze_confirmatory_v2.analyze_v2(
            make_60x3_fixture(),
            primary_pair=("SIGNAL", "BASE"),
            family=(("NULL", "BASE"),),
            permutation_seed=12345,
        )


def test_stratum_validity_flags_discriminating_and_floor_tied_strata() -> None:
    report = analyze_confirmatory_v2.analyze_v2(
        make_stratum_validity_fixture(),
        primary_pair=("B3", "B0"),
        family=(("B3", "B0"),),
        permutation_seed=12345,
        expected_traps=24,
        expected_seeds=(1,),
        require_complete_grid=False,
    )

    table = report["stratum_validity"]
    rows = {row["stratum"]: row for row in table["rows"]}
    headroom_rows = {row["stratum"]: row for row in table["b0_headroom_rows"]}

    assert table["table_name"] == "STRATUM-VALIDITY"
    assert rows["C2"]["status"] == "PASS"
    assert rows["C2"]["max_condition"] == "B3"
    assert rows["C2"]["max_condition_pass_rate"] == 0.2
    assert rows["C2"]["min_condition_pass_rate"] == 0.0
    assert rows["C2"]["spread"] == 0.2
    assert rows["C2"]["b0_headroom_holds"] is True
    assert "floor-tie" not in rows["C2"]["flags"]

    assert rows["C3"]["status"] == "FAIL"
    assert rows["C3"]["max_condition_pass_rate"] == 0.0
    assert rows["C3"]["spread"] == 0.0
    assert "floor-tie" in rows["C3"]["flags"]
    assert "no-spread" in rows["C3"]["flags"]

    assert headroom_rows["C10"]["b0_pass_rate"] == 0.0
    assert headroom_rows["C10"]["b0_headroom_holds"] is True
    assert table["c10_b0_headroom_holds"] is True
