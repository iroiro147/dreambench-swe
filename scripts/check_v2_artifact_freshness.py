#!/usr/bin/env python3
"""Read-only freshness guard for final DreamBench-SWE v2 artifacts.

This guard is intentionally local-only. It does not run analyzers, package the
artifact, contact the VPS, or mutate dist. Its job is to catch two finalization
mistakes that are easy to make under time pressure:

* analyzer outputs older than the rsynced result/log roots for the run stamp;
* a dist package older than the final analyzer/package inputs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import validate_submission_package as validator  # noqa: E402


DEFAULT_RUN_STAMP_FILE = Path("analysis/fold/v2_confirmatory_run_stamp.txt")
DEFAULT_ANALYZER_OUTPUTS = validator.REQUIRED_V2_ANALYSIS_ARTIFACTS + validator.REQUIRED_V2_FIGURES
DEFAULT_PACKAGE_INPUTS = validator.REQUIRED_V2_INPUTS
DEFAULT_PACKAGE_PROOFS = validator.REQUIRED_PACKAGE_PROOFS
DEFAULT_PACKAGE_SCRIPTS = validator.REQUIRED_V2_SCRIPTS
DEFAULT_PACKAGE_SUPPORT_FILES = (
    ".artifactignore",
    "LICENSE",
    "README.md",
    "artifact/README.md",
    "scripts/package_artifact.py",
    "scripts/validate_submission_package.py",
)
DEFAULT_DIST_FILES = (
    validator.PUBLIC_ARCHIVE,
    "MANIFEST.json",
    "CHECKSUMS.sha256",
)


@dataclass(frozen=True)
class Check:
    ok: bool
    message: str


@dataclass(frozen=True)
class FreshnessReport:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failure_count(self) -> int:
        return sum(1 for check in self.checks if not check.ok)

    def lines(self) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        lines = ["DreamBench-SWE v2 artifact freshness audit"]
        lines.extend(f"{'PASS' if check.ok else 'FAIL'} {check.message}" for check in self.checks)
        lines.append(f"STATUS {status} failures={self.failure_count}")
        return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    report = audit_freshness(
        repo_root=repo_root,
        run_stamp_file=resolve_under(repo_root, Path(args.run_stamp_file)),
        run_stamp=args.run_stamp,
        results_root=Path(args.results_root).resolve() if args.results_root else None,
        log_root=Path(args.log_root).resolve() if args.log_root else None,
        dist_dir=resolve_under(repo_root, Path(args.dist_dir)),
        analyzer_outputs=tuple(resolve_under(repo_root, Path(path)) for path in args.analyzer_output),
        package_inputs=tuple(resolve_under(repo_root, Path(path)) for path in args.package_input),
        dist_files=tuple(args.dist_file),
        generated_at_skew_seconds=args.generated_at_skew_seconds,
    )
    print("\n".join(report.lines()))
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check local v2 analyzer and dist artifacts are fresh against rsynced roots."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument(
        "--run-stamp-file",
        default=str(DEFAULT_RUN_STAMP_FILE),
        help="run-stamp file relative to repo root unless absolute",
    )
    parser.add_argument("--run-stamp", default=None, help="expected run stamp; defaults to run-stamp file content")
    parser.add_argument(
        "--results-root",
        default=None,
        help="rsynced result root; defaults to DREAMBENCH_RESULTS_ROOT or experiments/results/v2-confirmatory-$RUN_STAMP",
    )
    parser.add_argument(
        "--log-root",
        default=None,
        help="rsynced log root; defaults to DREAMBENCH_LOG_ROOT or logs/grid/v2-confirmatory-$RUN_STAMP",
    )
    parser.add_argument("--dist-dir", default="dist", help="dist directory to freshness-check")
    parser.add_argument(
        "--analyzer-output",
        action="append",
        default=list(DEFAULT_ANALYZER_OUTPUTS),
        help="analyzer output to require and freshness-check; defaults to required Paper A v2 outputs",
    )
    parser.add_argument(
        "--package-input",
        action="append",
        default=list(
            DEFAULT_ANALYZER_OUTPUTS
            + DEFAULT_PACKAGE_INPUTS
            + DEFAULT_PACKAGE_PROOFS
            + DEFAULT_PACKAGE_SCRIPTS
            + DEFAULT_PACKAGE_SUPPORT_FILES
        ),
        help="package input that dist must be newer than; defaults to v2 outputs, proofs, scripts, and package policy files",
    )
    parser.add_argument(
        "--dist-file",
        action="append",
        default=list(DEFAULT_DIST_FILES),
        help="dist file basename to require; defaults to public archive, MANIFEST.json, CHECKSUMS.sha256",
    )
    parser.add_argument(
        "--generated-at-skew-seconds",
        type=float,
        default=1.0,
        help="allowed clock skew when comparing MANIFEST generated_at_utc to package inputs",
    )
    return parser.parse_args(argv)


def audit_freshness(
    *,
    repo_root: Path,
    run_stamp_file: Path,
    run_stamp: str | None,
    results_root: Path | None,
    log_root: Path | None,
    dist_dir: Path,
    analyzer_outputs: Sequence[Path],
    package_inputs: Sequence[Path],
    dist_files: Sequence[str],
    generated_at_skew_seconds: float = 1.0,
) -> FreshnessReport:
    checks: list[Check] = []
    observed_run_stamp = run_stamp or read_run_stamp(run_stamp_file)

    checks.append(check_run_stamp(run_stamp_file, observed_run_stamp))
    if run_stamp is not None and read_run_stamp(run_stamp_file) and read_run_stamp(run_stamp_file) != run_stamp:
        checks.append(Check(False, f"run stamp mismatch: {run_stamp_file} != {run_stamp}"))

    if observed_run_stamp:
        results_root = results_root or default_results_root(repo_root, observed_run_stamp)
        log_root = log_root or default_log_root(repo_root, observed_run_stamp)
    else:
        results_root = results_root or default_results_root(repo_root, "UNKNOWN")
        log_root = log_root or default_log_root(repo_root, "UNKNOWN")

    checks.extend(check_source_roots(results_root, log_root))
    source_files = collect_source_files(results_root, log_root)
    checks.append(check_source_files(source_files, results_root, log_root))
    checks.extend(check_files_exist(analyzer_outputs, "analyzer output"))
    checks.extend(check_analyzer_outputs_fresh(analyzer_outputs, source_files, repo_root))
    checks.extend(check_files_exist(package_inputs, "package input"))
    checks.extend(check_dist_freshness(dist_dir, dist_files, package_inputs, repo_root, generated_at_skew_seconds))
    return FreshnessReport(tuple(checks))


def resolve_under(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def read_run_stamp(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def default_results_root(repo_root: Path, run_stamp: str) -> Path:
    env = os.environ.get("DREAMBENCH_RESULTS_ROOT")
    return Path(env).resolve() if env else repo_root / "experiments" / "results" / f"v2-confirmatory-{run_stamp}"


def default_log_root(repo_root: Path, run_stamp: str) -> Path:
    env = os.environ.get("DREAMBENCH_LOG_ROOT")
    return Path(env).resolve() if env else repo_root / "logs" / "grid" / f"v2-confirmatory-{run_stamp}"


def check_run_stamp(run_stamp_file: Path, run_stamp: str) -> Check:
    if not run_stamp_file.is_file():
        return Check(False, f"run-stamp file missing: {run_stamp_file}")
    if not run_stamp:
        return Check(False, f"run-stamp file is empty: {run_stamp_file}")
    return Check(True, f"run stamp is {run_stamp} from {run_stamp_file}")


def check_source_roots(results_root: Path, log_root: Path) -> list[Check]:
    return [
        Check(results_root.is_dir(), f"results root {'exists' if results_root.is_dir() else 'missing'}: {results_root}"),
        Check(log_root.is_dir(), f"log root {'exists' if log_root.is_dir() else 'missing'}: {log_root}"),
    ]


def collect_source_files(results_root: Path, log_root: Path) -> list[Path]:
    files: list[Path] = []
    if results_root.is_dir():
        files.extend(path for path in results_root.glob("*/*/results.json") if path.is_file())
    if log_root.is_dir():
        files.extend(path for path in log_root.rglob("*") if path.is_file())
    return sorted(files)


def check_source_files(source_files: Sequence[Path], results_root: Path, log_root: Path) -> Check:
    if source_files:
        return Check(True, f"freshness source files scanned={len(source_files)} from {results_root} and {log_root}")
    return Check(False, f"no result/log source files found under {results_root} and {log_root}")


def check_files_exist(paths: Sequence[Path], label: str) -> list[Check]:
    checks: list[Check] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        checks.append(Check(path.is_file(), f"{label} {'exists' if path.is_file() else 'missing'}: {path}"))
    return checks


def check_analyzer_outputs_fresh(
    analyzer_outputs: Sequence[Path],
    source_files: Sequence[Path],
    repo_root: Path,
) -> list[Check]:
    if not source_files:
        return [Check(False, "cannot check analyzer freshness: no result/log source files found")]
    latest_source = latest_file(source_files)
    latest_source_mtime = latest_source.stat().st_mtime
    checks: list[Check] = []
    for path in unique_paths(analyzer_outputs):
        if not path.is_file():
            continue
        if path.stat().st_mtime < latest_source_mtime:
            checks.append(
                Check(
                    False,
                    f"analyzer output is stale: {display_path(repo_root, path)} older than {display_path(repo_root, latest_source)}",
                )
            )
        else:
            checks.append(Check(True, f"analyzer output is fresh: {display_path(repo_root, path)}"))
    return checks


def check_dist_freshness(
    dist_dir: Path,
    dist_files: Sequence[str],
    package_inputs: Sequence[Path],
    repo_root: Path,
    generated_at_skew_seconds: float,
) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check(dist_dir.is_dir(), f"dist dir {'exists' if dist_dir.is_dir() else 'missing'}: {dist_dir}"))
    required_dist_paths = [dist_dir / rel for rel in dist_files]
    checks.extend(check_files_exist(required_dist_paths, "dist file"))

    existing_inputs = [path for path in unique_paths(package_inputs) if path.is_file()]
    existing_dist = [path for path in required_dist_paths if path.is_file()]
    if not existing_inputs:
        checks.append(Check(False, "cannot check dist freshness: no package input files found"))
        return checks
    if not existing_dist:
        checks.append(Check(False, "cannot check dist freshness: no dist files found"))
        return checks

    latest_input = latest_file(existing_inputs)
    latest_input_mtime = latest_input.stat().st_mtime
    for path in existing_dist:
        if path.stat().st_mtime < latest_input_mtime:
            checks.append(
                Check(
                    False,
                    f"dist file is stale: {display_path(repo_root, path)} older than package input {display_path(repo_root, latest_input)}",
                )
            )
        else:
            checks.append(Check(True, f"dist file is fresh: {display_path(repo_root, path)}"))

    checks.append(check_manifest_generated_at(dist_dir / "MANIFEST.json", latest_input, repo_root, generated_at_skew_seconds))
    return checks


def check_manifest_generated_at(
    manifest_path: Path,
    latest_input: Path,
    repo_root: Path,
    generated_at_skew_seconds: float,
) -> Check:
    if not manifest_path.is_file():
        return Check(False, f"cannot check MANIFEST generated_at_utc: missing {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Check(False, f"cannot parse MANIFEST generated_at_utc: {manifest_path}: {exc}")
    generated_at = manifest.get("generated_at_utc")
    if not isinstance(generated_at, str) or not generated_at:
        return Check(False, f"MANIFEST.json missing generated_at_utc: {manifest_path}")
    try:
        generated_ts = parse_utc_timestamp(generated_at)
    except ValueError as exc:
        return Check(False, f"MANIFEST.json generated_at_utc is invalid: {generated_at!r}: {exc}")
    latest_input_ts = latest_input.stat().st_mtime
    if generated_ts + generated_at_skew_seconds < latest_input_ts:
        return Check(
            False,
            f"MANIFEST generated_at_utc is stale: {generated_at} older than package input {display_path(repo_root, latest_input)}",
        )
    return Check(True, f"MANIFEST generated_at_utc is fresh: {generated_at}")


def parse_utc_timestamp(value: str) -> float:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def latest_file(paths: Sequence[Path]) -> Path:
    return max(paths, key=lambda path: path.stat().st_mtime)


def unique_paths(paths: Sequence[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def display_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
