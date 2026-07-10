"""Regression tests for the BARRIER-2 offline rescore gate."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import barrier2_rescore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ESCAPED_RESULTS = ROOT / "tests" / "fixtures" / "contamination" / "escaped_b0_results.json"
SEQUENCES = ROOT / "experiments" / "env" / "sequences.jsonl"
ESCAPED_SEQ = "todo-reviewer-export-dialect"


def _record(seq_id: str, session_index: int, **overrides):
    record = {
        "task": {"sequence_id": seq_id, "seq_id": seq_id, "session_index": session_index},
        "final_passed": False,
        "pass_at_1": False,
        "sleep_error": None,
        "started_from_previous_session": session_index == 1,
        "previous_session_end_commit": None,
        "forbidden_fresh_base_ref_used": False,
        "score": {"production_diff": ""},
        "memory_context": [],
        "memory_writes": [],
    }
    if session_index > 1:
        record["started_from_previous_session"] = True
        record["previous_session_end_commit"] = f"{seq_id}-s{session_index - 1}"
    record.update(overrides)
    return record


def _write_result(
    results_root: Path,
    run_name: str,
    condition: str,
    records: list[dict],
    *,
    seed: int = 1,
    gate_valid: bool = True,
    sleep_error_count: int = 0,
    hygiene_metrics_non_null: bool = True,
) -> Path:
    run_dir = results_root / run_name
    condition_dir = run_dir / condition
    condition_dir.mkdir(parents=True)
    (condition_dir / "results.json").write_text(
        json.dumps({"manifest": {"condition": condition, "seed": seed}, "records": records}),
        encoding="utf-8",
    )
    gate = {
        "acceptance_gates": {
            "continuation_audit_valid": gate_valid,
            "no_sleep_errors": sleep_error_count == 0,
            "hygiene_metrics_non_null": hygiene_metrics_non_null,
        },
        "continuation_audit": {"valid": gate_valid},
        "sleep_error_count": sleep_error_count,
    }
    (run_dir / "gate_report.json").write_text(json.dumps(gate), encoding="utf-8")
    return condition_dir


def _load_sequence(seq_id: str = ESCAPED_SEQ) -> dict:
    for line in SEQUENCES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sequence = json.loads(line)
        if sequence.get("seq_id") == seq_id:
            return sequence
    raise AssertionError(f"missing sequence {seq_id}")


def _load_escaped_record() -> dict:
    if not ESCAPED_RESULTS.exists():
        pytest.skip(f"historical contamination fixture absent: {ESCAPED_RESULTS}")
    payload = json.loads(ESCAPED_RESULTS.read_text(encoding="utf-8"))
    for record in payload["records"]:
        task = record.get("task") or {}
        if task.get("sequence_id") == ESCAPED_SEQ and int(task.get("session_index") or 0) == 3:
            return record
    raise AssertionError("missing escaped B0 S3 record")


def test_union_records_excludes_invalid_gate_report_continuations(tmp_path: Path) -> None:
    invalid_record = _record(
        "expr-generated-category",
        2,
        started_from_previous_session=False,
        previous_session_end_commit=None,
    )
    _write_result(
        tmp_path,
        "20260701T134517Z-SLICE-gpt-5.5-a3b6a64d",
        "B4",
        [invalid_record],
        gate_valid=False,
    )

    records, excluded = barrier2_rescore.union_records("B4", results_root=tmp_path)

    assert records == []
    assert excluded
    assert excluded[0]["scope"] == "result_dir"
    assert "continuation_audit" in excluded[0]["reason"]
    assert "20260701T134517Z-SLICE-gpt-5.5-a3b6a64d" in excluded[0]["path"]


def test_record_validity_rejects_bad_continuation_and_pass_at_1_mismatch() -> None:
    record = _record(
        "expr-generated-precgroup",
        2,
        started_from_previous_session=False,
        previous_session_end_commit=None,
        final_passed=True,
        pass_at_1=False,
    )

    errors = barrier2_rescore._record_validity_errors(record, headline_pass_at_1=True)

    assert "invalid_continuation:started_from_previous_session" in errors
    assert "invalid_continuation:previous_session_end_commit" in errors
    assert "final_pass_without_pass_at_1" not in errors


def test_build_rows_refuses_sleep_error_by_excluding_and_failing_coverage(tmp_path: Path) -> None:
    sequence = {"seq_id": "sleep-seq", "sessions": [{"session_index": 3}], "events": [], "oracle_labels": []}
    _write_result(
        tmp_path,
        "20260702T000000Z-SLICE-gpt-5.5-sleepbad",
        "DF",
        [_record("sleep-seq", 3, sleep_error="RuntimeError: judge unavailable")],
        gate_valid=True,
    )

    rows, excluded, coverage_errors = barrier2_rescore.build_rows(
        sequences=[sequence],
        conditions=["DF"],
        expected_s3=1,
        expected_seeds=(1,),
        results_root=tmp_path,
    )

    assert rows["DF"]["n3"] == 0
    assert any(item["reason"] == "sleep_error" for item in excluded)
    assert coverage_errors[0]["condition"] == "DF"
    assert coverage_errors[0]["reason"] == "incomplete_s3_coverage"


def test_build_rows_excludes_task_exception_as_infrastructure_failure(tmp_path: Path) -> None:
    sequence = {"seq_id": "infra-seq", "sessions": [{"session_index": 3}], "events": [], "oracle_labels": []}
    _write_result(
        tmp_path,
        "20260703T000000Z-SLICE-gpt-5.5-infra",
        "DF",
        [_record("infra-seq", 3, error_type="task_exception:PermissionError")],
        gate_valid=True,
    )

    rows, excluded, coverage_errors = barrier2_rescore.build_rows(
        sequences=[sequence],
        conditions=["DF"],
        expected_s3=1,
        expected_seeds=(1,),
        results_root=tmp_path,
    )

    assert rows["DF"]["n3"] == 0
    assert any(item["reason"] == "task_exception:PermissionError" for item in excluded)
    assert coverage_errors[0]["condition"] == "DF"
    assert coverage_errors[0]["reason"] == "incomplete_s3_coverage"


def test_hygiene_null_gate_does_not_drop_task_success_rows(tmp_path: Path) -> None:
    sequence = {"seq_id": "hygiene-null-seq", "sessions": [{"session_index": 3}], "events": [], "oracle_labels": []}
    _write_result(
        tmp_path,
        "20260702T000000Z-SLICE-gpt-5.5-hygnull",
        "B0",
        [_record("hygiene-null-seq", 3, final_passed=True, pass_at_1=True, isolation_mode="container")],
        gate_valid=True,
        hygiene_metrics_non_null=False,
    )

    rows, excluded, coverage_errors = barrier2_rescore.build_rows(
        sequences=[sequence],
        conditions=["B0"],
        expected_s3=1,
        expected_seeds=(1,),
        results_root=tmp_path,
    )

    assert rows["B0"]["n3"] == 1
    assert rows["B0"]["pass_at_1_count"] == 1
    assert rows["B0"]["final_passed_count"] == 1
    assert excluded == []
    assert coverage_errors == []


def test_union_records_excludes_contaminated_historical_record(tmp_path: Path) -> None:
    sequence = _load_sequence()
    _write_result(
        tmp_path,
        "20260701T065503Z-SLICE-gpt-5.5-565e39cd",
        "B0",
        [_load_escaped_record()],
        gate_valid=True,
    )

    records, excluded = barrier2_rescore.union_records(
        "B0",
        results_root=tmp_path,
        sequence_records=[sequence],
    )

    assert records == []
    assert len(excluded) == 1
    assert excluded[0]["sequence_id"] == ESCAPED_SEQ
    assert excluded[0]["session_index"] == 3
    assert excluded[0]["reason"].startswith("contaminated")
    assert excluded[0]["contamination_evidence"]


def test_union_records_scans_container_records_for_hidden_reads(tmp_path: Path) -> None:
    sequence = {"seq_id": "hidden-read-seq", "sessions": [{"session_index": 3}], "events": [], "oracle_labels": []}
    record = _record(
        "hidden-read-seq",
        3,
        isolation_mode="container",
        agent_metadata={
            "stderr": (
                "codex\nexec\n/usr/bin/bash -lc 'cat <REVIEWER_ONLY_ORACLES>/hidden-read-seq/s3_test.py' "
                "in /work\n succeeded in 0ms:\nSECRET\n"
            )
        },
    )
    _write_result(
        tmp_path,
        "20260702T000000Z-SLICE-gpt-5.5-container",
        "B3",
        [record],
        gate_valid=True,
    )

    records, excluded = barrier2_rescore.union_records(
        "B3",
        results_root=tmp_path,
        sequence_records=[sequence],
    )

    assert records == []
    assert len(excluded) == 1
    assert excluded[0]["reason"].startswith("contaminated")
    assert excluded[0]["contamination_evidence"]


def test_container_records_ignore_stale_broad_contaminated_flag_without_read(tmp_path: Path) -> None:
    sequence = _load_sequence()
    record = dict(_load_escaped_record())
    record["isolation_mode"] = "container"
    _write_result(
        tmp_path,
        "20260702T000000Z-SLICE-gpt-5.5-container",
        "B3",
        [record],
        gate_valid=True,
    )

    records, excluded = barrier2_rescore.union_records(
        "B3",
        results_root=tmp_path,
        sequence_records=[sequence],
    )

    assert len(records) == 1
    assert records[0]["isolation_mode"] == "container"
    assert excluded == []


def test_canary_proof_status_reports_existing_artifact(tmp_path: Path) -> None:
    proof = tmp_path / "CANARY-PROOF.txt"
    proof.write_text("container canary transcript\n", encoding="utf-8")

    status = barrier2_rescore.canary_proof_status(proof)

    assert status == {
        "path": str(proof),
        "exists": True,
        "bytes": len("container canary transcript\n"),
    }


def test_build_rows_reports_per_seed_and_pooled_pass_at_1(tmp_path: Path) -> None:
    sequences = [
        {"seq_id": "seq-a", "sessions": [{"session_index": 3}], "events": [], "oracle_labels": []},
        {"seq_id": "seq-b", "sessions": [{"session_index": 3}], "events": [], "oracle_labels": []},
    ]
    outcomes = {
        1: {"seq-a": True, "seq-b": False},
        2: {"seq-a": True, "seq-b": True},
        3: {"seq-a": False, "seq-b": False},
    }
    for seed, by_sequence in outcomes.items():
        _write_result(
            tmp_path,
            f"20260703T00000{seed}Z-SLICE-gpt-5.5-seed{seed}",
            "DF",
            [
                _record(
                    seq_id,
                    3,
                    isolation_mode="container",
                    final_passed=passed,
                    pass_at_1=passed,
                )
                for seq_id, passed in by_sequence.items()
            ],
            seed=seed,
            gate_valid=True,
        )

    rows, excluded, coverage_errors = barrier2_rescore.build_rows(
        sequences=sequences,
        conditions=["DF"],
        expected_s3=2,
        expected_seeds=(1, 2, 3),
        results_root=tmp_path,
    )

    assert excluded == []
    assert coverage_errors == []
    assert rows["DF"]["per_seed"][1]["pass_at_1_count"] == 1
    assert rows["DF"]["per_seed"][2]["pass_at_1_count"] == 2
    assert rows["DF"]["per_seed"][3]["pass_at_1_count"] == 0
    assert rows["DF"]["pooled"]["n3"] == 6
    assert rows["DF"]["pooled"]["pass_at_1_count"] == 3
    assert rows["DF"]["pass_at_1_rate"] == 0.5


def test_condition_labels_separate_b5_instance_and_mem0() -> None:
    assert "Instance" in barrier2_rescore.CONDITION_LABELS["B5"]
    assert "MEM0" in barrier2_rescore.CONDITION_LABELS["B5-MEM0"]
