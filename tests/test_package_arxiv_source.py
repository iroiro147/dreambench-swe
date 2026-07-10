"""Regression coverage for the arXiv source tarball builder."""
from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import pytest

from scripts import package_arxiv_source


REQUIRED_MEMBERS = {
    "main.tex",
    "main.bbl",
    "bibliography/sources.bib",
    "analysis/fold/v2_tables.tex",
    "sections/01_abstract.tex",
    "figures/architecture.tex",
}

FORBIDDEN_GENERATED = {
    "main.pdf",
    "main.log",
    "main.blg",
    "main.aux",
    "main.out",
}


def test_arxiv_source_tar_includes_bibliography_and_excludes_generated_files(tmp_path: Path) -> None:
    source = build_arxiv_source_fixture(tmp_path / "paper_arxiv")
    output = tmp_path / "dist" / "paper.tar.gz"
    output.parent.mkdir()

    members = package_arxiv_source.collect_members(source)
    package_arxiv_source.write_tarball(source, members, output)

    with tarfile.open(output, "r:gz") as archive:
        tar_paths = set(archive.getnames())

    assert REQUIRED_MEMBERS <= tar_paths
    assert not (FORBIDDEN_GENERATED & tar_paths)


def test_arxiv_source_tar_is_deterministic(tmp_path: Path) -> None:
    source = build_arxiv_source_fixture(tmp_path / "paper_arxiv")
    members = package_arxiv_source.collect_members(source)
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    package_arxiv_source.write_tarball(source, members, first)
    package_arxiv_source.write_tarball(source, members, second)

    assert sha256(first) == sha256(second)


def test_arxiv_source_packaging_requires_main_bbl(tmp_path: Path) -> None:
    source = build_arxiv_source_fixture(tmp_path / "paper_arxiv")
    (source / "main.bbl").unlink()

    with pytest.raises(SystemExit, match="required arXiv source file missing: main.bbl"):
        package_arxiv_source.collect_members(source)


def build_arxiv_source_fixture(source: Path) -> Path:
    for rel in REQUIRED_MEMBERS:
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {rel}\n", encoding="utf-8")
    for rel in FORBIDDEN_GENERATED:
        (source / rel).write_text(f"generated {rel}\n", encoding="utf-8")
    return source


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
