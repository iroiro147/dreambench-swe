#!/usr/bin/env python3
"""Validate the manuscript abstract against arXiv metadata constraints."""
from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ABSTRACT = REPO_ROOT / "paper" / "sections" / "01_abstract.tex"
BEGIN = r"\begin{abstract}"
END = r"\end{abstract}"
FORBIDDEN_METADATA_RE = re.compile(r"[\\{}$%#&_~^]")


@dataclass(frozen=True)
class AbstractReport:
    normalized: str
    findings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def characters(self) -> int:
        return len(self.normalized)

    @property
    def words(self) -> int:
        return len(self.normalized.split())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.normalized.encode("ascii", errors="ignore")).hexdigest()

    def lines(self) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        lines = ["DreamBench-SWE arXiv abstract audit"]
        lines.append(f"characters={self.characters}")
        lines.append(f"words={self.words}")
        lines.append(f"sha256={self.sha256}")
        lines.extend(f"FAIL {finding}" for finding in self.findings)
        lines.append(f"STATUS {status} failures={len(self.findings)}")
        return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_abstract(
        args.path,
        max_chars=args.max_chars,
        expected_sha256=args.expected_sha256,
        expected_characters=args.expected_characters,
    )
    print("\n".join(report.lines()))
    if args.print_abstract and report.normalized:
        print("ABSTRACT_BEGIN")
        print(report.normalized)
        print("ABSTRACT_END")
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_ABSTRACT)
    parser.add_argument("--max-chars", type=int, default=1920)
    parser.add_argument("--expected-sha256", default=None)
    parser.add_argument("--expected-characters", type=int, default=None)
    parser.add_argument("--print-abstract", action="store_true")
    return parser.parse_args(argv)


def audit_abstract(
    path: Path,
    *,
    max_chars: int = 1920,
    expected_sha256: str | None = None,
    expected_characters: int | None = None,
) -> AbstractReport:
    findings: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AbstractReport("", (f"abstract source could not be read: {path}: {exc}",))

    begin_count = source.count(BEGIN)
    end_count = source.count(END)
    if begin_count != 1 or end_count != 1:
        findings.append(
            f"expected exactly one abstract environment, found begin={begin_count} end={end_count}"
        )
        return AbstractReport("", tuple(findings))

    begin = source.index(BEGIN) + len(BEGIN)
    end = source.index(END, begin)
    body = source[begin:end]
    normalized = " ".join(body.split())

    if not normalized:
        findings.append("abstract is empty")
    if max_chars < 1:
        findings.append(f"max_chars must be positive, got {max_chars}")
    elif len(normalized) > max_chars:
        findings.append(f"abstract has {len(normalized)} characters, limit is {max_chars}")

    non_ascii = sorted({char for char in normalized if ord(char) < 32 or ord(char) > 126})
    if non_ascii:
        codepoints = ", ".join(f"U+{ord(char):04X}" for char in non_ascii)
        findings.append(f"abstract contains non-ASCII metadata characters: {codepoints}")

    forbidden = sorted(set(FORBIDDEN_METADATA_RE.findall(normalized)))
    if forbidden:
        findings.append("abstract contains TeX/metadata control characters: " + " ".join(forbidden))

    report = AbstractReport(normalized, tuple(findings))
    if expected_characters is not None and report.characters != expected_characters:
        findings.append(
            f"abstract character count is {report.characters}, expected {expected_characters}"
        )
    if expected_sha256 is not None and report.sha256 != expected_sha256:
        findings.append(f"abstract sha256 is {report.sha256}, expected {expected_sha256}")
    return AbstractReport(normalized, tuple(findings))


if __name__ == "__main__":
    raise SystemExit(main())
