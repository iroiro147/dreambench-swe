#!/usr/bin/env python3
"""Local completion auditor for the DreamBench-SWE v2 confirmatory fold.

This script is intentionally read-only.  It is for the post-rsync gate before
canonical analyzers run: count condition-level result files, verify driver
completion markers, reject failed grid finishes, verify completion dry plans,
and optionally guard against stale analyzer outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_ROOT = REPO_ROOT / "experiments" / "results"
DEFAULT_LOG_ROOT = REPO_ROOT / "logs" / "grid"
EXPECTED_RESULTS_COUNT = 1890
GRID_DONE_RE = re.compile(r"\b(?P<marker>[A-Z0-9_-]*GRID_DONE)\s+rc=(?P<rc>-?\d+)\b")
FINISH_FAILED_RE = re.compile(r"\bFINISH\b.*\brc=([1-9]\d*)\b")


@dataclass(frozen=True)
class Check:
    ok: bool
    message: str


@dataclass(frozen=True)
class AuditReport:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failure_count(self) -> int:
        return sum(1 for check in self.checks if not check.ok)

    def lines(self) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        lines = ["DreamBench-SWE v2 confirmatory completion audit"]
        lines.extend(f"{'PASS' if check.ok else 'FAIL'} {check.message}" for check in self.checks)
        lines.append(f"STATUS {status} failures={self.failure_count}")
        return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_completion(
        results_root=Path(args.results_root),
        log_root=Path(args.log_root),
        expected_results_count=args.expected_results_count,
        sequence_records=Path(args.sequence_records) if args.sequence_records else None,
        expected_sequence_sha256=args.expected_sequence_sha256,
        dry_plan_paths=tuple(Path(path) for path in args.dry_plan),
        analyzer_output_paths=tuple(Path(path) for path in args.analyzer_output),
    )
    print("\n".join(report.lines()))
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit local v2 confirmatory fold completion after rsync."
    )
    parser.add_argument(
        "--results-root",
        default=str(Path(os.environ.get("DREAMBENCH_RESULTS_ROOT") or DEFAULT_RESULTS_ROOT)),
        help="Local rsynced result root. Counts only results_root/*/*/results.json.",
    )
    parser.add_argument(
        "--log-root",
        default=str(Path(os.environ.get("DREAMBENCH_LOG_ROOT") or DEFAULT_LOG_ROOT)),
        help="Local rsynced grid log root containing driver logs and RUN_GRID.log.",
    )
    parser.add_argument(
        "--expected-results-count",
        type=positive_int,
        default=EXPECTED_RESULTS_COUNT,
        help=f"Expected count for results_root/*/*/results.json (default: {EXPECTED_RESULTS_COUNT}).",
    )
    parser.add_argument(
        "--sequence-records",
        help="Local confirmatory sequence JSONL. When provided, pair with --expected-sequence-sha256.",
    )
    parser.add_argument(
        "--expected-sequence-sha256",
        help="Expected SHA256 for --sequence-records. Fails if the local source does not match.",
    )
    parser.add_argument(
        "--dry-plan",
        action="append",
        default=[],
        help="Completion dry-plan path. May be repeated; every file must have zero lines beginning 'RUN '.",
    )
    parser.add_argument(
        "--analyzer-output",
        action="append",
        default=[],
        help="Analyzer output path. May be repeated; every file must be newer than all result/log files.",
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected positive integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected positive integer, got {value!r}")
    return parsed


def audit_completion(
    *,
    results_root: Path,
    log_root: Path,
    expected_results_count: int = EXPECTED_RESULTS_COUNT,
    sequence_records: Path | None = None,
    expected_sequence_sha256: str | None = None,
    dry_plan_paths: Sequence[Path] = (),
    analyzer_output_paths: Sequence[Path] = (),
) -> AuditReport:
    checks: list[Check] = []
    result_files = count_result_files(results_root)
    log_files = list_log_files(log_root)

    checks.append(check_results_root(results_root))
    checks.append(check_log_root(log_root))
    checks.extend(check_sequence_sha256(sequence_records, expected_sequence_sha256))
    checks.append(check_result_count(result_files, expected_results_count, results_root))
    checks.extend(check_required_driver_markers(log_root))
    checks.append(check_grid_done_return_codes(log_files))
    checks.append(check_run_grid_finishes(log_root / "RUN_GRID.log"))
    checks.extend(check_dry_plans(dry_plan_paths))
    checks.extend(check_analyzer_freshness(analyzer_output_paths, tuple(result_files) + tuple(log_files)))
    return AuditReport(tuple(checks))


def count_result_files(results_root: Path) -> list[Path]:
    if not results_root.is_dir():
        return []
    return sorted(path for path in results_root.glob("*/*/results.json") if path.is_file())


def list_log_files(log_root: Path) -> list[Path]:
    if not log_root.is_dir():
        return []
    return sorted(path for path in log_root.rglob("*") if path.is_file())


def check_results_root(results_root: Path) -> Check:
    if results_root.is_dir():
        return Check(True, f"results root exists: {results_root}")
    return Check(False, f"results root missing or not a directory: {results_root}")


def check_log_root(log_root: Path) -> Check:
    if log_root.is_dir():
        return Check(True, f"log root exists: {log_root}")
    return Check(False, f"log root missing or not a directory: {log_root}")


def check_result_count(result_files: Sequence[Path], expected_count: int, results_root: Path) -> Check:
    actual = len(result_files)
    if actual == expected_count:
        return Check(
            True,
            f"condition-level results count is {actual} using {results_root}/*/*/results.json",
        )
    return Check(
        False,
        f"condition-level results count is {actual}, expected {expected_count}, using {results_root}/*/*/results.json",
    )


def check_sequence_sha256(sequence_records: Path | None, expected_sha256: str | None) -> list[Check]:
    if sequence_records is None and expected_sha256 is None:
        return []
    if sequence_records is None or expected_sha256 is None:
        return [
            Check(
                False,
                "sequence hash check requires both --sequence-records and --expected-sequence-sha256",
            )
        ]
    if not sequence_records.is_file():
        return [Check(False, f"missing sequence records: {sequence_records}")]

    expected = expected_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return [Check(False, f"expected sequence SHA256 is not a 64-character hex digest: {expected_sha256}")]

    actual = sha256_file(sequence_records)
    if actual == expected:
        return [Check(True, f"sequence records sha256 matches expected: {sequence_records} sha256={actual}")]
    return [
        Check(
            False,
            f"sequence records sha256 mismatch: {sequence_records} actual={actual} expected={expected}",
        )
    ]


def check_required_driver_markers(log_root: Path) -> list[Check]:
    required = (
        ("NONLIVE.driver.log", "NONLIVE_GRID_DONE rc=0"),
        ("LIVE.driver.log", "LIVE_GRID_DONE rc=0"),
    )
    checks: list[Check] = []
    for filename, marker in required:
        path = log_root / filename
        if not path.is_file():
            checks.append(Check(False, f"missing required driver log: {path}"))
            continue
        text = read_text(path)
        if marker in text:
            checks.append(Check(True, f"{filename} contains {marker}"))
        else:
            checks.append(Check(False, f"{filename} does not contain {marker}"))
    return checks


def check_grid_done_return_codes(log_files: Sequence[Path]) -> Check:
    bad_markers: list[str] = []
    marker_count = 0
    for path in log_files:
        for line_number, line in enumerate(read_lines(path), start=1):
            for match in GRID_DONE_RE.finditer(line):
                marker_count += 1
                rc = int(match.group("rc"))
                if rc != 0:
                    bad_markers.append(
                        f"{path}:{line_number}: {match.group('marker')} rc={rc}"
                    )
    if bad_markers:
        return Check(False, "nonzero GRID_DONE marker(s): " + "; ".join(bad_markers))
    return Check(True, f"GRID_DONE markers scanned={marker_count}; no nonzero rc found")


def check_run_grid_finishes(run_grid_log: Path) -> Check:
    if not run_grid_log.is_file():
        return Check(False, f"missing RUN_GRID log: {run_grid_log}")
    failures = [
        f"{run_grid_log}:{line_number}: {line.strip()}"
        for line_number, line in enumerate(read_lines(run_grid_log), start=1)
        if FINISH_FAILED_RE.search(line)
    ]
    if failures:
        return Check(False, "RUN_GRID FINISH failure line(s): " + "; ".join(failures))
    return Check(True, f"RUN_GRID FINISH lines have no rc=[1-9]: {run_grid_log}")


def check_dry_plans(dry_plan_paths: Sequence[Path]) -> list[Check]:
    checks: list[Check] = []
    for path in dry_plan_paths:
        if not path.is_file():
            checks.append(Check(False, f"missing dry-plan file: {path}"))
            continue
        run_lines = [
            line_number
            for line_number, line in enumerate(read_lines(path), start=1)
            if line.startswith("RUN ")
        ]
        if run_lines:
            preview = ",".join(str(line_number) for line_number in run_lines[:10])
            checks.append(Check(False, f"dry-plan has {len(run_lines)} RUN line(s): {path} lines={preview}"))
        else:
            checks.append(Check(True, f"dry-plan has zero RUN lines: {path}"))
    return checks


def check_analyzer_freshness(
    analyzer_output_paths: Sequence[Path],
    source_paths: Sequence[Path],
) -> list[Check]:
    if not analyzer_output_paths:
        return []
    source_files = [path for path in source_paths if path.is_file()]
    if not source_files:
        return [Check(False, "cannot check analyzer freshness: no result/log files found")]

    latest_source = max(source_files, key=lambda path: path.stat().st_mtime)
    latest_mtime = latest_source.stat().st_mtime
    checks: list[Check] = []
    for path in analyzer_output_paths:
        if not path.is_file():
            checks.append(Check(False, f"missing analyzer output: {path}"))
            continue
        output_mtime = path.stat().st_mtime
        if output_mtime < latest_mtime:
            checks.append(
                Check(
                    False,
                    f"analyzer output is stale: {path} is older than latest result/log file {latest_source}",
                )
            )
        else:
            checks.append(Check(True, f"analyzer output is fresh: {path}"))
    return checks


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
