"""Regression tests for post-run contamination scanning."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from experiments.contamination_scan import hidden_markers_for_sequence, scan_record, scan_results  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
# Self-contained fixtures (copied from original run dirs, which get archived between runs).
ESCAPED_RESULTS = ROOT / "tests" / "fixtures" / "contamination" / "escaped_b0_results.json"
CONFIG_A2_RESULTS = ROOT / "tests" / "fixtures" / "contamination" / "a2_config_results.json"
SEQUENCES = ROOT / "experiments" / "env" / "sequences.jsonl"
ESCAPED_SEQ = "todo-reviewer-export-dialect"
CONFIG_SEQ = "config-freeze-provenance"


def _load_sequence(seq_id: str = ESCAPED_SEQ) -> dict:
    for line in SEQUENCES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sequence = json.loads(line)
        if sequence.get("seq_id") == seq_id:
            return sequence
    raise AssertionError(f"missing sequence {seq_id}")


def _load_record(results_path: Path, seq_id: str, session_index: int) -> dict:
    if not results_path.exists():
        pytest.skip(f"historical contamination fixture absent: {results_path}")
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    for record in payload["records"]:
        task = record.get("task") or {}
        if task.get("sequence_id") == seq_id and int(task.get("session_index") or 0) == session_index:
            return record
    raise AssertionError(f"missing {seq_id} S{session_index} record in {results_path}")


def _load_escaped_record() -> dict:
    return _load_record(ESCAPED_RESULTS, ESCAPED_SEQ, 3)


def _load_config_a2_record() -> dict:
    return _load_record(CONFIG_A2_RESULTS, CONFIG_SEQ, 3)


def _clean_control_from(record: dict) -> dict:
    clean = copy.deepcopy(record)
    clean["agent_metadata"]["stdout"] = ""
    clean["agent_metadata"]["stderr"] = ""
    clean["agent_metadata"]["steps"] = []
    clean["trajectory"]["steps"] = []
    clean["trajectory"]["raw_episode"]["observations"] = []
    clean["trajectory"]["raw_episode"]["failure_observations"] = []
    assert "EXPORT-vQ7M2-L9Z" in clean["agent_result"]["patch"]
    assert "EXPORT-vQ7M2-L9Z" in clean["score"]["production_diff"]
    return clean


def test_hidden_markers_reuse_slice_scorer_contract_extraction() -> None:
    sequence = _load_sequence()

    markers = hidden_markers_for_sequence(sequence)

    assert "EXPORT-vQ7M2-L9Z" in markers
    assert "~id~;~status~;~title~" in markers
    assert "CSV" not in markers


def test_known_b0_escape_record_is_flagged_with_path_or_marker_evidence() -> None:
    sequence = _load_sequence()
    record = _load_escaped_record()

    result = scan_record(record, sequence)

    assert result["contaminated"] is True
    assert any(item["type"] == "hidden_path" for item in result["evidence"])
    marker_evidence = [item for item in result["evidence"] if item["type"] == "hidden_marker"]
    assert marker_evidence
    assert all(item["memory_sourced"] is False for item in marker_evidence)


def test_memory_sourced_marker_is_evidence_but_not_contamination() -> None:
    sequence = _load_sequence(CONFIG_SEQ)
    record = _load_config_a2_record()

    result = scan_record(record, sequence)

    assert result["contaminated"] is False
    marker_evidence = [item for item in result["evidence"] if item["type"] == "hidden_marker"]
    assert marker_evidence == [
        {
            "type": "hidden_marker",
            "field": "agent_metadata.stderr",
            "match": "FRZ-7Q2",
            "memory_sourced": True,
            "excerpt": marker_evidence[0]["excerpt"],
        }
    ]
    assert "FRZ-7Q2" in marker_evidence[0]["excerpt"]


def test_non_memory_sourced_marker_without_path_is_contamination() -> None:
    record = {
        "agent_metadata": {
            "stderr": "codex\npython emitted the required hidden token FRZ-7Q2.\n",
        },
        "memory_context": [],
    }

    result = scan_record(record, {}, hidden_markers=["FRZ-7Q2"])

    assert result["contaminated"] is True
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["type"] == "hidden_marker"
    assert result["evidence"][0]["field"] == "agent_metadata.stderr"
    assert result["evidence"][0]["match"] == "FRZ-7Q2"
    assert result["evidence"][0]["memory_sourced"] is False
    assert "FRZ-7Q2" in result["evidence"][0]["excerpt"]


def test_token_only_in_final_patch_is_clean_control() -> None:
    sequence = _load_sequence()
    record = _clean_control_from(_load_escaped_record())

    result = scan_record(record, sequence)

    assert result == {"contaminated": False, "evidence": []}


def test_scan_results_reports_contaminated_key() -> None:
    sequence = _load_sequence()
    record = _load_escaped_record()

    result = scan_results([record], [sequence])

    assert result["contaminated_keys"] == [(ESCAPED_SEQ, 3)]
    assert result["per_record"][f"{ESCAPED_SEQ}:S3"]["contaminated"] is True
