"""Tests for the DreamBench-SWE trap-validity gate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments import validate_trap
from experiments.env import load_env


ROOT = Path(__file__).resolve().parents[1]
SEQUENCES_PATH = ROOT / "experiments" / "env" / "sequences.jsonl"
ORACLES_ROOT = ROOT / "experiments" / "env" / "oracles"
REFSOL_ROOT = ROOT / "experiments" / "env" / "refsol"
REQUIRED_SEQUENCE_IDS = {
    "config-stale-merge",
    "todo-convention-aggregate",
    "config-reviewer-strict-csv",
    "expr-generated-category",
    "todo-flaky-monotonic-ids",
}


def _requires_private_validation_assets() -> None:
    if not ORACLES_ROOT.is_dir() or not REFSOL_ROOT.is_dir() or not load_env.REPOS_ROOT.is_dir():
        pytest.skip("reviewer-only oracle/refsol assets and fixture repositories are not included in the public artifact")


def test_validate_trap_selftest_dry_runs_existing_sequences(monkeypatch, tmp_path, capsys) -> None:
    _requires_private_validation_assets()

    sequence_records = load_env.load_sequence_records(SEQUENCES_PATH)
    expected_sequence_ids = {record["seq_id"] for record in sequence_records}
    assert REQUIRED_SEQUENCE_IDS <= expected_sequence_ids

    monkeypatch.setattr(validate_trap, "VALIDATION_ROOT", tmp_path)

    code = validate_trap.main(["--selftest"])

    assert code == 0
    stdout = capsys.readouterr().out
    assert "seq_id" in stdout
    assert "b0_s3_failed" in stdout
    assert "refsol_passes" in stdout
    assert "oracle_hidden" in stdout

    reports = sorted(tmp_path.glob("*-validation.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["sequence_records_path"] == "[hidden-sequences-path]"
    assert report["mode"] == "dry"
    assert report["sequence_count"] == len(sequence_records)
    assert report["valid_count"] == len(sequence_records)
    assert report["invalid_count"] == 0

    verdicts = report["verdicts"]
    verdict_sequence_ids = {verdict["seq_id"] for verdict in verdicts}
    assert verdict_sequence_ids == expected_sequence_ids
    assert REQUIRED_SEQUENCE_IDS <= verdict_sequence_ids
    for verdict in verdicts:
        assert set(verdict) == validate_trap.REQUIRED_VERDICT_KEYS
        assert verdict["valid"] is True
        assert verdict["b0_s3_failed"] is True
        assert verdict["refsol_passes"] is True
        assert verdict["oracle_hidden"] is True
        assert verdict["reasons"] == []


def test_validate_trap_report_redacts_hidden_contracts_and_paths() -> None:
    report = validate_trap._redact_hidden_report({
        "reason": (
            f"{ROOT}/<REVIEWER_ONLY_ORACLES>/todo-reviewer-export-dialect/s3_test.py "
            f"{ROOT}/<REVIEWER_ONLY_REFSOL>/todo-reviewer-export-dialect/s3.py-diff "
            "Exact reviewer export dialect contract: export must write EXPORT-vQ7M2-L9Z"
        )
    })

    text = json.dumps(report, sort_keys=True)
    assert "experiments/env/oracles" not in text
    assert "experiments/env/refsol" not in text
    assert "EXPORT-vQ7M2-L9Z" not in text
    assert "[hidden-oracle-path]" in text
    assert "[hidden-refsol-path]" in text
    assert "[hidden-contract" in text
