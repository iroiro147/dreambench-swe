"""Regression coverage for the detached v2.0.5 release-binding gate."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

from scripts import check_release_coherence as checker


def test_coherent_release_surfaces_pass(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture = build_release_fixture(tmp_path)
    monkeypatch.setattr(checker, "extract_pdf_text", lambda path, findings: fixture["appendix"])

    report = checker.check_release_coherence(
        repo_root=tmp_path,
        dist_dir=tmp_path / "dist",
        paper_source=tmp_path / "paper/sections/appendix_artifact.tex",
        arxiv_source=tmp_path / "paper_arXiv/sections/appendix_artifact.tex",
    )

    assert report.passed, "\n".join(report.lines())


def test_stale_manuscript_hash_fails(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture = build_release_fixture(tmp_path)
    stale = fixture["appendix"].replace(fixture["artifact_sha"], "0" * 64)
    paper_source = tmp_path / "paper/sections/appendix_artifact.tex"
    paper_source.write_text(stale, encoding="utf-8")
    monkeypatch.setattr(checker, "extract_pdf_text", lambda path, findings: stale)

    report = checker.check_release_coherence(
        repo_root=tmp_path,
        dist_dir=tmp_path / "dist",
        paper_source=paper_source,
        arxiv_source=tmp_path / "paper_arXiv/sections/appendix_artifact.tex",
    )

    assert report.passed is False
    assert any("paper appendix artifact SHA" in finding for finding in report.findings)
    assert any("paper PDF does not contain" in finding for finding in report.findings)


def test_release_manifest_version_drift_fails(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture = build_release_fixture(tmp_path)
    manifest_path = tmp_path / "dist/MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_version"] = "v2.0.4"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    rewrite_ledger(tmp_path / "dist")
    monkeypatch.setattr(checker, "extract_pdf_text", lambda path, findings: fixture["appendix"])

    report = checker.check_release_coherence(
        repo_root=tmp_path,
        dist_dir=tmp_path / "dist",
        paper_source=tmp_path / "paper/sections/appendix_artifact.tex",
        arxiv_source=tmp_path / "paper_arXiv/sections/appendix_artifact.tex",
    )

    assert report.passed is False
    assert any("release_version is 'v2.0.4'" in finding for finding in report.findings)


def build_release_fixture(tmp_path: Path) -> dict[str, str]:
    dist = tmp_path / "dist"
    dist.mkdir()
    artifact = dist / checker.ARTIFACT_NAME
    artifact.write_bytes(b"frozen public artifact\n")
    artifact_sha = sha256(artifact)
    appendix = (
        f"https://github.com/iroiro147/dreambench-swe/releases/tag/{checker.RELEASE_VERSION}\n"
        f"{checker.ARTIFACT_NAME}\nSHA-256: {artifact_sha}\n"
    )

    for rel in (
        "paper/sections/appendix_artifact.tex",
        "paper_arXiv/sections/appendix_artifact.tex",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(appendix, encoding="utf-8")

    (dist / "MANIFEST.json").write_text(
        json.dumps({"release_version": checker.RELEASE_VERSION}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (dist / "CHECKSUMS.sha256").write_text("package checksums\n", encoding="utf-8")
    (dist / checker.PAPER_PDF_NAME).write_bytes(b"%PDF fixture\n")
    write_arxiv_tar(dist / checker.ARXIV_SOURCE_NAME, appendix)
    rewrite_ledger(dist)
    return {"artifact_sha": artifact_sha, "appendix": appendix}


def write_arxiv_tar(path: Path, appendix: str) -> None:
    data = appendix.encode()
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as archive:
                info = tarfile.TarInfo("sections/appendix_artifact.tex")
                info.size = len(data)
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))


def rewrite_ledger(dist: Path) -> None:
    lines = [f"{sha256(dist / name)}  {name}" for name in checker.RELEASE_ASSETS]
    (dist / "RELEASE-CHECKSUMS.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
