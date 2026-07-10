#!/usr/bin/env python3
"""Fail closed when Paper A release identity, hashes, or source surfaces drift."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "v2.0.5"
ARTIFACT_NAME = "dreambench-swe-artifact.tar.gz"
ARXIV_SOURCE_NAME = "dreambench-swe-paper-arxiv-source.tar.gz"
PAPER_PDF_NAME = "dreambench-swe-paper.pdf"
RELEASE_ASSETS = (
    ARTIFACT_NAME,
    ARXIV_SOURCE_NAME,
    PAPER_PDF_NAME,
    "MANIFEST.json",
    "CHECKSUMS.sha256",
)
ARTIFACT_BLOCK_RE = re.compile(
    rf"{re.escape(ARTIFACT_NAME)}\s+SHA-256:\s*([0-9a-f]{{64}})",
    re.MULTILINE,
)
RELEASE_URL_RE = re.compile(r"releases/tag/(v[0-9]+\.[0-9]+\.[0-9]+)")


@dataclass(frozen=True)
class CoherenceReport:
    findings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def lines(self) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        return [
            "DreamBench-SWE release-coherence audit",
            *(f"FAIL {finding}" for finding in self.findings),
            f"STATUS {status} failures={len(self.findings)}",
        ]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = check_release_coherence(
        repo_root=args.repo_root,
        dist_dir=args.dist_dir,
        paper_source=args.paper_source,
        arxiv_source=args.arxiv_source,
    )
    print("\n".join(report.lines()))
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--paper-source",
        type=Path,
        default=ROOT / "paper/sections/appendix_artifact.tex",
    )
    parser.add_argument(
        "--arxiv-source",
        type=Path,
        default=ROOT / "paper_arXiv/sections/appendix_artifact.tex",
    )
    return parser.parse_args(argv)


def check_release_coherence(
    *,
    repo_root: Path,
    dist_dir: Path,
    paper_source: Path,
    arxiv_source: Path,
) -> CoherenceReport:
    del repo_root  # Kept explicit so callers cannot accidentally rely on cwd.
    findings: list[str] = []
    artifact_path = dist_dir / ARTIFACT_NAME
    manifest_path = dist_dir / "MANIFEST.json"
    ledger_path = dist_dir / "RELEASE-CHECKSUMS.sha256"

    artifact_sha = sha256_file(artifact_path, findings)
    manifest = load_json(manifest_path, findings)
    if manifest.get("release_version") != RELEASE_VERSION:
        findings.append(
            f"MANIFEST.json release_version is {manifest.get('release_version')!r}, "
            f"expected {RELEASE_VERSION!r}"
        )

    ledger = parse_checksum_file(ledger_path, findings)
    if set(ledger) != set(RELEASE_ASSETS):
        missing = sorted(set(RELEASE_ASSETS) - set(ledger))
        extra = sorted(set(ledger) - set(RELEASE_ASSETS))
        if missing:
            findings.append(f"release checksum ledger missing assets: {missing}")
        if extra:
            findings.append(f"release checksum ledger has unexpected assets: {extra}")
    for name in RELEASE_ASSETS:
        path = dist_dir / name
        actual = sha256_file(path, findings)
        expected = ledger.get(name)
        if actual is not None and expected is not None and actual != expected:
            findings.append(f"release checksum mismatch for {name}: expected {expected}, got {actual}")

    source_hashes: dict[str, str] = {}
    for label, path in (("paper appendix", paper_source), ("arXiv appendix", arxiv_source)):
        text = read_text(path, label, findings)
        if text is None:
            continue
        source_hash = extract_artifact_hash(text, label, findings)
        if source_hash is not None:
            source_hashes[label] = source_hash
        release_versions = set(RELEASE_URL_RE.findall(text))
        if release_versions != {RELEASE_VERSION}:
            findings.append(
                f"{label} release URL versions are {sorted(release_versions)!r}, "
                f"expected only {RELEASE_VERSION!r}"
            )

    if artifact_sha is not None:
        for label, source_hash in source_hashes.items():
            if source_hash != artifact_sha:
                findings.append(
                    f"{label} artifact SHA is {source_hash}, actual artifact SHA is {artifact_sha}"
                )
        if ledger.get(ARTIFACT_NAME) not in (None, artifact_sha):
            findings.append(
                f"release ledger artifact SHA is {ledger[ARTIFACT_NAME]}, actual artifact SHA is {artifact_sha}"
            )

    arxiv_tar = dist_dir / ARXIV_SOURCE_NAME
    tar_text = read_arxiv_appendix(arxiv_tar, findings)
    if tar_text is not None:
        tar_hash = extract_artifact_hash(tar_text, "arXiv source tar appendix", findings)
        if artifact_sha is not None and tar_hash is not None and tar_hash != artifact_sha:
            findings.append(
                f"arXiv source tar appendix artifact SHA is {tar_hash}, actual artifact SHA is {artifact_sha}"
            )

    pdf_path = dist_dir / PAPER_PDF_NAME
    pdf_text = extract_pdf_text(pdf_path, findings)
    if pdf_text is not None and artifact_sha is not None:
        compact = re.sub(r"\s+", "", pdf_text)
        if artifact_sha not in compact:
            findings.append("paper PDF does not contain the actual artifact SHA")

    return CoherenceReport(tuple(findings))


def extract_artifact_hash(text: str, label: str, findings: list[str]) -> str | None:
    matches = ARTIFACT_BLOCK_RE.findall(text)
    if len(matches) != 1:
        findings.append(f"{label} must contain exactly one artifact SHA block; found {len(matches)}")
        return None
    return matches[0]


def read_arxiv_appendix(path: Path, findings: list[str]) -> str | None:
    if not path.is_file():
        findings.append(f"arXiv source archive missing: {path}")
        return None
    try:
        with tarfile.open(path, "r:gz") as archive:
            member = archive.extractfile("sections/appendix_artifact.tex")
            if member is None:
                findings.append("arXiv source archive missing sections/appendix_artifact.tex")
                return None
            return member.read().decode("utf-8")
    except (OSError, tarfile.TarError, UnicodeDecodeError) as exc:
        findings.append(f"cannot read arXiv appendix from {path}: {exc}")
        return None


def extract_pdf_text(path: Path, findings: list[str]) -> str | None:
    if not path.is_file():
        findings.append(f"paper PDF missing: {path}")
        return None
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        findings.append(f"cannot run pdftotext: {exc}")
        return None
    if result.returncode != 0:
        findings.append(f"pdftotext failed rc={result.returncode}: {result.stderr.strip()}")
        return None
    return result.stdout


def load_json(path: Path, findings: list[str]) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(f"cannot read JSON {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        findings.append(f"JSON root must be an object: {path}")
        return {}
    return payload


def parse_checksum_file(path: Path, findings: list[str]) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        findings.append(f"cannot read checksum ledger {path}: {exc}")
        return {}
    entries: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        parts = line.split(None, 1)
        if len(parts) != 2 or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None:
            findings.append(f"invalid checksum ledger line {line_number}: {line!r}")
            continue
        entries[parts[1].strip()] = parts[0]
    return entries


def read_text(path: Path, label: str, findings: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        findings.append(f"cannot read {label} {path}: {exc}")
        return None


def sha256_file(path: Path, findings: list[str]) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        findings.append(f"cannot hash {path}: {exc}")
        return None


if __name__ == "__main__":
    raise SystemExit(main())
