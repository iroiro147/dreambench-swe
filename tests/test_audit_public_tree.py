"""Regression coverage for public tree and archive hygiene."""
from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

from scripts import audit_public_tree as audit


LARGE_FIXTURE = "tests/fixtures/contamination/escaped_b0_results.json"


def test_clean_public_root_passes(tmp_path: Path) -> None:
    root = tmp_path / "public"
    (root / "tests/fixtures/contamination").mkdir(parents=True)
    (root / "README.md").write_text("# DreamBench-SWE\n", encoding="utf-8")
    (root / LARGE_FIXTURE).write_text('{"synthetic": true}\n', encoding="utf-8")

    report = audit.audit_root(root)

    assert report.passed is True
    assert report.files_scanned == 2


def test_large_operational_fixture_fails(tmp_path: Path) -> None:
    root = tmp_path / "public"
    path = root / LARGE_FIXTURE
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 100_001)

    report = audit.audit_root(root)

    assert report.passed is False
    assert any("contamination fixture is too large" in item for item in report.findings)


def test_hidden_scoring_path_and_identity_content_fail(tmp_path: Path) -> None:
    root = tmp_path / "public"
    hidden = root / ("experiments/env/" + "oracles/example/s3_test.py")
    hidden.parent.mkdir(parents=True)
    hidden.write_text("assert True\n", encoding="utf-8")
    readme = root / "README.md"
    readme.write_text("contact person" + "@masters" + "union.org\n", encoding="utf-8")

    report = audit.audit_root(root)

    assert report.passed is False
    assert any("forbidden public path" in item for item in report.findings)
    assert any("institutional email" in item for item in report.findings)


def test_archive_parent_traversal_fails(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.tar.gz"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                data = b"escape\n"
                info = tarfile.TarInfo("../escape.txt")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    report = audit.audit_archive(archive_path)

    assert report.passed is False
    assert any("unsafe archive member path" in item for item in report.findings)


def test_archive_root_file_is_not_accepted_as_top_level_directory(tmp_path: Path) -> None:
    archive_path = tmp_path / "root-file.tar.gz"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                data = b"not wrapped in a package directory\n"
                info = tarfile.TarInfo("README.md")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    report = audit.audit_archive(archive_path)

    assert report.passed is False
    assert any("outside the top-level directory" in item for item in report.findings)
