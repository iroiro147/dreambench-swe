#!/usr/bin/env python3
"""Fail closed on raw, private, or unsafe material in a public tree/archive."""
from __future__ import annotations

import argparse
import hashlib
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


HIDDEN_ORACLE_PREFIX = "experiments/env/" + "oracles/"
HIDDEN_REFSOL_PREFIX = "experiments/env/" + "refsol/"
PRIVATE_PROJECT = "dreamforge-" + "3seed"
PAPER_B_PROJECT = "paper-" + "dreaming-agents"
LOCAL_USERS_PREFIX = "/" + "Users/"
EMPLOYER_DOMAIN = "@jus" + "pay.in"
SCHOOL_DOMAIN = "@masters" + "union.org"

FORBIDDEN_PATH_PREFIXES = (
    HIDDEN_ORACLE_PREFIX,
    HIDDEN_REFSOL_PREFIX,
    "experiments/results/",
    "experiments/validation/",
    "logs/",
    "handoff/",
    "bins/",
)
FORBIDDEN_PATH_PARTS = {".git", "__MACOSX"}
FIXTURE_LIMITS = {
    "tests/fixtures/contamination/escaped_b0_results.json": 100_000,
    "tests/fixtures/contamination/a2_config_results.json": 100_000,
}
KNOWN_RAW_SHA256 = {
    "a983abc068bd9ec52c9caa1dddb0ae9260e667b246612a0f843cb92a284411e2",
    "849db3fe70494ea7b0a900c7415338da47082f22f39db37d45c46f5ca136abeb",
}
FORBIDDEN_TEXT_PATTERNS = (
    (re.compile(re.escape(LOCAL_USERS_PREFIX)), "local user path"),
    (re.compile(r"/root/" + re.escape(PRIVATE_PROJECT)), "private VPS project path"),
    (re.compile(re.escape(PRIVATE_PROJECT)), "private project name"),
    (re.compile(re.escape(PAPER_B_PROJECT)), "Paper B project name"),
    (re.compile(re.escape(EMPLOYER_DOMAIN), re.IGNORECASE), "employer email"),
    (re.compile(re.escape(SCHOOL_DOMAIN), re.IGNORECASE), "institutional email"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "API secret"),
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "GitHub token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
)


@dataclass(frozen=True)
class PublicTreeReport:
    files_scanned: int
    findings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def lines(self) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        lines = ["DreamBench-SWE public-tree audit", f"files_scanned={self.files_scanned}"]
        lines.extend(f"FAIL {finding}" for finding in self.findings)
        lines.append(f"STATUS {status} failures={len(self.findings)}")
        return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_archive(args.archive) if args.archive is not None else audit_root(args.root)
    print("\n".join(report.lines()))
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--archive", type=Path)
    target.add_argument("--root", type=Path)
    return parser.parse_args(argv)


def audit_root(root: Path) -> PublicTreeReport:
    findings: list[str] = []
    entries: list[tuple[str, bytes]] = []
    if not root.is_dir():
        return PublicTreeReport(0, (f"public root is missing: {root}",))
    for path in sorted(root.rglob("*")):
        rel_path = path.relative_to(root)
        if rel_path.parts[:1] == (".git",):
            continue
        rel = rel_path.as_posix()
        if path.is_symlink():
            findings.append(f"public tree contains symlink: {rel}")
            continue
        if path.is_file():
            try:
                entries.append((rel, path.read_bytes()))
            except OSError as exc:
                findings.append(f"public tree file could not be read: {rel}: {exc}")
    findings.extend(audit_entries(entries))
    return PublicTreeReport(len(entries), tuple(findings))


def audit_archive(path: Path) -> PublicTreeReport:
    findings: list[str] = []
    entries: list[tuple[str, bytes]] = []
    if not path.is_file():
        return PublicTreeReport(0, (f"public archive is missing: {path}",))
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            top_levels: set[str] = set()
            seen: set[str] = set()
            cleaned: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
            for member in members:
                rel = clean_archive_path(member.name)
                if rel is None:
                    findings.append(f"unsafe archive member path: {member.name!r}")
                    continue
                top_levels.add(rel.parts[0])
                if rel.as_posix() in seen:
                    findings.append(f"duplicate archive member: {rel.as_posix()}")
                    continue
                seen.add(rel.as_posix())
                if not member.isfile() and not member.isdir():
                    findings.append(f"unsupported archive member type: {rel.as_posix()}")
                    continue
                cleaned.append((member, rel))
            if len(top_levels) != 1:
                findings.append(f"archive must have exactly one top-level directory: {sorted(top_levels)}")
            prefix = next(iter(top_levels), "")
            for member, rel in cleaned:
                if not member.isfile():
                    continue
                if len(rel.parts) < 2:
                    findings.append(f"archive file is outside the top-level directory: {rel.as_posix()}")
                    continue
                source = archive.extractfile(member)
                if source is None:
                    findings.append(f"archive member could not be read: {rel.as_posix()}")
                    continue
                stripped = PurePosixPath(*rel.parts[1:]).as_posix() if rel.parts[0] == prefix else rel.as_posix()
                entries.append((stripped, source.read()))
    except (OSError, tarfile.TarError) as exc:
        findings.append(f"public archive could not be read: {path}: {exc}")
    findings.extend(audit_entries(entries))
    return PublicTreeReport(len(entries), tuple(findings))


def clean_archive_path(raw: str) -> PurePosixPath | None:
    if not raw or "\\" in raw:
        return None
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts or "." in rel.parts:
        return None
    return rel


def audit_entries(entries: Iterable[tuple[str, bytes]]) -> list[str]:
    findings: list[str] = []
    for rel, data in entries:
        path = PurePosixPath(rel)
        if any(part in FORBIDDEN_PATH_PARTS for part in path.parts):
            findings.append(f"forbidden path component: {rel}")
        if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
            findings.append(f"forbidden public path: {rel}")
        limit = FIXTURE_LIMITS.get(rel)
        if limit is not None and len(data) > limit:
            findings.append(f"contamination fixture is too large: {rel} size={len(data)} limit={limit}")
        digest = hashlib.sha256(data).hexdigest()
        if digest in KNOWN_RAW_SHA256:
            findings.append(f"known raw operational fixture is present: {rel} sha256={digest}")
        if is_text(data):
            text = data.decode("utf-8", errors="replace")
            for pattern, label in FORBIDDEN_TEXT_PATTERNS:
                if pattern.search(text):
                    findings.append(f"{rel} contains forbidden {label}")
    return findings


def is_text(data: bytes) -> bool:
    return b"\0" not in data[:4096]


if __name__ == "__main__":
    raise SystemExit(main())
