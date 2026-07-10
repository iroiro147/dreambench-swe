#!/usr/bin/env python3
"""Build the DreamBench-SWE artifact package.

The package is explicit-include driven and staged through a scrubber before
archiving. Public mode excludes hidden oracle/reference-solution assets;
private mode adds those assets for reviewer-only distribution.
"""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "dist"
PACKAGE_NAME = "dreambench-swe-artifact"
PUBLIC_ARCHIVE = "dreambench-swe-artifact.tar.gz"
PRIVATE_ARCHIVE = "dreambench-swe-artifact-reviewer-private.tar.gz"

TEXT_EXTENSIONS = {
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

HIDDEN_ORACLE_PREFIX = "experiments/env/" + "oracles/"
HIDDEN_REFSOL_PREFIX = "experiments/env/" + "refsol/"
HIDDEN_ORACLE_DIR = HIDDEN_ORACLE_PREFIX.rstrip("/")
HIDDEN_REFSOL_DIR = HIDDEN_REFSOL_PREFIX.rstrip("/")

PUBLIC_ONLY_EXCLUDES = (
    HIDDEN_ORACLE_PREFIX,
    HIDDEN_REFSOL_PREFIX,
)

PUBLIC_REFERENCE_REPLACEMENTS = (
    (HIDDEN_ORACLE_PREFIX, "<REVIEWER_ONLY_ORACLES>/"),
    (HIDDEN_REFSOL_PREFIX, "<REVIEWER_ONLY_REFSOL>/"),
)

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?m)^\s*[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|OAUTH_TOKEN|PASSWORD|SECRET)[A-Z0-9_]*\s*=\s*['\"]?[A-Za-z0-9_./+=:-]{12,}"),
    re.compile(r"(?i)['\"](?:api_key|access_token|oauth_token|password|secret)['\"]\s*:\s*['\"][A-Za-z0-9_./+=:-]{12,}['\"]"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

SECRET_REPLACEMENTS = (
    (re.compile(r"\bsarthak\.singh@[A-Za-z0-9.-]+\b", re.IGNORECASE), "<AUTHOR_EMAIL>"),
    (re.compile(r"\bdesign\.purchase@[A-Za-z0-9.-]+\b", re.IGNORECASE), "<OPERATOR_EMAIL>"),
    (re.compile(r"\bsarthak\.singh\b", re.IGNORECASE), "<LOCAL_USER>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_.=-]+"), "Bearer <REDACTED>"),
    (
        re.compile(r"(?m)^(\s*[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|OAUTH_TOKEN|PASSWORD|SECRET)[A-Z0-9_]*\s*=\s*)['\"]?[A-Za-z0-9_./+=:-]{12,}"),
        r"\1=<REDACTED>",
    ),
    (
        re.compile(r"(?i)(['\"](?:api_key|access_token|oauth_token|password|secret)['\"]\s*:\s*['\"])[A-Za-z0-9_./+=:-]{12,}(['\"])"),
        r"\1<REDACTED>\2",
    ),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "sk-<REDACTED>"),
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "ghp_<REDACTED>"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_<REDACTED>"),
)

USERS_ROOT = "/" + "Users"
LEGACY_PROJECT_NAME = "paper-" + "dreaming-agents"
LOCAL_PROJECT_NAME = "dreamforge-" + "3seed"

LOCAL_PATH_REPLACEMENTS = (
    (
        re.compile(
            r"/root/(?:"
            + re.escape(LEGACY_PROJECT_NAME)
            + r"|"
            + re.escape(LOCAL_PROJECT_NAME)
            + r"(?:-codex-gate)?)(?=/|\b)"
        ),
        "<LOCAL_ROOT>",
    ),
    (re.compile(USERS_ROOT + r"/[^/]+/Desktop/projects/[^/]+(?=/|\b)"), "<LOCAL_ROOT>"),
    (re.compile(USERS_ROOT + r"/[^/\"'\s]+/"), "<USER_HOME>/"),
    (re.compile(r"/private/var/folders/[^/\"'\s]+/[^/\"'\s]+/T/"), "<TMPDIR>/"),
    (re.compile(r"/var/folders/[^/\"'\s]+/[^/\"'\s]+/T/"), "<TMPDIR>/"),
    (re.compile(r"<TMPDIR>/"), "<TMPDIR>/"),
    (re.compile(r"(?:<USER_HOME>|~)/\.codex/[^\"'\s]+"), "<CODEX_HOME>"),
    (re.compile(re.escape(LEGACY_PROJECT_NAME)), "<LOCAL_PROJECT>"),
    (re.compile(re.escape(LOCAL_PROJECT_NAME) + r"(?:-codex-gate)?"), "<LOCAL_PROJECT>"),
)

INCLUDE_FILES = (
    ".artifactignore",
    "LICENSE",
    "README.md",
    "Makefile",
    "pytest.ini",
    "artifact/README.md",
    "docs/DATASHEET.md",
    "docs/trap_skeleton_spec.md",
    "experiments/README.md",
    "experiments/validate_trap.py",
    "experiments/env/README.md",
    "experiments/env/build_env.py",
    "experiments/env/load_env.py",
    "experiments/env/sequences.jsonl",
    "experiments/env/sequences_confirmatory_v2.jsonl",
    "experiments/env/sequences_synth.jsonl",
    "experiments/env/tasks.jsonl",
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
    "paper/figures/v2_construct_coverage.pdf",
    "paper/figures/v2_ladder.pdf",
    "paper/figures/v2_verbatim_vs_synthesis.pdf",
    "analysis/fold/final_tables.json",
    "analysis/fold/final_tables.tex",
    "analysis/fold/stats.json",
    "analysis/fold/stats.tex",
    "analysis/fold/HERMETICITY-MANIFEST.md",
    "analysis/fold/CANARY-PROOF.txt",
    "analysis/fold/REPRO-MANIFEST.md",
    "analysis/investigation-evidence/CONFIRMATORY-FOLD.md",
    "analysis/investigation-evidence/CLUSTERED-STATS.md",
    "analysis/investigation-evidence/CONFIRMATORY-COST.md",
    "analysis/investigation-evidence/PREREGISTRATION.md",
    "analysis/investigation-evidence/PREREGISTRATION-V2.md",
    "analysis/investigation-evidence/V2-ANALYSIS-20260708T065236Z.md",
    "analysis/investigation-evidence/FABLE-CONSTRUCTS.md",
    "analysis/investigation-evidence/AUTHORING-WORKLIST.md",
    "analysis/investigation-evidence/SCALE1-FREEZE-DECISIONS.md",
    "analysis/investigation-evidence/XMODEL-glm.md",
    "analysis/investigation-evidence/MEM0-ROW-FOLD-REPORT.md",
)

INCLUDE_DIRS = (
    "src",
    "tests",
    "ops",
    "experiments/data/tasks",
    "experiments/fixtures",
)

SCRIPT_FILES = (
    "scripts/package_artifact.py",
    "scripts/package_arxiv_source.py",
    "scripts/generate_tables.py",
    "scripts/construct_validity.py",
    "scripts/clustered_sensitivity.py",
    "scripts/run_smoke.py",
    "scripts/stats_analysis.py",
    "scripts/analyze_confirmatory.py",
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
    "scripts/analyze_results.py",
    "scripts/barrier2_rescore.py",
    "scripts/check_synth_no_single_event.py",
    "scripts/cost_analysis.py",
    "scripts/cross_model_rescore.py",
    "scripts/emit_provenance.py",
    "scripts/fold_results.py",
    "scripts/run_experiment.py",
    "scripts/run_grid.py",
    "scripts/vps_confirmatory_status.py",
    "scripts/batch_validate.py",
    "scripts/inject_secrets.py",
    "scripts/Dockerfile.codex-agent",
    "scripts/Dockerfile.api-agent",
)

PRIVATE_INCLUDE_DIRS = (
    HIDDEN_ORACLE_DIR,
    HIDDEN_REFSOL_DIR,
)

REQUIRED_EXPLICIT_FILES = {
    "LICENSE",
    "README.md",
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
    "paper/figures/v2_construct_coverage.pdf",
    "paper/figures/v2_ladder.pdf",
    "paper/figures/v2_verbatim_vs_synthesis.pdf",
    "analysis/fold/HERMETICITY-MANIFEST.md",
    "analysis/fold/CANARY-PROOF.txt",
    "analysis/fold/REPRO-MANIFEST.md",
    "docs/DATASHEET.md",
    "docs/trap_skeleton_spec.md",
    "experiments/env/sequences_confirmatory_v2.jsonl",
    "analysis/investigation-evidence/PREREGISTRATION.md",
    "analysis/investigation-evidence/PREREGISTRATION-V2.md",
    "analysis/investigation-evidence/V2-ANALYSIS-20260708T065236Z.md",
    "analysis/investigation-evidence/FABLE-CONSTRUCTS.md",
    "analysis/investigation-evidence/AUTHORING-WORKLIST.md",
    "analysis/investigation-evidence/SCALE1-FREEZE-DECISIONS.md",
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
    "scripts/batch_validate.py",
    "scripts/inject_secrets.py",
    "scripts/Dockerfile.api-agent",
    "scripts/package_arxiv_source.py",
    "scripts/vps_confirmatory_status.py",
}

FORBIDDEN_CONTENT_PATTERNS = (
    (re.compile(re.escape(LEGACY_PROJECT_NAME)), "private project name"),
    (re.compile(re.escape(LOCAL_PROJECT_NAME)), "private project name"),
    (re.compile(r"/root/" + re.escape(LEGACY_PROJECT_NAME)), "private VPS project root"),
    (re.compile(r"/root/" + re.escape(LOCAL_PROJECT_NAME)), "private VPS project root"),
    (re.compile(USERS_ROOT + r"/"), "local user path"),
    (re.compile(r"\bsarthak\.singh\b", re.IGNORECASE), "local user identity"),
    (re.compile(r"\b" + "jus" + "pay" + r"\b", re.IGNORECASE), "employer-identifying text"),
    (re.compile(r"\bdesign\.purchase\b", re.IGNORECASE), "operator account identity"),
    (re.compile(r"\.codex/" + "plugins", re.IGNORECASE), "local Codex plugin path"),
    (re.compile("TEAM_" + "PROTOCOL", re.IGNORECASE), "local team protocol text"),
)


@dataclass(frozen=True)
class StagedFile:
    path: str
    source: str
    size: int
    sha256: str
    role: str
    scrubbed: bool


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_package(private=args.private, dist_dir=args.dist_dir)
    except RuntimeError as exc:
        print("FAIL artifact package build failed")
        print(f"- {exc}")
        return 1
    print(f"mode={result['mode']}")
    print(f"archive={result['archive']}")
    print(f"archive_sha256={result['archive_sha256']}")
    print(f"manifest={result['manifest']}")
    print(f"checksums={result['checksums']}")
    print(f"files={result['file_count']}")
    print(f"scrubbed_files={result['scrubbed_file_count']}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the DreamBench-SWE artifact tarball.")
    parser.add_argument("--private", action="store_true", help="include reviewer-only oracles/refsol assets")
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST, help="output directory")
    return parser.parse_args(argv)


def build_package(*, private: bool, dist_dir: Path) -> dict[str, object]:
    mode = "private" if private else "public"
    archive_name = PRIVATE_ARCHIVE if private else PUBLIC_ARCHIVE
    dist_dir.mkdir(parents=True, exist_ok=True)

    ignore_patterns = load_artifactignore(ROOT / ".artifactignore")
    with tempfile.TemporaryDirectory(prefix="dreambench-artifact-") as tmp:
        tmp_path = Path(tmp)
        package_root = tmp_path / PACKAGE_NAME
        package_root.mkdir()

        copy_package_files(package_root, ignore_patterns=ignore_patterns, private=private)
        staged_files = collect_staged_files(package_root, private=private)
        manifest = build_manifest(mode=mode, archive_name=archive_name, staged_files=staged_files)
        manifest_path = package_root / "MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        content_checksums = write_package_checksums(package_root)
        verify_staged_package(package_root, private=private)

        archive_path = dist_dir / archive_name
        create_tarball(package_root, archive_path)

        dist_manifest_path = dist_dir / "MANIFEST.json"
        dist_manifest_path.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
        dist_checksums_path = dist_dir / "CHECKSUMS.sha256"
        archive_sha = sha256_file(archive_path)
        dist_checksums_path.write_text(
            "\n".join(
                [
                    f"{archive_sha}  {archive_name}",
                    f"{sha256_file(dist_manifest_path)}  MANIFEST.json",
                    f"{content_checksums['sha256']}  {PACKAGE_NAME}/CHECKSUMS.sha256",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    return {
        "mode": mode,
        "archive": display_path(archive_path),
        "archive_sha256": archive_sha,
        "manifest": display_path(dist_manifest_path),
        "checksums": display_path(dist_checksums_path),
        "file_count": len(staged_files),
        "scrubbed_file_count": sum(1 for item in staged_files if item.scrubbed),
    }


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_artifactignore(path: Path) -> list[str]:
    patterns: list[str] = []
    if not path.exists():
        return patterns
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def copy_package_files(package_root: Path, *, ignore_patterns: list[str], private: bool) -> None:
    rel_paths: set[str] = set()
    for rel in INCLUDE_FILES + SCRIPT_FILES:
        add_file(
            rel_paths,
            rel,
            private=private,
            ignore_patterns=ignore_patterns,
            required=rel in REQUIRED_EXPLICIT_FILES,
        )
    for rel_dir in INCLUDE_DIRS:
        add_dir(rel_paths, rel_dir, private=private, ignore_patterns=ignore_patterns)
    if private:
        for rel_dir in PRIVATE_INCLUDE_DIRS:
            add_dir(rel_paths, rel_dir, private=private, ignore_patterns=ignore_patterns)

    for rel in sorted(rel_paths):
        src = ROOT / rel
        if src.is_symlink():
            raise RuntimeError(f"refusing to package symlink source file: {rel}")
        dst = package_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_text_candidate(src):
            text = src.read_text(encoding="utf-8", errors="surrogateescape")
            scrubbed = (
                scrub_json_text(text, rel, private=private)
                if src.suffix in {".json", ".jsonl"}
                else scrub_text(text, rel, private=private)
            )
            dst.write_text(scrubbed, encoding="utf-8")
        else:
            shutil.copy2(src, dst)
            os.utime(dst, (0, 0))


def add_file(
    rel_paths: set[str],
    rel: str,
    *,
    private: bool,
    ignore_patterns: list[str],
    required: bool = False,
) -> None:
    path = ROOT / rel
    if path.is_symlink():
        raise RuntimeError(f"refusing to package symlink source file: {rel}")
    if not path.is_file():
        if required:
            raise RuntimeError(f"required package file is missing: {rel}")
        return
    if should_exclude(rel, private=private, ignore_patterns=ignore_patterns):
        if required:
            raise RuntimeError(f"required package file is excluded: {rel}")
        return
    rel_paths.add(rel)


def add_dir(rel_paths: set[str], rel_dir: str, *, private: bool, ignore_patterns: list[str]) -> None:
    root = ROOT / rel_dir
    if root.is_symlink():
        raise RuntimeError(f"refusing to package symlink source directory: {rel_dir}")
    if not root.exists():
        return
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"refusing to package symlink source file: {rel}")
        if should_exclude(rel, private=private, ignore_patterns=ignore_patterns):
            continue
        rel_paths.add(rel)


def should_exclude(rel: str, *, private: bool, ignore_patterns: list[str]) -> bool:
    normalized = rel.strip("/")
    if not private and any(normalized == item.rstrip("/") or normalized.startswith(item) for item in PUBLIC_ONLY_EXCLUDES):
        return True
    if "/.git/" in f"/{normalized}/" or normalized.startswith(".git/"):
        return True
    for pattern in ignore_patterns:
        if private and pattern.rstrip("/") in {HIDDEN_ORACLE_DIR, HIDDEN_REFSOL_DIR}:
            continue
        if matches_pattern(normalized, pattern):
            return True
    return False


def matches_pattern(rel: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if not pattern:
        return False
    if pattern.endswith("/"):
        prefix = pattern.rstrip("/") + "/"
        return rel == pattern.rstrip("/") or rel.startswith(prefix)
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/") + "/"
        return rel.startswith(prefix)
    return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern)


def is_text_candidate(path: Path) -> bool:
    if path.suffix in TEXT_EXTENSIONS:
        return True
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\0" not in chunk


def scrub_text(text: str, rel: str, *, private: bool = False) -> str:
    for pattern, repl in LOCAL_PATH_REPLACEMENTS:
        text = pattern.sub(repl, text)
    for pattern, repl in SECRET_REPLACEMENTS:
        text = pattern.sub(repl, text)
    if not private:
        for prefix, replacement in PUBLIC_REFERENCE_REPLACEMENTS:
            text = text.replace(prefix, replacement)
    return text


def scrub_json_text(text: str, rel: str, *, private: bool = False) -> str:
    if not text.strip():
        return text
    if rel.endswith(".jsonl"):
        lines: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            lines.append(json.dumps(scrub_json_value(payload, rel, private=private), sort_keys=True))
        return "\n".join(lines) + "\n"
    payload = json.loads(text)
    return json.dumps(scrub_json_value(payload, rel, private=private), indent=2, sort_keys=True) + "\n"


def scrub_json_value(value: object, rel: str, *, private: bool = False) -> object:
    if isinstance(value, str):
        return scrub_text(value, rel, private=private)
    if isinstance(value, list):
        return [scrub_json_value(item, rel, private=private) for item in value]
    if isinstance(value, dict):
        return {str(key): scrub_json_value(item, rel, private=private) for key, item in value.items()}
    return value


def collect_staged_files(package_root: Path, *, private: bool) -> list[StagedFile]:
    files: list[StagedFile] = []
    for path in sorted(item for item in package_root.rglob("*") if item.is_file()):
        rel = path.relative_to(package_root).as_posix()
        if rel in {"MANIFEST.json", "CHECKSUMS.sha256"}:
            continue
        source = "generated" if rel in {"MANIFEST.json", "CHECKSUMS.sha256"} else rel
        files.append(
            StagedFile(
                path=rel,
                source=source,
                size=path.stat().st_size,
                sha256=sha256_file(path),
                role=role_for_path(rel, private=private),
                scrubbed=was_scrubbed(ROOT / rel, path),
            )
        )
    return files


def was_scrubbed(source: Path, staged: Path) -> bool:
    if not source.exists() or not source.is_file() or not is_text_candidate(source):
        return False
    try:
        original = source.read_text(encoding="utf-8", errors="surrogateescape")
        packaged = staged.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return original != packaged


def role_for_path(rel: str, *, private: bool) -> str:
    if rel.startswith("ops/"):
        return "live-rerun-provenance"
    if rel.startswith(HIDDEN_ORACLE_PREFIX) or rel.startswith(HIDDEN_REFSOL_PREFIX):
        return "reviewer-only-hidden-scoring-asset" if private else "excluded"
    if rel.startswith("analysis/fold/") or rel.startswith("analysis/investigation-evidence/"):
        return "frozen-analysis-artifact"
    if rel.startswith("paper/figures/v2_"):
        return "frozen-analysis-artifact"
    if rel.startswith("scripts/"):
        return "analysis-or-smoke-script"
    if rel.startswith("src/"):
        return "source"
    if rel.startswith("tests/"):
        return "test"
    if rel.startswith("experiments/data/tasks/") or rel.startswith("experiments/env/"):
        return "public-fixture-or-sequence-metadata"
    if rel == "artifact/README.md":
        return "artifact-runbook"
    return "support"


def build_manifest(*, mode: str, archive_name: str, staged_files: list[StagedFile]) -> dict[str, object]:
    generated_at = package_source_timestamp(staged_files)
    return {
        "package": PACKAGE_NAME,
        "mode": mode,
        "archive": archive_name,
        "generated_at_utc": generated_at,
        "anonymous": False,
        "raw_result_records_included": False,
        "exact_frozen_record_replay_supported": False,
        "live_rerun_is_new_experiment": True,
        "public_exclusions": [
            ".git and nested .git directories",
            "local agent instructions and day/night state files",
            "logs, bins, handoff material, and credentials",
            HIDDEN_ORACLE_DIR + " and " + HIDDEN_REFSOL_DIR + " in public mode",
            "full raw hosted-model result directories",
        ],
        "files": [item.__dict__ for item in staged_files],
    }


def package_source_timestamp(staged_files: list[StagedFile]) -> str:
    """Return a stable release timestamp derived from the newest packaged input."""
    mtimes: list[float] = []
    for item in staged_files:
        source = ROOT / item.source
        if source.is_file():
            mtimes.append(source.stat().st_mtime)
    latest = max(mtimes) if mtimes else 0
    return datetime.fromtimestamp(latest, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_package_checksums(package_root: Path) -> dict[str, str]:
    lines: list[str] = []
    for path in sorted(item for item in package_root.rglob("*") if item.is_file()):
        rel = path.relative_to(package_root).as_posix()
        if rel == "CHECKSUMS.sha256":
            continue
        lines.append(f"{sha256_file(path)}  {rel}")
    checksum_path = package_root / "CHECKSUMS.sha256"
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"sha256": sha256_file(checksum_path)}


def verify_staged_package(package_root: Path, *, private: bool) -> None:
    path_errors: list[str] = []
    if not private:
        for rel in PUBLIC_ONLY_EXCLUDES:
            if (package_root / rel).exists():
                path_errors.append(f"public package contains excluded path: {rel}")
    for forbidden in ("AGENTS.md", "CLAUDE.md", "DAY_STATE.md", "NIGHT_STATE.md", "MEMORY.md"):
        if (package_root / forbidden).exists():
            path_errors.append(f"package contains local-only file: {forbidden}")
    for path in package_root.rglob("*"):
        rel = path.relative_to(package_root).as_posix()
        if path.is_symlink():
            path_errors.append(f"package contains symlink: {rel}")
        if ".git/" in f"{rel}/" or rel == ".git":
            path_errors.append(f"package contains git metadata: {rel}")
    content_errors = scan_staged_content(package_root, private=private)
    errors = path_errors + content_errors
    if errors:
        preview = "\n".join(f"- {item}" for item in errors[:30])
        raise RuntimeError(f"artifact verification failed:\n{preview}")


def scan_staged_content(package_root: Path, *, private: bool) -> list[str]:
    errors: list[str] = []
    for path in sorted(item for item in package_root.rglob("*") if item.is_file()):
        if not is_text_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(package_root).as_posix()
        for pattern, label in FORBIDDEN_CONTENT_PATTERNS:
            if pattern.search(text):
                errors.append(f"{rel} contains forbidden {label}")
        if not private:
            for prefix in PUBLIC_ONLY_EXCLUDES:
                if prefix in text:
                    errors.append(f"{rel} contains public-excluded asset reference: {prefix}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"{rel} matches secret pattern {pattern.pattern}")
                break
    return errors


def create_tarball(package_root: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for path in sorted([package_root, *package_root.rglob("*")]):
                    arcname = f"{PACKAGE_NAME}/{path.relative_to(package_root).as_posix()}" if path != package_root else PACKAGE_NAME
                    info = tar.gettarinfo(str(path), arcname=arcname)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    if path.is_file():
                        with path.open("rb") as handle:
                            tar.addfile(info, fileobj=handle)
                    else:
                        tar.addfile(info)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
