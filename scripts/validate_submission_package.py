#!/usr/bin/env python3
"""Validate the final DreamBench-SWE v2 submission package.

This is an independent post-packaging check. It validates the dist-level
archive/manifest/checksum files and the package directory contents without
depending on live fold outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


PACKAGE_NAME = "dreambench-swe-artifact"
RELEASE_VERSION = "v2.0.5"
PUBLIC_ARCHIVE = "dreambench-swe-artifact.tar.gz"
PRIVATE_ARCHIVE = "dreambench-swe-artifact-reviewer-private.tar.gz"
VALID_MODES = {"public", "private"}

REQUIRED_V2_ANALYSIS_ARTIFACTS = (
    "analysis/fold/confirmatory.json",
    "analysis/fold/v2_fold.json",
    "analysis/fold/v2_confirmatory_clustered.json",
    "analysis/fold/v2_confirmatory_clustered.stdout.json",
    "analysis/fold/v2_hygiene_oracle.json",
    "analysis/fold/v2_hygiene_oracle.tex",
    "analysis/fold/v2_cost_frontier.json",
    "analysis/fold/v2_cost_frontier.tex",
    "analysis/fold/admission_funnel.json",
    "analysis/fold/admission_funnel.tex",
    "analysis/fold/admission_funnel.md",
    "analysis/fold/v2_tables.tex",
)

REQUIRED_V2_FIGURES = (
    "paper/figures/v2_construct_coverage.pdf",
    "paper/figures/v2_ladder.pdf",
    "paper/figures/v2_verbatim_vs_synthesis.pdf",
)

REQUIRED_V2_INPUTS = (
    "experiments/env/sequences_confirmatory_v2.jsonl",
    "analysis/investigation-evidence/PREREGISTRATION.md",
)

REQUIRED_V2_SCRIPTS = (
    "scripts/batch_validate.py",
    "scripts/construct_validity.py",
    "scripts/analyze_confirmatory_v2.py",
    "scripts/admission_funnel.py",
    "scripts/hygiene_oracle.py",
    "scripts/cost_frontier.py",
    "scripts/generate_tables_v2.py",
    "scripts/make_v2_figures.py",
    "scripts/check_paper_a_postfold_preflight.py",
    "scripts/validate_submission_package.py",
    "scripts/check_v2_artifact_freshness.py",
    "scripts/check_v2_confirmatory_completion.py",
    "scripts/check_paper_claim_hygiene.py",
    "scripts/check_arxiv_abstract.py",
    "scripts/check_paper_public_evidence.py",
    "scripts/check_release_coherence.py",
    "scripts/audit_public_tree.py",
    "scripts/inject_secrets.py",
    "scripts/Dockerfile.api-agent",
    "scripts/package_arxiv_source.py",
    "scripts/vps_confirmatory_status.py",
)

REQUIRED_PACKAGE_PROOFS = (
    "LICENSE",
    "README.md",
    "requirements-artifact.txt",
    "docs/DATASHEET.md",
    "docs/trap_skeleton_spec.md",
    "analysis/fold/HERMETICITY-MANIFEST.md",
    "analysis/fold/CANARY-PROOF.txt",
    "analysis/fold/REPRO-MANIFEST.md",
    "analysis/fold/v2_post_analyzer_completion_check.txt",
    "analysis/investigation-evidence/PREREGISTRATION-V2.md",
    "analysis/investigation-evidence/V2-ANALYSIS-20260708T065236Z.md",
    "analysis/investigation-evidence/FABLE-CONSTRUCTS.md",
    "analysis/investigation-evidence/AUTHORING-WORKLIST.md",
    "analysis/investigation-evidence/SCALE1-FREEZE-DECISIONS.md",
    "analysis/investigation-evidence/CLUSTERED-STATS.md",
    "analysis/investigation-evidence/CONFIRMATORY-FOLD.md",
    "analysis/investigation-evidence/MEM0-ROW-FOLD-REPORT.md",
    "analysis/investigation-evidence/REPORT-Q-BENCH.md",
    "analysis/investigation-evidence/per_trap_matrix_and_leakage.json",
)

GENERATED_PACKAGE_FILES = {"MANIFEST.json", "CHECKSUMS.sha256"}
HIDDEN_ORACLE_PREFIX = "experiments/env/" + "oracles/"
HIDDEN_REFSOL_PREFIX = "experiments/env/" + "refsol/"
PUBLIC_EXCLUDED_PREFIXES = (HIDDEN_ORACLE_PREFIX, HIDDEN_REFSOL_PREFIX)
PRIVATE_REQUIRED_PREFIXES = PUBLIC_EXCLUDED_PREFIXES
LOCAL_ONLY_FILENAMES = {"AGENTS.md", "CLAUDE.md", "DAY_STATE.md", "NIGHT_STATE.md", "MEMORY.md"}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_NAME_RE = re.compile(r"(?i)(^|[-_.])(placeholder|stub|todo|tbd|fixme)([-_.]|$)")
PLACEHOLDER_CONTENT_RE = re.compile(
    r"(?is)^\s*(?:todo|tbd|placeholder|stub|not implemented|coming soon|lorem ipsum)[\s.!:;,_/\-]*$"
)
TEXT_SUFFIXES = {
    "",
    ".bib",
    ".cfg",
    ".csv",
    ".dockerfile",
    ".env",
    ".ini",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".py",
    ".sh",
    ".tex",
    ".txt",
    ".yaml",
    ".yml",
}

LEGACY_PROJECT_NAME = "paper-" + "dreaming-agents"
USERS_ROOT = "/" + "Users"

FORBIDDEN_CONTENT_PATTERNS = (
    (re.compile(re.escape(LEGACY_PROJECT_NAME)), "private project name"),
    (re.compile(r"/root/" + re.escape(LEGACY_PROJECT_NAME)), "private VPS project root"),
    (re.compile(re.escape(USERS_ROOT) + r"/"), "local user path"),
    (re.compile(r"\bsarthak\.singh\b", re.IGNORECASE), "local user identity"),
    (re.compile(r"\b" + "jus" + "pay" + r"\b", re.IGNORECASE), "employer-identifying text"),
    (re.compile(r"\bdesign\.purchase\b", re.IGNORECASE), "operator account identity"),
    (re.compile(r"\.codex/" + "plugins", re.IGNORECASE), "local Codex plugin path"),
    (re.compile("TEAM_" + "PROTOCOL", re.IGNORECASE), "local team protocol text"),
)
PUBLIC_NOMENCLATURE_FILES = {
    "README.md",
    "artifact/README.md",
    "experiments/README.md",
    "analysis/fold/v2_tables.tex",
    "analysis/fold/final_tables.tex",
    "analysis/fold/stats.tex",
}
PUBLIC_NOMENCLATURE_FORBIDDEN_PATTERNS = (
    (re.compile(r"(?i)dreamforge|dream forge|DREAMFORGE_ROOT"), "legacy public system name"),
    (re.compile(r"(?i)\bdreambench\b(?!-swe)"), "bare DreamBench name"),
)
REQUIRED_TEXT_ARTIFACTS_WITHOUT_PENDING = tuple(
    rel
    for rel in REQUIRED_V2_ANALYSIS_ARTIFACTS + REQUIRED_V2_INPUTS + REQUIRED_PACKAGE_PROOFS
    if PurePosixPath(rel).suffix in TEXT_SUFFIXES
)


class PackageValidationError(RuntimeError):
    """Raised when one or more package validation checks fail."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class ValidationSummary:
    package_name: str
    mode: str
    package_dir: Path
    archive_path: Path | None
    manifest_file_count: int
    checksum_file_count: int


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.package_dir is not None:
            summary = validate_package_directory(args.package_dir, mode=args.mode)
        else:
            summary = validate_dist_package(args.dist_dir, mode=args.mode, archive_path=args.archive)
    except PackageValidationError as exc:
        print("FAIL submission package validation failed")
        for error in exc.errors:
            print(f"- {error}")
        return 1

    print("OK submission package validation passed")
    print(f"package={summary.package_name}")
    print(f"mode={summary.mode}")
    print(f"package_dir={summary.package_dir}")
    if summary.archive_path is not None:
        print(f"archive={summary.archive_path}")
    print(f"manifest_files={summary.manifest_file_count}")
    print(f"checksum_files={summary.checksum_file_count}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the DreamBench-SWE v2 submission package after final packaging."
    )
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"), help="dist directory produced by package_artifact.py")
    parser.add_argument("--archive", type=Path, default=None, help="archive path; defaults to MANIFEST.json archive field")
    parser.add_argument("--package-dir", type=Path, default=None, help="validate an already extracted package directory")
    parser.add_argument("--mode", choices=("public", "private"), default=None, help="expected manifest mode")
    return parser.parse_args(argv)


def validate_dist_package(
    dist_dir: Path,
    *,
    mode: str | None = None,
    archive_path: Path | None = None,
) -> ValidationSummary:
    errors: list[str] = []
    dist_dir = dist_dir.resolve()
    manifest_path = dist_dir / "MANIFEST.json"
    checksums_path = dist_dir / "CHECKSUMS.sha256"

    manifest = _load_json_object(manifest_path, "dist MANIFEST.json", errors)
    package_name = _string_field(manifest, "package", "dist MANIFEST.json", errors) or PACKAGE_NAME
    _validate_release_version(manifest, "dist MANIFEST.json", errors)
    manifest_mode = _string_field(manifest, "mode", "dist MANIFEST.json", errors) or ""
    _validate_manifest_mode(manifest_mode, "dist MANIFEST.json", errors)
    archive_name = _string_field(manifest, "archive", "dist MANIFEST.json", errors)
    if archive_name is None:
        archive_name = PRIVATE_ARCHIVE if manifest_mode == "private" else PUBLIC_ARCHIVE
    else:
        archive_name = _validate_archive_name(archive_name, manifest_mode, "dist MANIFEST.json archive", errors)

    if mode is not None and manifest_mode and manifest_mode != mode:
        errors.append(f"dist MANIFEST.json mode is {manifest_mode!r}, expected {mode!r}")

    resolved_archive = archive_path.resolve() if archive_path is not None else dist_dir / archive_name
    dist_checksums = _parse_checksum_file(checksums_path, "dist CHECKSUMS.sha256", errors)
    _verify_dist_checksum_entry(dist_checksums, resolved_archive.name, resolved_archive, errors)
    _verify_dist_checksum_entry(dist_checksums, "MANIFEST.json", manifest_path, errors)

    package_checksum_name = f"{package_name}/CHECKSUMS.sha256"
    expected_package_checksum = dist_checksums.get(package_checksum_name)
    if expected_package_checksum is None:
        errors.append(f"dist CHECKSUMS.sha256 missing entry for {package_checksum_name}")

    with tempfile.TemporaryDirectory(prefix="dreambench-package-validate-") as tmp:
        package_dir = _extract_archive(resolved_archive, Path(tmp), package_name, errors)
        if package_dir is None:
            _raise_if_errors(errors)
            raise AssertionError("unreachable")
        summary = _validate_package_directory(
            package_dir,
            mode=mode,
            archive_path=resolved_archive,
            external_manifest_path=manifest_path,
            expected_package_checksum=expected_package_checksum,
            errors=errors,
        )

    _raise_if_errors(errors)
    return summary


def validate_package_directory(package_dir: Path, *, mode: str | None = None) -> ValidationSummary:
    errors: list[str] = []
    summary = _validate_package_directory(
        package_dir.resolve(),
        mode=mode,
        archive_path=None,
        external_manifest_path=None,
        expected_package_checksum=None,
        errors=errors,
    )
    _raise_if_errors(errors)
    return summary


def _validate_package_directory(
    package_dir: Path,
    *,
    mode: str | None,
    archive_path: Path | None,
    external_manifest_path: Path | None,
    expected_package_checksum: str | None,
    errors: list[str],
) -> ValidationSummary:
    if not package_dir.is_dir():
        errors.append(f"package directory missing: {package_dir}")
        return ValidationSummary(PACKAGE_NAME, mode or "", package_dir, archive_path, 0, 0)

    manifest_path = package_dir / "MANIFEST.json"
    checksums_path = package_dir / "CHECKSUMS.sha256"
    manifest = _load_json_object(manifest_path, "package MANIFEST.json", errors)
    package_name = _string_field(manifest, "package", "package MANIFEST.json", errors) or PACKAGE_NAME
    _validate_release_version(manifest, "package MANIFEST.json", errors)
    manifest_mode = _string_field(manifest, "mode", "package MANIFEST.json", errors) or ""
    _validate_manifest_mode(manifest_mode, "package MANIFEST.json", errors)
    if package_dir.name != package_name:
        errors.append(f"package directory name {package_dir.name!r} does not match manifest package {package_name!r}")
    if mode is not None and manifest_mode and manifest_mode != mode:
        errors.append(f"package MANIFEST.json mode is {manifest_mode!r}, expected {mode!r}")

    if external_manifest_path is not None and manifest_path.is_file() and external_manifest_path.is_file():
        if manifest_path.read_bytes() != external_manifest_path.read_bytes():
            errors.append("dist MANIFEST.json does not match package MANIFEST.json")

    if expected_package_checksum is not None:
        _verify_expected_sha256(checksums_path, expected_package_checksum, f"{package_name}/CHECKSUMS.sha256", errors)

    manifest_entries = _manifest_file_entries(manifest, errors)
    manifest_paths = set(manifest_entries)
    _validate_no_symlinks(package_dir, errors)
    actual_files = _package_file_paths(package_dir)
    payload_paths = {item for item in actual_files if item not in GENERATED_PACKAGE_FILES}
    checksum_paths = _parse_checksum_file(checksums_path, "package CHECKSUMS.sha256", errors)

    _validate_manifest_against_files(package_dir, manifest_entries, payload_paths, errors)
    _validate_internal_checksums(package_dir, checksum_paths, actual_files, errors)
    _validate_required_paths(manifest_paths, errors)
    _validate_required_text_artifacts_clean(package_dir, manifest_paths, errors)
    _validate_public_exclusions(manifest_paths | payload_paths, manifest_mode, errors)
    _validate_private_inclusions(manifest_paths | payload_paths, manifest_mode, errors)
    _validate_local_only_paths(manifest_paths | payload_paths, errors)
    _scan_placeholder_files(package_dir, payload_paths, errors)
    _scan_forbidden_content(package_dir, payload_paths, manifest_mode, errors)

    return ValidationSummary(
        package_name=package_name,
        mode=manifest_mode,
        package_dir=package_dir,
        archive_path=archive_path,
        manifest_file_count=len(manifest_entries),
        checksum_file_count=len(checksum_paths),
    )


def _manifest_file_entries(manifest: Mapping[str, Any], errors: list[str]) -> dict[str, Mapping[str, Any]]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        errors.append("MANIFEST.json field 'files' must be a list")
        return {}

    entries: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(raw_files):
        if not isinstance(item, Mapping):
            errors.append(f"MANIFEST.json files[{index}] must be an object")
            continue
        rel = _clean_package_relpath(item.get("path"), f"MANIFEST.json files[{index}].path", errors)
        if rel is None:
            continue
        if rel in GENERATED_PACKAGE_FILES:
            errors.append(f"MANIFEST.json must not list generated file {rel}")
        if rel in entries:
            errors.append(f"MANIFEST.json lists duplicate path {rel}")
            continue
        size = item.get("size")
        if not isinstance(size, int) or size < 0:
            errors.append(f"MANIFEST.json entry {rel} has invalid size {size!r}")
        sha = item.get("sha256")
        if not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None:
            errors.append(f"MANIFEST.json entry {rel} has invalid sha256 {sha!r}")
        if not isinstance(item.get("role"), str) or not item.get("role"):
            errors.append(f"MANIFEST.json entry {rel} missing non-empty role")
        entries[rel] = item
    return entries


def _validate_release_version(manifest: Mapping[str, Any], label: str, errors: list[str]) -> None:
    release_version = _string_field(manifest, "release_version", label, errors)
    if release_version is not None and release_version != RELEASE_VERSION:
        errors.append(
            f"{label} release_version is {release_version!r}, expected {RELEASE_VERSION!r}"
        )


def _validate_manifest_against_files(
    package_dir: Path,
    manifest_entries: Mapping[str, Mapping[str, Any]],
    payload_paths: set[str],
    errors: list[str],
) -> None:
    manifest_paths = set(manifest_entries)
    missing = sorted(manifest_paths - payload_paths)
    extra = sorted(payload_paths - manifest_paths)
    for rel in missing:
        errors.append(f"MANIFEST.json lists missing package file: {rel}")
    for rel in extra:
        errors.append(f"package contains file not listed in MANIFEST.json: {rel}")

    for rel, entry in manifest_entries.items():
        path = package_dir / rel
        if not path.is_file():
            continue
        expected_size = entry.get("size")
        if isinstance(expected_size, int) and path.stat().st_size != expected_size:
            errors.append(f"MANIFEST.json size mismatch for {rel}: expected {expected_size}, got {path.stat().st_size}")
        expected_sha = entry.get("sha256")
        if isinstance(expected_sha, str) and SHA256_RE.fullmatch(expected_sha):
            _verify_expected_sha256(path, expected_sha, f"MANIFEST.json entry {rel}", errors)


def _validate_internal_checksums(
    package_dir: Path,
    checksum_paths: Mapping[str, str],
    actual_files: set[str],
    errors: list[str],
) -> None:
    expected_checksum_paths = actual_files - {"CHECKSUMS.sha256"}
    missing = sorted(expected_checksum_paths - set(checksum_paths))
    extra = sorted(set(checksum_paths) - expected_checksum_paths)
    for rel in missing:
        errors.append(f"package CHECKSUMS.sha256 missing entry for {rel}")
    for rel in extra:
        errors.append(f"package CHECKSUMS.sha256 lists non-package file: {rel}")
    for rel, expected_sha in checksum_paths.items():
        path = package_dir / rel
        if path.is_file():
            _verify_expected_sha256(path, expected_sha, f"package CHECKSUMS.sha256 entry {rel}", errors)


def _validate_required_paths(manifest_paths: set[str], errors: list[str]) -> None:
    required = (
        REQUIRED_V2_ANALYSIS_ARTIFACTS
        + REQUIRED_V2_FIGURES
        + REQUIRED_V2_INPUTS
        + REQUIRED_V2_SCRIPTS
        + REQUIRED_PACKAGE_PROOFS
    )
    missing = [rel for rel in required if rel not in manifest_paths]
    for rel in missing:
        errors.append(f"required Paper A v2 package path missing from manifest: {rel}")


def _validate_required_text_artifacts_clean(package_dir: Path, manifest_paths: set[str], errors: list[str]) -> None:
    for rel in REQUIRED_TEXT_ARTIFACTS_WITHOUT_PENDING:
        if rel not in manifest_paths:
            continue
        path = package_dir / rel
        if not path.is_file() or not _is_text_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        pending_token = "V2-" + "PENDING"
        if pending_token in text:
            errors.append(f"required Paper A v2 text artifact contains unresolved v2 pending marker: {rel}")


def _validate_public_exclusions(paths: set[str], mode: str, errors: list[str]) -> None:
    if mode != "public":
        return
    for rel in sorted(paths):
        if any(rel.startswith(prefix) for prefix in PUBLIC_EXCLUDED_PREFIXES):
            errors.append(f"public package contains reviewer-only asset: {rel}")


def _validate_private_inclusions(paths: set[str], mode: str, errors: list[str]) -> None:
    if mode != "private":
        return
    for prefix in PRIVATE_REQUIRED_PREFIXES:
        if not any(rel.startswith(prefix) for rel in paths):
            errors.append(f"private package missing reviewer-only asset prefix: {prefix}")


def _validate_local_only_paths(paths: set[str], errors: list[str]) -> None:
    for rel in sorted(paths):
        posix = PurePosixPath(rel)
        if ".git" in posix.parts:
            errors.append(f"package contains git metadata path: {rel}")
        if posix.name in LOCAL_ONLY_FILENAMES:
            errors.append(f"package contains local-only file: {rel}")
        if rel.startswith(f"{LEGACY_PROJECT_NAME}/"):
            errors.append(f"package contains Paper B path: {rel}")


def _scan_placeholder_files(package_dir: Path, payload_paths: set[str], errors: list[str]) -> None:
    for rel in sorted(payload_paths):
        path = package_dir / rel
        name = PurePosixPath(rel).name
        if name == ".gitkeep":
            continue
        if PLACEHOLDER_NAME_RE.search(name):
            errors.append(f"package contains obvious placeholder filename: {rel}")
        if path.stat().st_size == 0:
            if name == "__init__.py":
                continue
            errors.append(f"package contains empty placeholder-like file: {rel}")
            continue
        if path.stat().st_size <= 512 and _is_text_candidate(path):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if PLACEHOLDER_CONTENT_RE.fullmatch(text):
                errors.append(f"package contains placeholder-only file content: {rel}")


def _scan_forbidden_content(package_dir: Path, payload_paths: set[str], mode: str, errors: list[str]) -> None:
    for rel in sorted(payload_paths):
        path = package_dir / rel
        if not path.is_file() or not _is_text_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, label in FORBIDDEN_CONTENT_PATTERNS:
            if pattern.search(text):
                errors.append(f"package file {rel} contains forbidden {label}")
        if mode == "public":
            if rel in PUBLIC_NOMENCLATURE_FILES:
                for pattern, label in PUBLIC_NOMENCLATURE_FORBIDDEN_PATTERNS:
                    if pattern.search(text):
                        errors.append(f"public-facing package file {rel} contains forbidden {label}")
            for prefix in PUBLIC_EXCLUDED_PREFIXES:
                if prefix in text:
                    errors.append(f"public package file {rel} contains reviewer-only asset reference: {prefix}")


def _extract_archive(archive_path: Path, tmp_dir: Path, package_name: str, errors: list[str]) -> Path | None:
    if not archive_path.is_file():
        errors.append(f"archive missing: {archive_path}")
        return None

    archive_errors: list[str] = []
    members: list[tarfile.TarInfo] = []
    names: set[str] = set()
    top_levels: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                rel = _clean_archive_name(member.name, archive_errors)
                if rel is None:
                    continue
                rel_name = rel.as_posix()
                if rel_name in names:
                    archive_errors.append(f"archive contains duplicate member: {rel_name}")
                    continue
                names.add(rel_name)
                top_levels.add(rel.parts[0])
                if not member.isdir() and not member.isfile():
                    archive_errors.append(f"archive contains unsupported member type: {rel_name}")
                members.append(member)
            if top_levels != {package_name}:
                archive_errors.append(f"archive top-level paths {sorted(top_levels)!r} do not match {package_name!r}")
            if archive_errors:
                errors.extend(archive_errors)
                return None
            archive.members = members
            for member in members:
                rel = PurePosixPath(member.name)
                target = tmp_dir.joinpath(*rel.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    errors.append(f"archive file member could not be read: {rel.as_posix()}")
                    continue
                with source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
    except (tarfile.TarError, OSError) as exc:
        errors.append(f"archive could not be read: {archive_path}: {exc}")
        return None

    package_dir = tmp_dir / package_name
    if not package_dir.is_dir():
        errors.append(f"archive did not extract package directory: {package_name}")
        return None
    return package_dir


def _clean_archive_name(raw_name: str, errors: list[str]) -> PurePosixPath | None:
    rel = _clean_package_relpath(raw_name, f"archive member {raw_name!r}", errors)
    if rel is None:
        return None
    return PurePosixPath(rel)


def _package_file_paths(package_dir: Path) -> set[str]:
    return {
        path.relative_to(package_dir).as_posix()
        for path in sorted(package_dir.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _validate_no_symlinks(package_dir: Path, errors: list[str]) -> None:
    for path in sorted(package_dir.rglob("*")):
        if path.is_symlink():
            errors.append(f"package contains symlink path: {path.relative_to(package_dir).as_posix()}")


def _load_json_object(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    if not path.is_file():
        errors.append(f"{label} missing: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} could not be parsed: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a JSON object")
        return {}
    return payload


def _parse_checksum_file(path: Path, label: str, errors: list[str]) -> dict[str, str]:
    if not path.is_file():
        errors.append(f"{label} missing: {path}")
        return {}
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"{label} could not be read: {exc}")
        return {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            errors.append(f"{label}:{line_number} must contain '<sha256>  <path>'")
            continue
        sha, raw_rel = parts
        rel = _clean_package_relpath(raw_rel.strip(), f"{label}:{line_number}", errors)
        if rel is None:
            continue
        if SHA256_RE.fullmatch(sha) is None:
            errors.append(f"{label}:{line_number} has invalid sha256 {sha!r}")
        if rel in entries:
            errors.append(f"{label}:{line_number} duplicates checksum path {rel}")
            continue
        entries[rel] = sha
    return entries


def _clean_package_relpath(raw_value: object, context: str, errors: list[str]) -> str | None:
    if not isinstance(raw_value, str) or not raw_value.strip():
        errors.append(f"{context} must be a non-empty relative path")
        return None
    raw = raw_value.strip()
    if "\\" in raw:
        errors.append(f"{context} uses backslashes: {raw!r}")
        return None
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts or "." in rel.parts:
        errors.append(f"{context} is not a safe relative path: {raw!r}")
        return None
    return rel.as_posix()


def _validate_manifest_mode(mode: str, label: str, errors: list[str]) -> None:
    if mode and mode not in VALID_MODES:
        errors.append(f"{label} mode is {mode!r}, expected one of {sorted(VALID_MODES)!r}")


def _validate_archive_name(raw_name: str, mode: str, context: str, errors: list[str]) -> str:
    cleaned = _clean_package_relpath(raw_name, context, errors)
    if cleaned is None:
        return raw_name
    rel = PurePosixPath(cleaned)
    if rel.name != cleaned:
        errors.append(f"{context} must be a basename inside dist: {raw_name!r}")
    expected = PRIVATE_ARCHIVE if mode == "private" else PUBLIC_ARCHIVE
    if mode in VALID_MODES and cleaned != expected:
        errors.append(f"{context} is {cleaned!r}, expected {expected!r} for mode {mode!r}")
    return cleaned


def _string_field(manifest: Mapping[str, Any], field: str, label: str, errors: list[str]) -> str | None:
    value = manifest.get(field)
    if not isinstance(value, str) or not value:
        errors.append(f"{label} field {field!r} must be a non-empty string")
        return None
    return value


def _verify_dist_checksum_entry(
    checksums: Mapping[str, str],
    checksum_name: str,
    path: Path,
    errors: list[str],
) -> None:
    expected_sha = checksums.get(checksum_name)
    if expected_sha is None:
        errors.append(f"dist CHECKSUMS.sha256 missing entry for {checksum_name}")
        return
    _verify_expected_sha256(path, expected_sha, f"dist CHECKSUMS.sha256 entry {checksum_name}", errors)


def _verify_expected_sha256(path: Path, expected_sha: str, label: str, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"{label} points to missing file: {path}")
        return
    actual_sha = _sha256_file(path)
    if actual_sha != expected_sha:
        errors.append(f"{label} sha256 mismatch: expected {expected_sha}, got {actual_sha}")


def _is_text_candidate(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        return b"\0" not in path.read_bytes()[:4096]
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raise_if_errors(errors: list[str]) -> None:
    if errors:
        raise PackageValidationError(errors)


if __name__ == "__main__":
    raise SystemExit(main())
