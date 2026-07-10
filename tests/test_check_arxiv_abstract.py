"""Regression coverage for arXiv abstract metadata validation."""
from __future__ import annotations

from pathlib import Path

from scripts import check_arxiv_abstract as checker


APPROVED = (
    "DreamBench-SWE is a multi-session benchmark for software-agent memory hygiene. "
    "Each sequence ends in a third-session software task whose executable oracle depends "
    "on hidden, non-inferable information available only in earlier-session evidence. We "
    "evaluate a reference probe that preserves raw trajectories and derives typed memories "
    "through offline consolidation, contradiction repair, grounded replay, stale-memory "
    "suppression, and retrieval gating. In the scaled confirmatory fold, 60 traps are "
    "evaluated over three seeds. The primary trap-clustered comparison does not distinguish "
    "the hybrid typed-plus-raw probe from strong verbatim event memory: 95/180 versus 89/180 "
    "S3 passes, signed statistic +6, exact permutation p=0.518, Holm-adjusted p=1.0. Five "
    "additional preregistered comparisons also fail to reject. The admission funnel is "
    "complete (30/30 authored traps dry-valid, live-valid, and admitted), but strengthened "
    "construct validity fails for C9 and C10 because the no-memory baseline passes all 12 "
    "and all 6 cells, respectively; these strata are therefore not evidence for "
    "anti-hoarding or abstention. DreamBench-SWE's durable contribution is a controlled, "
    "executable protocol for exposing multi-session software-memory failures, together with "
    "a fully disclosed null result and reference implementation."
)
APPROVED_SHA256 = "9ee75985d1017d9c40c2e312122c84a42fca6788be55f9c6e9fed6f701e5bc42"


def write_abstract(path: Path, body: str) -> Path:
    path.write_text(f"\\begin{{abstract}}\n{body}\n\\end{{abstract}}\n", encoding="utf-8")
    return path


def test_approved_abstract_has_frozen_length_and_hash(tmp_path: Path) -> None:
    report = checker.audit_abstract(
        write_abstract(tmp_path / "abstract.tex", APPROVED),
        expected_characters=1315,
        expected_sha256=APPROVED_SHA256,
    )

    assert report.passed is True
    assert report.words == 170


def test_exact_1920_character_boundary_passes(tmp_path: Path) -> None:
    report = checker.audit_abstract(write_abstract(tmp_path / "abstract.tex", "a" * 1920))

    assert report.passed is True
    assert report.characters == 1920


def test_1921_characters_fails(tmp_path: Path) -> None:
    report = checker.audit_abstract(write_abstract(tmp_path / "abstract.tex", "a" * 1921))

    assert report.passed is False
    assert "abstract has 1921 characters, limit is 1920" in report.findings


def test_tex_and_non_ascii_metadata_fail(tmp_path: Path) -> None:
    report = checker.audit_abstract(
        write_abstract(
            tmp_path / "abstract.tex",
            r"A \textbf{bold} claim with an en dash " + chr(0x2013) + " and cafe.",
        )
    )

    assert report.passed is False
    assert any("TeX/metadata control characters" in item for item in report.findings)
    assert any("non-ASCII" in item for item in report.findings)


def test_duplicate_abstract_environment_fails(tmp_path: Path) -> None:
    path = tmp_path / "abstract.tex"
    path.write_text(
        "\\begin{abstract}\none\n\\end{abstract}\n"
        "\\begin{abstract}\ntwo\n\\end{abstract}\n",
        encoding="utf-8",
    )

    report = checker.audit_abstract(path)

    assert report.passed is False
    assert report.normalized == ""
