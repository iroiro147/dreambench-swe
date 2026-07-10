#!/usr/bin/env python3
"""Fail-closed manuscript claim hygiene guard for DreamBench-SWE v2.

This checker is intentionally local and read-only. It scans the manuscript
sources for stale post-reanchor claim hazards that are easy to miss after the
canonical v2 analyzers land. It does not inspect benchmark outputs, run
analyzers, compile TeX, package artifacts, or contact the VPS.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCAN_GLOBS = ("paper/main.tex", "paper/sections/*.tex")

V2_PENDING_RE = re.compile(r"\b" + "V2-" + r"PENDING\b")
MEM0_RE = re.compile(r"\bB5-MEM0(?:-LIT)?\b")
PAPER_B_RE = re.compile(r"\bDreaming Agents\b|<LOCAL_PROJECT>|\bPaper B\b", re.IGNORECASE)
INVALID_CONSTRUCT_GUARANTEE_RE = re.compile(
    r"\b(?:effective\s+)?(?:anti[- ]hoarding|abstention)\s+guarantee\b",
    re.IGNORECASE,
)

COST_VALUE_RE = re.compile(
    r"\b("
    r"cost[- ]?effective(?:ness)?|"
    r"success[- ]?per[- ]?cost|"
    r"cost[- ]?per[- ]?success|"
    r"resource[- ]?value|"
    r"Pareto|"
    r"value\s+(?:claim|proposition|per|frontier|for\s+money)|"
    r"buy(?:s|ing)?\s+(?:more|better|additional)\s+(?:success|performance|coverage)|"
    r"efficien(?:t|cy)\s+(?:baseline|system|frontier|trade[- ]?off)"
    r")\b",
    re.IGNORECASE,
)

BACKBONE_RISK_RE = re.compile(
    r"\b("
    r"GLM(?:[- ]?5(?:\.2)?)?|"
    r"second[- ]backbone|"
    r"cross[- ]backbone|"
    r"across\s+(?:models|backbones)|"
    r"generaliz(?:e|es|ation)\s+across|"
    r"transfer\s+(?:fold|to|across)|"
    r"frontier\s+peer|"
    r"Kimi"
    r")\b",
    re.IGNORECASE,
)

BACKBONE_SCOPE_RE = re.compile(
    r"\b("
    r"limitation|future work|future-work|scoped[- ]out|scope[d]? out|"
    r"not run|not stabilized|did not stabilize|failed to stabilize|"
    r"attempted|exploratory|pilot|single[- ]backbone|"
    r"no\s+(?:cross[- ]backbone|generalization|transfer)\s+claim|"
    r"do\s+not\s+claim|does\s+not\s+claim|cannot\s+claim"
    r")\b",
    re.IGNORECASE,
)

SUPPLEMENTAL_MEM0_RE = re.compile(
    r"\b(supplemental|failure[- ]analysis|failure analysis|diagnostic|appendix|not headline)\b",
    re.IGNORECASE,
)

TITLE_CONTEXT_RE = re.compile(r"\\title\b|\\sys\{|\\bench\{|title", re.IGNORECASE)
HEADLINE_RESULTS_FILE_RE = re.compile(r"(?:^|/)(?:07_results|results|.*tables?).*\.tex$", re.IGNORECASE)
HEADLINE_RESULTS_LINE_RE = re.compile(
    r"\\(?:section|subsection|paragraph|caption|label)\b|"
    r"\\begin\{(?:table|tabular|figure)\}|"
    r"\\input\{analysis/fold/v2_tables",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: Path
    line: int
    message: str
    text: str

    def format(self, repo_root: Path) -> str:
        try:
            display_path = self.path.resolve().relative_to(repo_root.resolve())
        except ValueError:
            display_path = self.path
        clean_text = " ".join(self.text.strip().split())
        return (
            f"FAIL code={self.code} file={display_path} line={self.line} "
            f"message={quote_field(self.message)} text={quote_field(clean_text)}"
        )


@dataclass(frozen=True)
class ClaimHygieneReport:
    findings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def failure_count(self) -> int:
        return len(self.findings)

    def lines(self, repo_root: Path) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        lines = ["DreamBench-SWE v2 manuscript claim hygiene audit"]
        lines.extend(finding.format(repo_root) for finding in self.findings)
        lines.append(f"STATUS {status} failures={self.failure_count}")
        return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    paths = tuple(resolve_scan_paths(repo_root, args.path, args.scan_glob))
    report = audit_claim_hygiene(
        repo_root=repo_root,
        paths=paths,
        allow_cost_value_claims=args.allow_cost_value_claims,
        allow_supplemental_mem0=args.allow_supplemental_mem0,
    )
    print("\n".join(report.lines(repo_root)))
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check post-reanchor manuscript sources for stale v2 claim hazards."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="specific TeX file to scan; may be repeated. Defaults to manuscript glob set.",
    )
    parser.add_argument(
        "--scan-glob",
        action="append",
        default=[],
        help="glob relative to repo root; used only when --path is omitted.",
    )
    parser.add_argument(
        "--allow-cost-value-claims",
        action="store_true",
        help="allow cost/value/Pareto language after the F11 cost-frontier gate passes.",
    )
    parser.add_argument(
        "--allow-supplemental-mem0",
        action="store_true",
        help="allow B5-MEM0 mentions only in explicit supplemental or failure-analysis contexts.",
    )
    return parser.parse_args(argv)


def resolve_scan_paths(repo_root: Path, explicit_paths: Sequence[str], scan_globs: Sequence[str]) -> list[Path]:
    if explicit_paths:
        return sorted({resolve_under(repo_root, Path(path)) for path in explicit_paths})

    patterns = tuple(scan_globs) if scan_globs else DEFAULT_SCAN_GLOBS
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path for path in repo_root.glob(pattern) if path.is_file())
    return sorted(paths)


def resolve_under(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def audit_claim_hygiene(
    *,
    repo_root: Path,
    paths: Sequence[Path],
    allow_cost_value_claims: bool = False,
    allow_supplemental_mem0: bool = False,
) -> ClaimHygieneReport:
    findings: list[Finding] = []
    for path in paths:
        findings.extend(
            audit_file(
                repo_root=repo_root,
                path=path,
                allow_cost_value_claims=allow_cost_value_claims,
                allow_supplemental_mem0=allow_supplemental_mem0,
            )
        )
    return ClaimHygieneReport(tuple(findings))


def audit_file(
    *,
    repo_root: Path,
    path: Path,
    allow_cost_value_claims: bool,
    allow_supplemental_mem0: bool,
) -> list[Finding]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [Finding("READ_ERROR", path, 0, f"could not read file: {exc}", "")]

    findings: list[Finding] = []
    for index, line in enumerate(lines, start=1):
        window = context_window(lines, index)

        if V2_PENDING_RE.search(line):
            findings.append(Finding("V2_PENDING", path, index, "unresolved v2 pending marker", line))

        if PAPER_B_RE.search(line):
            findings.append(Finding("PAPER_B_TERM", path, index, "Paper B term is forbidden in Paper A", line))

        if INVALID_CONSTRUCT_GUARANTEE_RE.search(line):
            findings.append(
                Finding(
                    "INVALID_CONSTRUCT_GUARANTEE",
                    path,
                    index,
                    "C9/C10 construct-validity failure forbids anti-hoarding or abstention guarantee claims",
                    line,
                )
            )

        if MEM0_RE.search(line) and mem0_is_headline_surface(repo_root, path, index, line, window):
            if not (allow_supplemental_mem0 and SUPPLEMENTAL_MEM0_RE.search(window)):
                findings.append(
                    Finding(
                        "MEM0_HEADLINE_SURFACE",
                        path,
                        index,
                        "B5-MEM0/B5-MEM0-LIT appears in a headline result surface",
                        line,
                    )
                )

        if not allow_cost_value_claims and COST_VALUE_RE.search(line):
            findings.append(
                Finding(
                    "COST_VALUE_CLAIM",
                    path,
                    index,
                    "cost-effectiveness/value/Pareto language requires explicit post-F11 allowance",
                    line,
                )
            )

        if BACKBONE_RISK_RE.search(line) and not BACKBONE_SCOPE_RE.search(window):
            findings.append(
                Finding(
                    "BACKBONE_GENERALIZATION",
                    path,
                    index,
                    "GLM/second-backbone/generalization language is not scoped as limitation or future work",
                    line,
                )
            )

    return findings


def context_window(lines: Sequence[str], line_number: int, radius: int = 2) -> str:
    start = max(0, line_number - radius - 1)
    end = min(len(lines), line_number + radius)
    return "\n".join(lines[start:end])


def mem0_is_headline_surface(repo_root: Path, path: Path, line_number: int, line: str, window: str) -> bool:
    rel = relative_posix(repo_root, path)
    if rel == "paper/main.tex":
        return TITLE_CONTEXT_RE.search(line) is not None or TITLE_CONTEXT_RE.search(window) is not None
    if rel.endswith("paper/sections/01_abstract.tex"):
        return True
    if HEADLINE_RESULTS_FILE_RE.search(rel):
        return True
    if HEADLINE_RESULTS_LINE_RE.search(line) or table_environment_open(window):
        return True
    return False


def table_environment_open(text: str) -> bool:
    begins = len(re.findall(r"\\begin\{(?:table|tabular)\}", text))
    ends = len(re.findall(r"\\end\{(?:table|tabular)\}", text))
    return begins > ends


def relative_posix(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def quote_field(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


if __name__ == "__main__":
    raise SystemExit(main())
