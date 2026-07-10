"""Meta-tests for the 08b continuation memory-trap slice."""
from __future__ import annotations

import json
import py_compile
import shutil
import tempfile
from pathlib import Path

import pytest

from experiments.env import load_env


ROOT = Path(__file__).resolve().parents[1]
SEQUENCES_PATH = ROOT / "experiments" / "env" / "sequences.jsonl"
SYNTH_SEQUENCES_PATH = ROOT / "experiments" / "env" / "sequences_synth.jsonl"
ORACLES_ROOT = ROOT / "experiments" / "env" / "oracles"
REFSOL_ROOT = ROOT / "experiments" / "env" / "refsol"

REQUIRED_SEQUENCE_IDS = {
    "config-stale-merge",
    "todo-convention-aggregate",
    "config-reviewer-strict-csv",
    "expr-generated-category",
    "todo-flaky-monotonic-ids",
}
HIDDEN_SESSION_KEYS = {
    "base_ref",
    "oracle_cmd",
    "oracle_cmds",
    "oracle_path",
    "oracles",
    "reference_patch",
    "reference_solution",
}


def _requires_private_validation_assets() -> None:
    if not ORACLES_ROOT.is_dir() or not REFSOL_ROOT.is_dir() or not load_env.REPOS_ROOT.is_dir():
        pytest.skip("reviewer-only oracle/refsol assets and fixture repositories are not included in the public artifact")


def _raw_sequence_records() -> list[dict]:
    return [
        json.loads(line)
        for line in SEQUENCES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sequence_record_paths() -> list[Path]:
    paths = [SEQUENCES_PATH]
    if SYNTH_SEQUENCES_PATH.exists():
        paths.append(SYNTH_SEQUENCES_PATH)
    return paths


def _all_sequence_records() -> list[dict]:
    records: list[dict] = []
    for path in _sequence_record_paths():
        records.extend(load_env.load_sequence_records(path))
    return records


def test_sequence_jsonl_has_only_public_session_payloads() -> None:
    records = _raw_sequence_records()
    sequence_records = load_env.load_sequence_records(SEQUENCES_PATH)
    expected_sequence_ids = {record["seq_id"] for record in sequence_records}
    record_sequence_ids = {record["seq_id"] for record in records}
    assert record_sequence_ids == expected_sequence_ids
    assert REQUIRED_SEQUENCE_IDS <= record_sequence_ids
    assert len(records) == len(sequence_records)

    for record in records:
        assert record["repo"] in {"configly", "exprmini", "todolite"}
        assert record["initial_commit"]
        assert len(record["sessions"]) == 3
        assert [session["session_index"] for session in record["sessions"]] == [1, 2, 3]
        assert len(record["events"]) == 2
        assert len(record["oracle_labels"]) >= 5

        for session in record["sessions"]:
            assert not (set(session) & HIDDEN_SESSION_KEYS)
            assert session["instruction"].strip()
            assert session["oracle_id"] == f"{record['seq_id']}-s{session['session_index']}"
            assert all("content" not in str(value) for value in session.get("inject_after", []))


def test_hidden_oracles_and_reference_solutions_pass_in_continuation() -> None:
    _requires_private_validation_assets()

    records = _all_sequence_records()
    record_sequence_ids = {record["seq_id"] for record in records}
    assert REQUIRED_SEQUENCE_IDS <= record_sequence_ids

    expected_oracle_files = {
        ORACLES_ROOT / record["seq_id"] / f"s{int(session['session_index'])}_test.py"
        for record in records
        for session in record["sessions"]
    }
    oracle_files = sorted(path for path in ORACLES_ROOT.glob("*/*.py") if path in expected_oracle_files)
    assert set(oracle_files) == expected_oracle_files
    assert len(oracle_files) == sum(len(record["sessions"]) for record in records)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        compile_dir = tmp_path / "compiled-oracles"
        compile_dir.mkdir()
        for oracle_file in oracle_files:
            py_compile.compile(
                str(oracle_file),
                cfile=str(compile_dir / f"{oracle_file.parent.name}-{oracle_file.stem}.pyc"),
                doraise=True,
            )

        for record in records:
            seq_id = record["seq_id"]
            worktree = load_env.materialize(record["repo"], record["initial_commit"], dest=tmp_path / seq_id)
            try:
                for session in record["sessions"]:
                    session_index = int(session["session_index"])
                    oracle_file = ORACLES_ROOT / seq_id / f"s{session_index}_test.py"
                    refsol_file = REFSOL_ROOT / seq_id / f"s{session_index}.py-diff"
                    assert oracle_file.exists(), oracle_file
                    assert refsol_file.exists(), refsol_file
                    assert str(oracle_file.resolve()) in session["oracle_cmd"]

                    patch_result = load_env.apply_patch(worktree, refsol_file.read_text(encoding="utf-8"))
                    assert patch_result["passed"], patch_result
                    oracle_result = load_env.run_oracle(worktree, session["oracle_cmd"])
                    assert oracle_result["passed"], (
                        seq_id,
                        session_index,
                        oracle_result["stdout"],
                        oracle_result["stderr"],
                    )
            finally:
                shutil.rmtree(worktree, ignore_errors=True)
