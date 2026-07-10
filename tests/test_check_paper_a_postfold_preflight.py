"""Tests for the Paper A post-fold preflight wrapper."""
from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import check_paper_a_postfold_preflight as preflight


def write(path: Path, text: str = "fixture\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_repo(tmp_path: Path, *, sequence_text: str = '{"seq_id":"v2-a"}\n') -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    write(repo / "AGENTS.md", "# fixture\n")
    (repo / "handoff").mkdir(parents=True)
    write(repo / preflight.DEFAULT_RUN_STAMP_FILE, preflight.EXPECTED_RUN_STAMP + "\n")
    sequence = write(repo / preflight.DEFAULT_SEQUENCE_RECORDS, sequence_text)
    write(repo / "scripts" / "run_grid.py", "# fixture\n")
    write(repo / "scripts" / "check_v2_confirmatory_completion.py", "# fixture\n")
    return repo, sequence, preflight.sha256_file(sequence)


def audit(repo: Path, sequence: Path, expected_sha: str, **kwargs: object) -> preflight.PreflightReport:
    return preflight.audit_preflight(
        repo_root=repo,
        run_stamp_file=repo / preflight.DEFAULT_RUN_STAMP_FILE,
        expected_run_stamp=preflight.EXPECTED_RUN_STAMP,
        sequence_records=sequence,
        expected_sequence_sha256=expected_sha,
        stale_outputs=tuple(repo / path for path in preflight.default_stale_outputs()),
        required_tools=tuple(repo / path for path in preflight.REQUIRED_LOCAL_TOOLS),
        **kwargs,
    )


def test_preflight_passes_with_clean_local_inputs_and_prints_command_deck(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    repo, sequence, expected_sha = make_repo(tmp_path)

    rc = preflight.main(
        [
            "--repo-root",
            str(repo),
            "--expected-sequence-sha256",
            expected_sha,
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "STATUS PASS failures=0" in captured.out
    assert "rsync -av --delete" in captured.out
    assert '--sequence-records "$SEQ"' in captured.out
    assert '--expected-sequence-sha256 "$EXPECTED_CONFIRMATORY_SHA256"' in captured.out
    assert "ssh root@157.90.114.41" in captured.out
    assert "cd $REMOTE_ROOT" in captured.out
    assert r"\$(git rev-parse HEAD)" in captured.out
    assert r"\$REMOTE_ROOT" not in captured.out
    assert "scripts/check_v2_confirmatory_completion.py" in captured.out


def test_preflight_rejects_sequence_hash_mismatch(tmp_path: Path) -> None:
    repo, sequence, _expected_sha = make_repo(tmp_path)

    report = audit(repo, sequence, "0" * 64)

    assert report.passed is False
    assert any("sequence records sha256 mismatch" in line for line in report.lines())


def test_preflight_rejects_stale_postfold_outputs(tmp_path: Path) -> None:
    repo, sequence, expected_sha = make_repo(tmp_path)
    write(repo / "analysis" / "fold" / "confirmatory.json", "{}\n")

    report = audit(repo, sequence, expected_sha)

    assert report.passed is False
    assert any("stale post-fold output exists" in line for line in report.lines())
    assert any("analysis/fold/confirmatory.json" in line for line in report.lines())
    assert any(
        "analysis/fold/archive-pre-v2-confirmatory-20260706T074759Z" in line
        for line in report.lines()
    )


def test_default_preflight_does_not_execute_remote_runner(tmp_path: Path) -> None:
    repo, sequence, expected_sha = make_repo(tmp_path)

    def fail_runner(_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError("remote runner should not be called by default")

    report = audit(repo, sequence, expected_sha, remote_runner=fail_runner)

    assert report.passed is True


def test_opt_in_remote_proof_uses_one_ssh_command(tmp_path: Path) -> None:
    repo, sequence, expected_sha = make_repo(tmp_path)
    seen: list[list[str]] = []

    def runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="remote ok\n", stderr="")

    report = audit(
        repo,
        sequence,
        expected_sha,
        check_remote=True,
        remote="root@example.invalid",
        remote_root="/remote/root",
        expected_vps_head="a" * 40,
        expected_results_count=preflight.EXPECTED_RESULTS_COUNT,
        remote_runner=runner,
    )

    assert report.passed is True
    assert len(seen) == 1
    assert seen[0][0:2] == ["ssh", "root@example.invalid"]
    assert "cd /remote/root &&" in seen[0][2]
    assert "git rev-parse HEAD" in seen[0][2]
    assert "sha256sum experiments/env/sequences_confirmatory_v2.jsonl" in seen[0][2]
    assert "NONLIVE_GRID_DONE rc=0" in seen[0][2]
    assert "LIVE_GRID_DONE rc=0" in seen[0][2]
    assert "rsync" not in seen[0][2]
    assert "analyze_confirmatory" not in seen[0][2]


def test_opt_in_remote_failure_fails_preflight(tmp_path: Path) -> None:
    repo, sequence, expected_sha = make_repo(tmp_path)

    def runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not done\n")

    report = audit(repo, sequence, expected_sha, check_remote=True, remote_runner=runner)

    assert report.passed is False
    assert any("opt-in remote proof failed rc=1" in line for line in report.lines())
