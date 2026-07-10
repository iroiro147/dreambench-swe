#!/usr/bin/env python3
"""Read-only preflight for the Paper A v2 post-fold gate.

This script is intentionally not an orchestrator.  By default it does not
contact the VPS, rsync results, run completion dry plans, run analyzers, edit
the manuscript, or package artifacts.  Its job is to fail early on local
preconditions that make the binding post-fold checklist unsafe to execute, and
then print the exact next command deck from the hardened runbook.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_RUN_STAMP = "20260706T074759Z"
EXPECTED_VPS_HEAD = "3ac349e873b1f9d03a25502629dc43083b8808ee"
EXPECTED_CONFIRMATORY_SHA256 = "4966bad1e535aaa0165c8aa7f2cb6fefd1d4e4afca78accfd868591a40448743"
EXPECTED_RESULTS_COUNT = 1890
DEFAULT_REMOTE = "root@157.90.114.41"
DEFAULT_REMOTE_ROOT = "<LOCAL_ROOT>"
DEFAULT_RUN_STAMP_FILE = Path("analysis/fold/v2_confirmatory_run_stamp.txt")
DEFAULT_SEQUENCE_RECORDS = Path("experiments/env/sequences_confirmatory_v2.jsonl")
STALE_OUTPUT_ARCHIVE_DIR = Path("analysis/fold/archive-pre-v2-confirmatory-20260706T074759Z")

NONLIVE_CONDITIONS = (
    "B0,B1,B2,B3,B4,B5,B6,B7,DF,DF-hybrid,DF-raw-only,DF-strict,"
    "DF-strict-hybrid,A0,A2,A4,A5,A6,A11"
)
LIVE_CONDITIONS = "B5-MEM0,B5-MEM0-LIT"

STALE_COMPLETION_OUTPUTS = (
    Path("analysis/fold/v2_completion_nonlive.txt"),
    Path("analysis/fold/v2_completion_live.txt"),
    Path("analysis/fold/v2_completion_check.txt"),
)
STALE_ANALYZER_OUTPUTS = (
    Path("analysis/fold/confirmatory.json"),
    Path("analysis/fold/v2_fold.json"),
    Path("analysis/fold/v2_confirmatory_clustered.json"),
    Path("analysis/fold/v2_confirmatory_clustered.stdout.json"),
    Path("analysis/fold/v2_hygiene_oracle.json"),
    Path("analysis/fold/v2_hygiene_oracle.tex"),
    Path("analysis/fold/v2_cost_frontier.json"),
    Path("analysis/fold/v2_cost_frontier.tex"),
    Path("analysis/fold/admission_funnel.json"),
    Path("analysis/fold/admission_funnel.md"),
    Path("analysis/fold/admission_funnel.tex"),
    Path("analysis/fold/v2_tables.tex"),
    Path("analysis/fold/v2_post_analyzer_full_freshness_check.txt"),
)
STALE_FIGURE_OUTPUTS = (
    Path("paper/figures/v2_construct_coverage.pdf"),
    Path("paper/figures/v2_ladder.pdf"),
    Path("paper/figures/v2_verbatim_vs_synthesis.pdf"),
)
REQUIRED_LOCAL_TOOLS = (
    Path("scripts/run_grid.py"),
    Path("scripts/check_v2_confirmatory_completion.py"),
)


@dataclass(frozen=True)
class Check:
    ok: bool
    message: str


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failure_count(self) -> int:
        return sum(1 for check in self.checks if not check.ok)

    def lines(self) -> list[str]:
        status = "PASS" if self.passed else "FAIL"
        lines = ["DreamBench-SWE v2 Paper A post-fold preflight"]
        lines.extend(f"{'PASS' if check.ok else 'FAIL'} {check.message}" for check in self.checks)
        lines.append(f"STATUS {status} failures={self.failure_count}")
        return lines


RemoteRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    run_stamp_file = resolve_under(repo_root, Path(args.run_stamp_file))
    sequence_records = resolve_under(repo_root, Path(args.sequence_records))
    report = audit_preflight(
        repo_root=repo_root,
        run_stamp_file=run_stamp_file,
        expected_run_stamp=args.expected_run_stamp,
        sequence_records=sequence_records,
        expected_sequence_sha256=args.expected_sequence_sha256,
        stale_outputs=tuple(resolve_under(repo_root, path) for path in default_stale_outputs()),
        required_tools=tuple(resolve_under(repo_root, path) for path in REQUIRED_LOCAL_TOOLS),
        check_remote=args.check_remote,
        remote=args.remote,
        remote_root=args.remote_root,
        expected_vps_head=args.expected_vps_head,
        expected_results_count=args.expected_results_count,
    )
    print("\n".join(report.lines()))
    if report.passed:
        print()
        print(render_next_commands(args))
    return 0 if report.passed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only local preflight for the Paper A post-fold gate. "
            "Default mode never contacts the VPS and only prints the next commands."
        )
    )
    parser.add_argument("--repo-root", default=REPO_ROOT, help="repository root")
    parser.add_argument(
        "--run-stamp-file",
        default=str(DEFAULT_RUN_STAMP_FILE),
        help="run-stamp file relative to repo root unless absolute",
    )
    parser.add_argument(
        "--expected-run-stamp",
        default=EXPECTED_RUN_STAMP,
        help="expected active Paper A fold run stamp",
    )
    parser.add_argument(
        "--sequence-records",
        default=str(DEFAULT_SEQUENCE_RECORDS),
        help="confirmatory sequence JSONL relative to repo root unless absolute",
    )
    parser.add_argument(
        "--expected-sequence-sha256",
        default=EXPECTED_CONFIRMATORY_SHA256,
        help="expected SHA256 for --sequence-records",
    )
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="VPS SSH target used in printed commands")
    parser.add_argument(
        "--remote-root",
        default=DEFAULT_REMOTE_ROOT,
        help="VPS Paper A repository root used in printed commands",
    )
    parser.add_argument(
        "--expected-vps-head",
        default=EXPECTED_VPS_HEAD,
        help="expected remote HEAD used in printed commands and optional remote proof",
    )
    parser.add_argument(
        "--expected-results-count",
        type=positive_int,
        default=EXPECTED_RESULTS_COUNT,
        help=f"expected condition-level result count (default: {EXPECTED_RESULTS_COUNT})",
    )
    parser.add_argument(
        "--check-remote",
        action="store_true",
        help="opt-in: execute the read-only one-shell remote proof; never rsyncs or runs analyzers",
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


def audit_preflight(
    *,
    repo_root: Path,
    run_stamp_file: Path,
    expected_run_stamp: str,
    sequence_records: Path,
    expected_sequence_sha256: str,
    stale_outputs: Sequence[Path],
    required_tools: Sequence[Path],
    check_remote: bool = False,
    remote: str = DEFAULT_REMOTE,
    remote_root: str = DEFAULT_REMOTE_ROOT,
    expected_vps_head: str = EXPECTED_VPS_HEAD,
    expected_results_count: int = EXPECTED_RESULTS_COUNT,
    remote_runner: RemoteRunner | None = None,
) -> PreflightReport:
    checks: list[Check] = []
    run_stamp = read_text(run_stamp_file).strip()

    checks.append(check_repo_root(repo_root))
    checks.append(check_run_stamp(run_stamp_file, run_stamp, expected_run_stamp))
    checks.append(check_sequence_hash(sequence_records, expected_sequence_sha256))
    checks.extend(check_required_tools(required_tools))
    checks.extend(check_absent_stale_outputs(stale_outputs, repo_root))
    checks.append(check_local_roots_notice(repo_root, run_stamp))

    if check_remote:
        if all(check.ok for check in checks):
            checks.append(
                run_remote_proof(
                    remote=remote,
                    remote_root=remote_root,
                    run_stamp=run_stamp,
                    expected_vps_head=expected_vps_head,
                    expected_sequence_sha256=expected_sequence_sha256,
                    expected_results_count=expected_results_count,
                    runner=remote_runner,
                )
            )
        else:
            checks.append(Check(False, "remote proof skipped because local preflight failed"))

    return PreflightReport(tuple(checks))


def resolve_under(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def default_stale_outputs() -> tuple[Path, ...]:
    return STALE_COMPLETION_OUTPUTS + STALE_ANALYZER_OUTPUTS + STALE_FIGURE_OUTPUTS


def check_repo_root(repo_root: Path) -> Check:
    if (repo_root / "AGENTS.md").is_file() and (repo_root / "handoff").is_dir():
        return Check(True, f"repo root looks valid: {repo_root}")
    return Check(False, f"repo root does not look like <LOCAL_PROJECT>: {repo_root}")


def check_run_stamp(path: Path, run_stamp: str, expected_run_stamp: str) -> Check:
    if not path.is_file():
        return Check(False, f"run-stamp file missing: {path}")
    if not run_stamp:
        return Check(False, f"run-stamp file is empty: {path}")
    if not re.fullmatch(r"\d{8}T\d{6}Z", run_stamp):
        return Check(False, f"run stamp has unexpected format: {run_stamp!r} from {path}")
    if run_stamp != expected_run_stamp:
        return Check(False, f"run stamp mismatch: actual={run_stamp} expected={expected_run_stamp}")
    return Check(True, f"run stamp matches expected active fold: {run_stamp}")


def check_sequence_hash(path: Path, expected_sha256: str) -> Check:
    expected = expected_sha256.lower()
    if not path.is_file():
        return Check(False, f"sequence records missing: {path}")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return Check(False, f"expected sequence SHA256 is not a 64-character hex digest: {expected_sha256}")
    actual = sha256_file(path)
    if actual != expected:
        return Check(False, f"sequence records sha256 mismatch: {path} actual={actual} expected={expected}")
    return Check(True, f"sequence records sha256 matches expected: {path} sha256={actual}")


def check_required_tools(paths: Sequence[Path]) -> list[Check]:
    return [
        Check(path.is_file(), f"required post-fold tool {'exists' if path.is_file() else 'missing'}: {path}")
        for path in paths
    ]


def check_absent_stale_outputs(paths: Sequence[Path], repo_root: Path) -> list[Check]:
    checks: list[Check] = []
    for path in paths:
        if path.exists():
            checks.append(
                Check(
                    False,
                    (
                        "stale post-fold output exists; archive or explicitly delete before canonical rerun: "
                        f"{display_path(repo_root, path)} "
                        f"(planned archive root: {STALE_OUTPUT_ARCHIVE_DIR})"
                    ),
                )
            )
        else:
            checks.append(Check(True, f"stale output absent: {display_path(repo_root, path)}"))
    return checks


def check_local_roots_notice(repo_root: Path, run_stamp: str) -> Check:
    if not run_stamp:
        return Check(False, "cannot derive local mirror roots because run stamp is empty")
    results_root = repo_root / "experiments" / "results" / f"v2-confirmatory-{run_stamp}"
    log_root = repo_root / "logs" / "grid" / f"v2-confirmatory-{run_stamp}"
    result_count = len(list(results_root.glob("*/*/results.json"))) if results_root.is_dir() else 0
    log_count = len([path for path in log_root.rglob("*") if path.is_file()]) if log_root.is_dir() else 0
    return Check(
        True,
        (
            "local mirror roots are not trusted by preflight; next sync must use rsync --delete "
            f"(current local results={result_count}, logs={log_count})"
        ),
    )


def run_remote_proof(
    *,
    remote: str,
    remote_root: str,
    run_stamp: str,
    expected_vps_head: str,
    expected_sequence_sha256: str,
    expected_results_count: int,
    runner: RemoteRunner | None = None,
) -> Check:
    command = ["ssh", remote, remote_proof_script(remote_root, run_stamp, expected_vps_head, expected_sequence_sha256, expected_results_count)]
    if runner is None:
        runner = lambda cmd: subprocess.run(cmd, text=True, capture_output=True, check=False)
    completed = runner(command)
    if completed.returncode == 0:
        return Check(True, "opt-in remote proof passed")
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    detail = "; ".join(part for part in (stdout, stderr) if part)
    suffix = f": {detail}" if detail else ""
    return Check(False, f"opt-in remote proof failed rc={completed.returncode}{suffix}")


def remote_proof_script(
    remote_root: str,
    run_stamp: str,
    expected_vps_head: str,
    expected_sequence_sha256: str,
    expected_results_count: int,
) -> str:
    return (
        f"cd {remote_root} && "
        f"test \"$(git rev-parse HEAD)\" = \"{expected_vps_head}\" && "
        "test \"$(sha256sum experiments/env/sequences_confirmatory_v2.jsonl | cut -d' ' -f1)\" "
        f"= \"{expected_sequence_sha256}\" && "
        f"grep -H \"\\[NONLIVE_GRID_DONE rc=0\" logs/grid/v2-confirmatory-{run_stamp}/NONLIVE.driver.log && "
        f"grep -H \"\\[LIVE_GRID_DONE rc=0\" logs/grid/v2-confirmatory-{run_stamp}/LIVE.driver.log && "
        f"! grep -HE \"_GRID_DONE rc=[1-9]\" logs/grid/v2-confirmatory-{run_stamp}/*.driver.log && "
        f"! grep -HE \"FINISH .* rc=[1-9]\" logs/grid/v2-confirmatory-{run_stamp}/RUN_GRID.log && "
        "test \"$(find "
        f"experiments/results/v2-confirmatory-{run_stamp} "
        f"-mindepth 3 -maxdepth 3 -name results.json | wc -l | tr -d ' ')\" = \"{expected_results_count}\" && "
        f"! pgrep -af \"python3 scripts/run_grid.py.*v2-confirmatory-{run_stamp}\""
    )


def render_next_commands(args: argparse.Namespace) -> str:
    run_stamp_expr = "$(tr -d '[:space:]' < analysis/fold/v2_confirmatory_run_stamp.txt)"
    remote_command = render_remote_proof_command_for_shell(args.remote, args.expected_results_count)
    return f"""Next commands from the binding post-fold gate:

```bash
cd {Path(args.repo_root).resolve()}
export RUN_STAMP="{run_stamp_expr}"
export EXPECTED_VPS_HEAD={args.expected_vps_head}
export EXPECTED_CONFIRMATORY_SHA256={args.expected_sequence_sha256}
export REMOTE_ROOT={args.remote_root}
export DREAMBENCH_RESULTS_ROOT=experiments/results/v2-confirmatory-$RUN_STAMP
export DREAMBENCH_LOG_ROOT=logs/grid/v2-confirmatory-$RUN_STAMP
export SEQ=experiments/env/sequences_confirmatory_v2.jsonl
export NONLIVE_CONDITIONS="{NONLIVE_CONDITIONS}"
export LIVE_CONDITIONS="{LIVE_CONDITIONS}"

test "$RUN_STAMP" = "{args.expected_run_stamp}"
test "$(sha256sum "$SEQ" | awk '{{print $1}}')" = "$EXPECTED_CONFIRMATORY_SHA256"

{remote_command}
mkdir -p "$DREAMBENCH_RESULTS_ROOT" "$DREAMBENCH_LOG_ROOT"
rsync -av --delete {args.remote}:$REMOTE_ROOT/experiments/results/v2-confirmatory-$RUN_STAMP/ "$DREAMBENCH_RESULTS_ROOT/"
rsync -av --delete {args.remote}:$REMOTE_ROOT/logs/grid/v2-confirmatory-$RUN_STAMP/ "$DREAMBENCH_LOG_ROOT/"

PYTHONPATH=src python3 scripts/run_grid.py --conditions "$NONLIVE_CONDITIONS" --seeds 1,2,3 --group-size 2 --max-parallel 16 --judge-model codex-gpt-5.5 --sequence-records "$SEQ" --results-root "$DREAMBENCH_RESULTS_ROOT" --log-root "$DREAMBENCH_LOG_ROOT" --dry | tee analysis/fold/v2_completion_nonlive.txt
PYTHONPATH=src python3 scripts/run_grid.py --conditions "$LIVE_CONDITIONS" --seeds 1,2,3 --group-size 2 --max-parallel 4 --judge-model codex-gpt-5.5 --sequence-records "$SEQ" --results-root "$DREAMBENCH_RESULTS_ROOT" --log-root "$DREAMBENCH_LOG_ROOT" --dry | tee analysis/fold/v2_completion_live.txt
test "$(grep -c '^RUN ' analysis/fold/v2_completion_nonlive.txt)" = "0"
test "$(grep -c '^RUN ' analysis/fold/v2_completion_live.txt)" = "0"
PYTHONPATH=src python3 scripts/check_v2_confirmatory_completion.py \\
  --results-root "$DREAMBENCH_RESULTS_ROOT" \\
  --log-root "$DREAMBENCH_LOG_ROOT" \\
  --sequence-records "$SEQ" \\
  --expected-sequence-sha256 "$EXPECTED_CONFIRMATORY_SHA256" \\
  --dry-plan analysis/fold/v2_completion_nonlive.txt \\
  --dry-plan analysis/fold/v2_completion_live.txt \\
  | tee analysis/fold/v2_completion_check.txt
tail -1 analysis/fold/v2_completion_check.txt | grep -Fx "STATUS PASS failures=0"
```
"""


def render_remote_proof_command_for_shell(remote: str, expected_results_count: int) -> str:
    return f"""ssh {remote} "cd $REMOTE_ROOT && \\
  test \\"\\$(git rev-parse HEAD)\\" = \\"$EXPECTED_VPS_HEAD\\" && \\
  test \\"\\$(sha256sum experiments/env/sequences_confirmatory_v2.jsonl | cut -d' ' -f1)\\" = \\"$EXPECTED_CONFIRMATORY_SHA256\\" && \\
  grep -H \\"\\\\[NONLIVE_GRID_DONE rc=0\\" logs/grid/v2-confirmatory-$RUN_STAMP/NONLIVE.driver.log && \\
  grep -H \\"\\\\[LIVE_GRID_DONE rc=0\\" logs/grid/v2-confirmatory-$RUN_STAMP/LIVE.driver.log && \\
  ! grep -HE \\"_GRID_DONE rc=[1-9]\\" logs/grid/v2-confirmatory-$RUN_STAMP/*.driver.log && \\
  ! grep -HE \\"FINISH .* rc=[1-9]\\" logs/grid/v2-confirmatory-$RUN_STAMP/RUN_GRID.log && \\
  test \\"\\$(find experiments/results/v2-confirmatory-$RUN_STAMP -mindepth 3 -maxdepth 3 -name results.json | wc -l | tr -d ' ')\\" = \\"{expected_results_count}\\" && \\
  ! pgrep -af \\"python3 scripts/run_grid.py.*v2-confirmatory-$RUN_STAMP\\"\""""


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
