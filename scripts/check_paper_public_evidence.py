#!/usr/bin/env python3
"""Require every manuscript-cited public evidence file in the artifact package."""
from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import package_artifact
from scripts import validate_submission_package as validator


DEFAULT_MANIFEST = REPO_ROOT / "dist" / "MANIFEST.json"
DEFAULT_ARCHIVE = REPO_ROOT / "dist" / package_artifact.PUBLIC_ARCHIVE
CITATION_RE = re.compile(r"\\(?:path|filepath)\{(analysis/[^{}]+)\}")


@dataclass(frozen=True)
class EvidenceReport:
    citations: tuple[str, ...]
    findings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def lines(self) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        lines = ["DreamBench-SWE manuscript public-evidence audit"]
        lines.append(f"citations={len(self.citations)}")
        lines.extend(f"PASS cited public evidence: {path}" for path in self.citations)
        lines.extend(f"FAIL {finding}" for finding in self.findings)
        lines.append(f"STATUS {status} failures={len(self.findings)}")
        return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_public_evidence(
        repo_root=args.repo_root,
        manifest_path=args.manifest,
        archive_path=args.archive,
    )
    print("\n".join(report.lines()))
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    return parser.parse_args(argv)


def discover_citations(repo_root: Path) -> dict[str, tuple[str, ...]]:
    occurrences: dict[str, set[str]] = {}
    tex_paths = [repo_root / "paper" / "main.tex"]
    tex_paths.extend(sorted((repo_root / "paper" / "sections").glob("*.tex")))
    for tex_path in tex_paths:
        if not tex_path.is_file():
            continue
        text = tex_path.read_text(encoding="utf-8")
        for raw in CITATION_RE.findall(text):
            rel = clean_file_citation(raw)
            if rel is None:
                continue
            source = tex_path.relative_to(repo_root).as_posix()
            occurrences.setdefault(rel, set()).add(source)
    return {rel: tuple(sorted(sources)) for rel, sources in sorted(occurrences.items())}


def clean_file_citation(raw: str) -> str | None:
    if "\\" in raw:
        return None
    rel = PurePosixPath(raw.strip())
    if rel.is_absolute() or ".." in rel.parts or "." in rel.parts:
        return None
    if raw.endswith("/") or not rel.suffix:
        return None
    if rel.parts[:1] != ("analysis",):
        return None
    return rel.as_posix()


def audit_public_evidence(
    *,
    repo_root: Path,
    manifest_path: Path,
    archive_path: Path,
    include_files: Iterable[str] | None = None,
    include_dirs: Iterable[str] | None = None,
    required_files: Iterable[str] | None = None,
    validator_required: Iterable[str] | None = None,
    package_name: str = package_artifact.PACKAGE_NAME,
) -> EvidenceReport:
    repo_root = repo_root.resolve()
    citations = discover_citations(repo_root)
    findings: list[str] = []

    include_file_set = set(
        include_files
        if include_files is not None
        else package_artifact.INCLUDE_FILES + package_artifact.SCRIPT_FILES
    )
    include_dir_set = set(include_dirs if include_dirs is not None else package_artifact.INCLUDE_DIRS)
    required_set = set(
        required_files if required_files is not None else package_artifact.REQUIRED_EXPLICIT_FILES
    )
    validator_set = set(
        validator_required if validator_required is not None else all_validator_required_paths()
    )

    manifest_paths = load_manifest_paths(manifest_path, findings)
    archive_paths = load_archive_paths(archive_path, package_name, findings)

    for rel, sources in citations.items():
        source_label = ", ".join(sources)
        if not (repo_root / rel).is_file():
            findings.append(f"cited evidence source is missing: {rel} cited by {source_label}")
        if not policy_includes(rel, include_file_set, include_dir_set):
            findings.append(f"cited evidence is absent from package include policy: {rel}")
        if rel not in required_set:
            findings.append(f"cited evidence is not fail-closed in REQUIRED_EXPLICIT_FILES: {rel}")
        if rel not in validator_set:
            findings.append(f"cited evidence is not required by package validator: {rel}")
        if rel not in manifest_paths:
            findings.append(f"cited evidence is absent from public MANIFEST.json: {rel}")
        if f"{package_name}/{rel}" not in archive_paths:
            findings.append(f"cited evidence is absent from public artifact tar: {rel}")

    if not citations:
        findings.append("no concrete analysis evidence citations were discovered in manuscript TeX")
    return EvidenceReport(tuple(citations), tuple(findings))


def policy_includes(rel: str, include_files: set[str], include_dirs: set[str]) -> bool:
    if rel in include_files:
        return True
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix.rstrip("/") + "/") for prefix in include_dirs)


def all_validator_required_paths() -> tuple[str, ...]:
    return (
        validator.REQUIRED_V2_ANALYSIS_ARTIFACTS
        + validator.REQUIRED_V2_FIGURES
        + validator.REQUIRED_V2_INPUTS
        + validator.REQUIRED_V2_SCRIPTS
        + validator.REQUIRED_PACKAGE_PROOFS
    )


def load_manifest_paths(path: Path, findings: list[str]) -> set[str]:
    if not path.is_file():
        findings.append(f"public manifest is missing: {path}")
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(f"public manifest could not be parsed: {path}: {exc}")
        return set()
    files = payload.get("files") if isinstance(payload, Mapping) else None
    if not isinstance(files, list):
        findings.append(f"public manifest files field is invalid: {path}")
        return set()
    return {
        str(item["path"])
        for item in files
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }


def load_archive_paths(path: Path, package_name: str, findings: list[str]) -> set[str]:
    if not path.is_file():
        findings.append(f"public artifact archive is missing: {path}")
        return set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            return set(archive.getnames())
    except (OSError, tarfile.TarError) as exc:
        findings.append(f"public artifact archive could not be read: {path}: {exc}")
        return set()


if __name__ == "__main__":
    raise SystemExit(main())
