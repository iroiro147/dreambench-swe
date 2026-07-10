"""Regression coverage for manuscript-cited public evidence enforcement."""
from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from scripts import check_paper_public_evidence as checker


PACKAGE = "dreambench-swe-artifact"
EVIDENCE = (
    "analysis/fold/proof.txt",
    "analysis/investigation-evidence/audit.md",
)


def test_cli_runs_without_pythonpath() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(Path(checker.__file__).resolve()), "--help"],
        cwd=Path(checker.__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_cited_evidence_must_exist_in_policy_manifest_and_archive(tmp_path: Path) -> None:
    repo, manifest, archive = build_fixture(tmp_path)

    report = checker.audit_public_evidence(
        repo_root=repo,
        manifest_path=manifest,
        archive_path=archive,
        include_files=EVIDENCE,
        include_dirs=(),
        required_files=EVIDENCE,
        validator_required=EVIDENCE,
        package_name=PACKAGE,
    )

    assert report.passed is True
    assert report.citations == EVIDENCE


def test_missing_archive_member_fails_even_when_source_and_manifest_exist(tmp_path: Path) -> None:
    repo, manifest, archive = build_fixture(tmp_path, omit_from_archive={EVIDENCE[1]})

    report = checker.audit_public_evidence(
        repo_root=repo,
        manifest_path=manifest,
        archive_path=archive,
        include_files=EVIDENCE,
        include_dirs=(),
        required_files=EVIDENCE,
        validator_required=EVIDENCE,
        package_name=PACKAGE,
    )

    assert report.passed is False
    assert f"cited evidence is absent from public artifact tar: {EVIDENCE[1]}" in report.findings


def test_directory_references_are_not_treated_as_file_citations(tmp_path: Path) -> None:
    repo, manifest, archive = build_fixture(tmp_path)
    section = repo / "paper/sections/appendix.tex"
    section.write_text(section.read_text() + "\\path{analysis/fold/}\n", encoding="utf-8")

    citations = checker.discover_citations(repo)

    assert set(citations) == set(EVIDENCE)


def build_fixture(
    tmp_path: Path,
    *,
    omit_from_archive: set[str] | None = None,
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    section = repo / "paper/sections/appendix.tex"
    section.parent.mkdir(parents=True)
    section.write_text(
        "\\path{analysis/fold/proof.txt}\n"
        "\\filepath{analysis/investigation-evidence/audit.md}\n",
        encoding="utf-8",
    )
    for rel in EVIDENCE:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"public evidence for {rel}\n", encoding="utf-8")

    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(
        json.dumps({"files": [{"path": rel} for rel in EVIDENCE]}, indent=2) + "\n",
        encoding="utf-8",
    )
    archive = tmp_path / "artifact.tar.gz"
    omitted = omit_from_archive or set()
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for rel in EVIDENCE:
                    if rel in omitted:
                        continue
                    data = (repo / rel).read_bytes()
                    info = tarfile.TarInfo(f"{PACKAGE}/{rel}")
                    info.size = len(data)
                    info.mtime = 0
                    tar.addfile(info, io.BytesIO(data))
    return repo, manifest, archive
