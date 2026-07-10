"""Tests for the v2 confirmatory completion auditor."""
from __future__ import annotations

import os
from pathlib import Path

from scripts import check_v2_confirmatory_completion as completion


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_complete_fixture(tmp_path: Path, *, result_count: int = 2) -> tuple[Path, Path]:
    results_root = tmp_path / "results"
    log_root = tmp_path / "logs"
    for index in range(result_count):
        write(results_root / f"run-{index:03d}" / "B0" / "results.json", "{}\n")
    write(log_root / "NONLIVE.driver.log", "[NONLIVE_GRID_DONE rc=0 ts=20260706T000000Z]\n")
    write(log_root / "LIVE.driver.log", "[LIVE_GRID_DONE rc=0 ts=20260706T000000Z]\n")
    write(log_root / "RUN_GRID.log", "2026 FINISH B0-s1-g01 rc=0 elapsed=1.0s\n")
    return results_root, log_root


def audit(
    results_root: Path,
    log_root: Path,
    *,
    expected_results_count: int = 2,
    sequence_records: Path | None = None,
    expected_sequence_sha256: str | None = None,
    dry_plan_paths: tuple[Path, ...] = (),
    analyzer_output_paths: tuple[Path, ...] = (),
) -> completion.AuditReport:
    return completion.audit_completion(
        results_root=results_root,
        log_root=log_root,
        expected_results_count=expected_results_count,
        sequence_records=sequence_records,
        expected_sequence_sha256=expected_sequence_sha256,
        dry_plan_paths=dry_plan_paths,
        analyzer_output_paths=analyzer_output_paths,
    )


def test_passes_complete_local_post_rsync_fixture(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    dry_plan = write(tmp_path / "analysis" / "completion.txt", "PLAN total=2 pending=0\nSKIP 001/002\n")
    output = write(tmp_path / "analysis" / "v2_fold.json", "{}\n")

    report = audit(
        results_root,
        log_root,
        dry_plan_paths=(dry_plan,),
        analyzer_output_paths=(output,),
    )

    assert report.passed is True
    assert report.lines()[-1] == "STATUS PASS failures=0"


def test_counts_only_results_root_star_star_results_json(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path, result_count=1)
    write(results_root / "too" / "deep" / "B0" / "results.json", "{}\n")
    write(results_root / "too-shallow" / "results.json", "{}\n")

    report = audit(results_root, log_root, expected_results_count=1)

    assert report.passed is True
    assert any("condition-level results count is 1" in line for line in report.lines())


def test_sequence_sha256_gate_accepts_matching_source(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    sequence_records = write(tmp_path / "sequences_confirmatory_v2.jsonl", '{"seq_id":"v2-a"}\n')
    expected_sha256 = completion.sha256_file(sequence_records)

    report = audit(
        results_root,
        log_root,
        sequence_records=sequence_records,
        expected_sequence_sha256=expected_sha256,
    )

    assert report.passed is True
    assert any("sequence records sha256 matches expected" in line for line in report.lines())


def test_sequence_sha256_gate_rejects_mismatched_source(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    sequence_records = write(tmp_path / "sequences_confirmatory_v2.jsonl", '{"seq_id":"v2-a"}\n')

    report = audit(
        results_root,
        log_root,
        sequence_records=sequence_records,
        expected_sequence_sha256="0" * 64,
    )

    assert report.passed is False
    assert any("sequence records sha256 mismatch" in line for line in report.lines())


def test_sequence_sha256_gate_rejects_missing_source(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)

    report = audit(
        results_root,
        log_root,
        sequence_records=tmp_path / "missing.jsonl",
        expected_sequence_sha256="0" * 64,
    )

    assert report.passed is False
    assert any("missing sequence records" in line for line in report.lines())


def test_sequence_sha256_gate_rejects_malformed_expected_digest(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    sequence_records = write(tmp_path / "sequences_confirmatory_v2.jsonl", '{"seq_id":"v2-a"}\n')

    report = audit(
        results_root,
        log_root,
        sequence_records=sequence_records,
        expected_sequence_sha256="not-a-sha256",
    )

    assert report.passed is False
    assert any("expected sequence SHA256 is not a 64-character hex digest" in line for line in report.lines())


def test_sequence_sha256_gate_requires_path_and_expected_digest(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    sequence_records = write(tmp_path / "sequences_confirmatory_v2.jsonl", '{"seq_id":"v2-a"}\n')

    report = audit(results_root, log_root, sequence_records=sequence_records)

    assert report.passed is False
    assert any("requires both --sequence-records and --expected-sequence-sha256" in line for line in report.lines())


def test_fails_when_required_driver_done_marker_is_missing(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    write(log_root / "LIVE.driver.log", "[LIVE_GRID_STARTED]\n")

    report = audit(results_root, log_root)

    assert report.passed is False
    assert any("LIVE.driver.log does not contain LIVE_GRID_DONE rc=0" in line for line in report.lines())


def test_rejects_any_nonzero_grid_done_marker(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    write(log_root / "extra.driver.log", "[EXTRA_GRID_DONE rc=2 ts=20260706T000000Z]\n")

    report = audit(results_root, log_root)

    assert report.passed is False
    assert any("EXTRA_GRID_DONE rc=2" in line for line in report.lines())


def test_rejects_run_grid_finish_positive_nonzero_rc(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    write(log_root / "RUN_GRID.log", "2026 FINISH B0-s1-g01 rc=0\n2026 FINISH B1-s1-g01 rc=17\n")

    report = audit(results_root, log_root)

    assert report.passed is False
    assert any("RUN_GRID FINISH failure line" in line and "rc=17" in line for line in report.lines())


def test_dry_plan_fails_on_lines_beginning_run_space(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    dry_plan = write(tmp_path / "completion.txt", "RUN 001/002 B0-s1-g01\nRUNTIME note\n")

    report = audit(results_root, log_root, dry_plan_paths=(dry_plan,))

    assert report.passed is False
    assert any("dry-plan has 1 RUN line" in line for line in report.lines())


def test_analyzer_output_must_not_be_older_than_latest_result_or_log(tmp_path: Path) -> None:
    results_root, log_root = make_complete_fixture(tmp_path)
    output = write(tmp_path / "analysis" / "v2_fold.json", "{}\n")
    old_time = 1000
    new_time = 2000
    os.utime(output, (old_time, old_time))
    os.utime(log_root / "RUN_GRID.log", (new_time, new_time))

    report = audit(results_root, log_root, analyzer_output_paths=(output,))

    assert report.passed is False
    assert any("analyzer output is stale" in line for line in report.lines())


def test_cli_returns_nonzero_and_prints_status_on_failure(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    results_root, log_root = make_complete_fixture(tmp_path, result_count=1)

    rc = completion.main(
        [
            "--results-root",
            str(results_root),
            "--log-root",
            str(log_root),
            "--expected-results-count",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "expected 2" in captured.out
    assert "STATUS FAIL failures=1" in captured.out
