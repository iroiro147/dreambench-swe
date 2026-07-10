"""Tests for the v2 artifact freshness guard."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from scripts import check_v2_artifact_freshness as guard


RUN_STAMP = "20260706T074759Z"


def ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def write(path: Path, text: str = "fixture\n", *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def make_fixture(
    tmp_path: Path,
    *,
    source_mtime: float | None = None,
    analyzer_mtime: float | None = None,
    package_input_mtime: float | None = None,
    dist_mtime: float | None = None,
    manifest_generated_at: str = "2026-07-06T15:00:00Z",
) -> Path:
    repo = tmp_path / "repo"
    source_mtime = source_mtime if source_mtime is not None else ts("2026-07-06T12:00:00+00:00")
    analyzer_mtime = analyzer_mtime if analyzer_mtime is not None else ts("2026-07-06T13:00:00+00:00")
    package_input_mtime = package_input_mtime if package_input_mtime is not None else ts("2026-07-06T13:30:00+00:00")
    dist_mtime = dist_mtime if dist_mtime is not None else ts("2026-07-06T15:00:00+00:00")

    write(repo / guard.DEFAULT_RUN_STAMP_FILE, RUN_STAMP, mtime=source_mtime)
    write(
        repo / "experiments" / "results" / f"v2-confirmatory-{RUN_STAMP}" / "B0-s1-g01" / "B0" / "results.json",
        "{}\n",
        mtime=source_mtime,
    )
    write(
        repo / "logs" / "grid" / f"v2-confirmatory-{RUN_STAMP}" / "RUN_GRID.log",
        "2026 FINISH B0-s1-g01 rc=0\n",
        mtime=source_mtime,
    )

    for rel in guard.DEFAULT_ANALYZER_OUTPUTS:
        payload = "{}\n" if rel.endswith(".json") else "artifact\n"
        write(repo / rel, payload, mtime=analyzer_mtime)
    for rel in guard.DEFAULT_PACKAGE_PROOFS + guard.DEFAULT_PACKAGE_SCRIPTS + guard.DEFAULT_PACKAGE_SUPPORT_FILES:
        if rel in guard.DEFAULT_ANALYZER_OUTPUTS:
            continue
        write(repo / rel, f"package input {rel}\n", mtime=package_input_mtime)

    manifest = {
        "package": guard.validator.PACKAGE_NAME,
        "mode": "public",
        "archive": guard.validator.PUBLIC_ARCHIVE,
        "generated_at_utc": manifest_generated_at,
        "files": [],
    }
    write(repo / "dist" / "MANIFEST.json", json.dumps(manifest) + "\n", mtime=dist_mtime)
    write(repo / "dist" / "CHECKSUMS.sha256", "0" * 64 + "  MANIFEST.json\n", mtime=dist_mtime)
    write(repo / "dist" / guard.validator.PUBLIC_ARCHIVE, "archive\n", mtime=dist_mtime)
    return repo


def audit(repo: Path) -> guard.FreshnessReport:
    run_stamp_file = repo / guard.DEFAULT_RUN_STAMP_FILE
    return guard.audit_freshness(
        repo_root=repo,
        run_stamp_file=run_stamp_file,
        run_stamp=None,
        results_root=None,
        log_root=None,
        dist_dir=repo / "dist",
        analyzer_outputs=tuple(repo / rel for rel in guard.DEFAULT_ANALYZER_OUTPUTS),
        package_inputs=tuple(
            repo / rel
            for rel in (
                guard.DEFAULT_ANALYZER_OUTPUTS
                + guard.DEFAULT_PACKAGE_PROOFS
                + guard.DEFAULT_PACKAGE_SCRIPTS
                + guard.DEFAULT_PACKAGE_SUPPORT_FILES
            )
        ),
        dist_files=guard.DEFAULT_DIST_FILES,
        generated_at_skew_seconds=1.0,
    )


def test_artifact_freshness_passes_when_outputs_and_dist_are_newer(tmp_path: Path) -> None:
    repo = make_fixture(tmp_path)

    report = audit(repo)

    assert report.passed is True
    assert report.lines()[-1] == "STATUS PASS failures=0"


def test_fails_when_analyzer_output_is_older_than_result_or_log_source(tmp_path: Path) -> None:
    repo = make_fixture(
        tmp_path,
        source_mtime=ts("2026-07-06T14:00:00+00:00"),
        analyzer_mtime=ts("2026-07-06T13:00:00+00:00"),
        package_input_mtime=ts("2026-07-06T13:30:00+00:00"),
        dist_mtime=ts("2026-07-06T15:00:00+00:00"),
    )

    report = audit(repo)

    assert report.passed is False
    assert any("analyzer output is stale" in line for line in report.lines())


def test_fails_when_dist_file_is_older_than_package_input(tmp_path: Path) -> None:
    repo = make_fixture(
        tmp_path,
        analyzer_mtime=ts("2026-07-06T13:00:00+00:00"),
        package_input_mtime=ts("2026-07-06T16:00:00+00:00"),
        dist_mtime=ts("2026-07-06T15:00:00+00:00"),
        manifest_generated_at="2026-07-06T17:00:00Z",
    )

    report = audit(repo)

    assert report.passed is False
    assert any("dist file is stale" in line for line in report.lines())


def test_fails_when_manifest_generated_at_predates_package_input(tmp_path: Path) -> None:
    repo = make_fixture(
        tmp_path,
        analyzer_mtime=ts("2026-07-06T13:00:00+00:00"),
        package_input_mtime=ts("2026-07-06T16:00:00+00:00"),
        dist_mtime=ts("2026-07-06T17:00:00+00:00"),
        manifest_generated_at="2026-07-06T15:00:00Z",
    )

    report = audit(repo)

    assert report.passed is False
    assert any("MANIFEST generated_at_utc is stale" in line for line in report.lines())


def test_cli_returns_nonzero_for_missing_final_outputs(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    write(repo / guard.DEFAULT_RUN_STAMP_FILE, RUN_STAMP)

    rc = guard.main(["--repo-root", str(repo)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "analyzer output missing" in captured.out
    assert "STATUS FAIL" in captured.out
