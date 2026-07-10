"""Tests for the manuscript claim hygiene guard."""
from __future__ import annotations

from pathlib import Path

from scripts import check_paper_claim_hygiene as guard


PENDING_TOKEN = "V2-" + "PENDING"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def audit(repo: Path, **kwargs: bool) -> guard.ClaimHygieneReport:
    paths = tuple(guard.resolve_scan_paths(repo, (), ()))
    return guard.audit_claim_hygiene(repo_root=repo, paths=paths, **kwargs)


def make_clean_manuscript(repo: Path) -> None:
    write(
        repo / "paper" / "main.tex",
        "\\documentclass{article}\n\\title{\\bench{}: a benchmark}\n\\begin{document}\n\\end{document}\n",
    )
    write(
        repo / "paper" / "sections" / "01_abstract.tex",
        "We evaluate memory systems on the benchmark with clustered v2 analysis.\n",
    )
    write(
        repo / "paper" / "sections" / "07_results.tex",
        "\\section{Results}\nThe canonical analyzer reports the final ladder.\n",
    )


def finding_codes(report: guard.ClaimHygieneReport) -> set[str]:
    return {finding.code for finding in report.findings}


def test_passes_on_clean_temp_manuscript(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)

    report = audit(tmp_path)

    assert report.passed is True
    assert report.lines(tmp_path)[-1] == "STATUS PASS failures=0"


def test_fails_on_v2_pending_marker(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)
    write(tmp_path / "paper" / "sections" / "appendix_v2_protocol.tex", f"Remaining: {PENDING_TOKEN}.\n")

    report = audit(tmp_path)

    assert report.passed is False
    assert "V2_PENDING" in finding_codes(report)


def test_fails_on_b5_mem0_in_abstract_and_results(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)
    write(tmp_path / "paper" / "sections" / "01_abstract.tex", "B5-MEM0 improves the headline result.\n")
    write(tmp_path / "paper" / "sections" / "07_results.tex", "\\caption{B5-MEM0-LIT headline table}\n")

    report = audit(tmp_path)

    assert report.passed is False
    codes = [finding.code for finding in report.findings]
    assert codes.count("MEM0_HEADLINE_SURFACE") == 2


def test_allows_b5_mem0_only_when_supplemental_flag_and_context_are_explicit(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)
    write(
        tmp_path / "paper" / "sections" / "appendix_failure_analysis.tex",
        "Supplemental failure-analysis diagnostic: B5-MEM0 is not a headline baseline.\n",
    )
    write(tmp_path / "paper" / "sections" / "07_results.tex", "Headline B5-MEM0 result remains forbidden.\n")

    report = audit(tmp_path, allow_supplemental_mem0=True)

    assert report.passed is False
    mem0_findings = [finding for finding in report.findings if finding.code == "MEM0_HEADLINE_SURFACE"]
    assert len(mem0_findings) == 1
    assert mem0_findings[0].path.name == "07_results.tex"


def test_fails_on_cost_value_language_unless_allowed(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)
    write(tmp_path / "paper" / "sections" / "08_analysis.tex", "The method is cost-effective and Pareto dominant.\n")

    blocked = audit(tmp_path)
    allowed = audit(tmp_path, allow_cost_value_claims=True)

    assert "COST_VALUE_CLAIM" in finding_codes(blocked)
    assert "COST_VALUE_CLAIM" not in finding_codes(allowed)


def test_fails_on_unscoped_backbone_generalization_but_allows_limitation(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)
    write(
        tmp_path / "paper" / "sections" / "appendix_v2_protocol.tex",
        "The result generalizes across backbones including GLM-5.2.\n",
    )
    write(
        tmp_path / "paper" / "sections" / "09_limitations.tex",
        "Limitation: GLM-5.2 was attempted, did not stabilize, and no cross-backbone claim is made.\n",
    )

    report = audit(tmp_path)

    backbone_findings = [finding for finding in report.findings if finding.code == "BACKBONE_GENERALIZATION"]
    assert len(backbone_findings) == 1
    assert backbone_findings[0].path.name == "appendix_v2_protocol.tex"


def test_fails_on_paper_b_terms(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)
    write(tmp_path / "paper" / "sections" / "02_introduction.tex", "This is related to Dreaming Agents and Paper B.\n")

    report = audit(tmp_path)

    assert "PAPER_B_TERM" in finding_codes(report)


def test_fails_on_invalid_construct_guarantee(tmp_path: Path) -> None:
    make_clean_manuscript(tmp_path)
    write(
        tmp_path / "paper" / "sections" / "07_results.tex",
        "The retained strata establish an effective anti-hoarding guarantee.\n",
    )

    report = audit(tmp_path)

    assert "INVALID_CONSTRUCT_GUARANTEE" in finding_codes(report)


def test_cli_prints_machine_readable_failures(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    make_clean_manuscript(tmp_path)
    write(tmp_path / "paper" / "sections" / "10_conclusion.tex", f"Remaining {PENDING_TOKEN} marker.\n")

    rc = guard.main(["--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "FAIL code=V2_PENDING file=paper/sections/10_conclusion.tex line=1" in captured.out
    assert captured.out.strip().endswith("STATUS FAIL failures=1")
