#!/usr/bin/env python3
"""Bounded grid runner for DreamForge MVP matrix jobs.

The runner splits sequence records into fixed-size groups, resumes completed
groups by matching existing results, and launches run_bench subprocesses with a
hard concurrency cap.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_ROOT = REPO_ROOT / "experiments" / "results"
DEFAULT_LOG_ROOT = REPO_ROOT / "logs" / "grid"
RESULTS_ROOT = Path(os.environ.get("DREAMBENCH_RESULTS_ROOT") or DEFAULT_RESULTS_ROOT)
LOG_ROOT = Path(os.environ.get("DREAMBENCH_LOG_ROOT") or DEFAULT_LOG_ROOT)
DEFAULT_SEQUENCE_RECORDS = "experiments/env/sequences.jsonl"
DEFAULT_JUDGE_MODEL = "glm-latest"
DEFAULT_GROUP_SIZE = 2
DEFAULT_MAX_PARALLEL = 8
VALID_CONDITIONS = {
    "B0",
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "B6",
    "B7",
    "B5-MEM0",
    "B5-MEM0-LIT",
    "DF",
    "DF-strict",
    "DF-hybrid",
    "DF-raw-only",
    "DF-strict-hybrid",
    "A0",
    "A2",
    "A4",
    "A5",
    "A6",
    "A11",
}
LIVE_BASELINE_CONDITIONS = {"B5-MEM0", "B5-MEM0-LIT"}
TASK_ID_RE = re.compile(r"^(?P<seq>.+)-s(?P<session>\d+)$")
REQUIRED_SEQUENCE_SESSIONS = frozenset({1, 2, 3})


@dataclasses.dataclass(frozen=True)
class WorkUnit:
    condition: str
    seed: int
    group_index: int
    group_total: int
    seq_ids: Tuple[str, ...]

    @property
    def key(self) -> Tuple[str, int, Tuple[str, ...]]:
        return (self.condition, self.seed, tuple(sorted(self.seq_ids)))

    @property
    def label(self) -> str:
        return f"{self.condition}-s{self.seed}-g{self.group_index:02d}"


@dataclasses.dataclass
class RunningJob:
    unit: WorkUnit
    proc: subprocess.Popen[bytes]
    log_handle: object
    log_path: Path
    started_at: float


@dataclasses.dataclass
class UnitResult:
    unit: WorkUnit
    rc: int
    status: str
    log_path: Optional[Path] = None
    elapsed_seconds: float = 0.0
    detail: str = ""


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    conditions = parse_conditions(args.conditions)
    seeds = parse_seeds(args.seeds)
    sequence_records = resolve_repo_path(args.sequence_records)
    results_root = resolve_repo_path(args.results_root)
    log_root = resolve_repo_path(args.log_root)
    seq_ids = load_seq_ids(sequence_records)
    units = build_units(
        conditions=conditions,
        seeds=seeds,
        seq_ids=seq_ids,
        group_size=args.group_size,
    )
    completed = scan_completed_units(results_root)

    pending: List[WorkUnit] = []
    skipped: List[UnitResult] = []
    for unit in units:
        matches = completed.get(unit.key, [])
        if matches:
            skipped.append(
                UnitResult(
                    unit=unit,
                    rc=0,
                    status="skipped",
                    detail=f"resume_match={matches[0]}",
                )
            )
        else:
            pending.append(unit)

    if args.dry:
        print_plan(
            units=units,
            skipped=skipped,
            pending=pending,
            group_size=args.group_size,
            max_parallel=args.max_parallel,
            sequence_records=sequence_records,
            judge_model=args.judge_model,
            results_root=results_root,
        )
        print("DRY RUN: no subprocesses launched")
        return 0

    log_root.mkdir(parents=True, exist_ok=True)
    for result in skipped:
        print(
            f"SKIP {result.unit.label} condition={result.unit.condition} "
            f"seed={result.unit.seed} seqs={','.join(result.unit.seq_ids)} "
            f"{result.detail}"
        )
        append_grid_log(result.unit, "SKIP", rc=0, detail=result.detail, log_root=log_root)

    try:
        launched_results = run_pool(
            units=pending,
            max_parallel=args.max_parallel,
            judge_model=args.judge_model,
            sequence_records=sequence_records,
            results_root=results_root,
            log_root=log_root,
        )
    except KeyboardInterrupt:
        return 130
    failed = [result for result in launched_results if result.rc != 0]
    done = len(skipped) + sum(1 for result in launched_results if result.rc == 0)
    print(
        f"SUMMARY total={len(units)} skipped={len(skipped)} launched={len(launched_results)} "
        f"done={done} failed={len(failed)}"
    )
    return 0 if not failed else 1


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run run_bench over a bounded condition/seed/sequence grid.")
    parser.add_argument(
        "--conditions",
        required=True,
        help=f"Comma-separated conditions, e.g. A0,A2,DF. Valid: {','.join(sorted(VALID_CONDITIONS))}.",
    )
    parser.add_argument("--seeds", required=True, help="Comma-separated integer seeds, e.g. 1,2,3.")
    parser.add_argument(
        "--group-size",
        type=positive_int,
        default=DEFAULT_GROUP_SIZE,
        help=f"Sequence ids per run_bench job (default: {DEFAULT_GROUP_SIZE}).",
    )
    parser.add_argument(
        "--max-parallel",
        type=positive_int,
        default=DEFAULT_MAX_PARALLEL,
        help=f"Maximum concurrent run_bench subprocesses (default: {DEFAULT_MAX_PARALLEL}).",
    )
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help="GRID judge model for DF/ablation jobs.")
    parser.add_argument("--sequence-records", default=DEFAULT_SEQUENCE_RECORDS, help="Sequence records JSONL path.")
    parser.add_argument(
        "--results-root",
        default=str(Path(os.environ.get("DREAMBENCH_RESULTS_ROOT") or DEFAULT_RESULTS_ROOT)),
        help="Result root to scan for completed units and pass to run_bench.",
    )
    parser.add_argument(
        "--log-root",
        default=str(Path(os.environ.get("DREAMBENCH_LOG_ROOT") or DEFAULT_LOG_ROOT)),
        help="Grid subprocess log root.",
    )
    parser.add_argument("--dry", action="store_true", help="Print the launch plan without starting subprocesses.")
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected positive integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected positive integer, got {value!r}")
    return parsed


def parse_conditions(value: str) -> List[str]:
    conditions = [normalize_condition(part) for part in value.split(",") if part.strip()]
    if not conditions:
        raise SystemExit("--conditions must include at least one condition")
    unknown = [condition for condition in conditions if condition not in VALID_CONDITIONS]
    if unknown:
        known = ",".join(sorted(VALID_CONDITIONS))
        raise SystemExit(f"unknown condition(s): {','.join(unknown)}; known={known}")
    return dedupe_preserving_order(conditions)


def normalize_condition(value: str) -> str:
    raw = str(value or "").strip()
    canonical = {condition.upper(): condition for condition in VALID_CONDITIONS}
    return canonical.get(raw.upper(), raw.upper())


def parse_seeds(value: str) -> List[int]:
    seeds: List[int] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            seeds.append(int(item))
        except ValueError as exc:
            raise SystemExit(f"invalid seed {item!r}; seeds must be integers") from exc
    if not seeds:
        raise SystemExit("--seeds must include at least one integer seed")
    return dedupe_preserving_order(seeds)


def dedupe_preserving_order(values: Iterable) -> List:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def load_seq_ids(sequence_records: Path) -> List[str]:
    seq_ids: List[str] = []
    with sequence_records.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{sequence_records}:{line_number}: invalid JSON: {exc}") from exc
            seq_id = record.get("seq_id") or record.get("sequence_id")
            if not seq_id:
                raise SystemExit(f"{sequence_records}:{line_number}: missing seq_id")
            seq_ids.append(str(seq_id))
    if not seq_ids:
        raise SystemExit(f"{sequence_records}: no sequence records found")
    duplicates = sorted(seq_id for seq_id, count in collections.Counter(seq_ids).items() if count > 1)
    if duplicates:
        raise SystemExit(f"{sequence_records}: duplicate seq_id(s): {','.join(duplicates)}")
    return seq_ids


def build_units(
    *,
    conditions: Sequence[str],
    seeds: Sequence[int],
    seq_ids: Sequence[str],
    group_size: int,
) -> List[WorkUnit]:
    groups = [tuple(seq_ids[index : index + group_size]) for index in range(0, len(seq_ids), group_size)]
    units: List[WorkUnit] = []
    for condition in conditions:
        for seed in seeds:
            for group_index, group in enumerate(groups, start=1):
                units.append(
                    WorkUnit(
                        condition=condition,
                        seed=seed,
                        group_index=group_index,
                        group_total=len(groups),
                        seq_ids=group,
                    )
                )
    return units


def scan_completed_units(results_root: Path) -> Dict[Tuple[str, int, Tuple[str, ...]], List[str]]:
    completed: Dict[Tuple[str, int, Tuple[str, ...]], List[str]] = {}
    if not results_root.exists():
        return completed
    for run_dir in sorted(results_root.iterdir()):
        if not run_dir.is_dir():
            continue
        parent_gate = run_dir / "gate_report.json"
        for condition_dir in sorted(run_dir.iterdir()):
            if not condition_dir.is_dir():
                continue
            results_path = condition_dir / "results.json"
            if not results_path.is_file():
                continue
            condition_gate = condition_dir / "gate_report.json"
            if not parent_gate.is_file() and not condition_gate.is_file():
                continue
            payload = load_json(results_path)
            if not isinstance(payload, Mapping):
                continue
            key = completed_key_from_payload(payload, fallback_condition=condition_dir.name)
            if key is None:
                continue
            completed.setdefault(key, []).append(str(results_path))
    return completed


def load_json(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def completed_key_from_payload(
    payload: Mapping[str, object],
    *,
    fallback_condition: str,
) -> Optional[Tuple[str, int, Tuple[str, ...]]]:
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        return None
    condition = normalize_condition(str(manifest.get("condition") or fallback_condition))
    if condition not in VALID_CONDITIONS:
        return None
    try:
        seed = int(manifest.get("seed"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    coverage = extract_session_coverage_from_records(records)
    seq_ids = sorted(coverage)
    if not seq_ids:
        return None
    if any(sessions != REQUIRED_SEQUENCE_SESSIONS for sessions in coverage.values()):
        return None
    return (condition, seed, tuple(seq_ids))


def extract_seq_ids_from_records(records: Sequence[object]) -> set[str]:
    seq_ids: set[str] = set()
    for record in records:
        seq_id = extract_seq_id_from_record(record)
        if seq_id:
            seq_ids.add(seq_id)
    return seq_ids


def extract_session_coverage_from_records(records: Sequence[object]) -> Dict[str, set[int]]:
    grouped: Dict[str, List[Tuple[int, int, Optional[int]]]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            continue
        seq_id = extract_seq_id_from_record(record)
        if not seq_id:
            continue
        grouped.setdefault(seq_id, []).append(
            (record_order(record, fallback=index), index, explicit_session_from_record(record))
        )

    coverage: Dict[str, set[int]] = {}
    for seq_id, entries in grouped.items():
        sessions: set[int] = set()
        for position, (_, _, explicit_session) in enumerate(sorted(entries), start=1):
            session = explicit_session if explicit_session in REQUIRED_SEQUENCE_SESSIONS else position
            if session in REQUIRED_SEQUENCE_SESSIONS:
                sessions.add(session)
        coverage[seq_id] = sessions
    return coverage


def extract_seq_id_from_record(record: object) -> Optional[str]:
    if not isinstance(record, Mapping):
        return None
    task = record.get("task")
    task_mapping = task if isinstance(task, Mapping) else {}
    seq_id = task_mapping.get("seq_id") or task_mapping.get("sequence_id") or record.get("seq_id") or record.get("sequence_id")
    if seq_id:
        return str(seq_id)
    task_id = task_mapping.get("id") or record.get("task_id")
    if task_id:
        match = TASK_ID_RE.match(str(task_id))
        if match:
            return match.group("seq")
    return None


def explicit_session_from_record(record: Mapping[str, object]) -> Optional[int]:
    task = record.get("task")
    task_mapping = task if isinstance(task, Mapping) else {}
    for value in (task_mapping.get("session_index"), record.get("session_index")):
        session = coerce_session_index(value)
        if session is not None:
            return session
    for value in (task_mapping.get("oracle_id"), record.get("oracle_id"), task_mapping.get("id"), record.get("task_id")):
        if not value:
            continue
        match = TASK_ID_RE.match(str(value))
        if not match:
            continue
        session = coerce_session_index(match.group("session"))
        if session is not None:
            return session
    return None


def coerce_session_index(value: object) -> Optional[int]:
    try:
        session = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if session in REQUIRED_SEQUENCE_SESSIONS:
        return session
    return None


def record_order(record: Mapping[str, object], *, fallback: int) -> int:
    try:
        return int(record.get("ordinal"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def print_plan(
    *,
    units: Sequence[WorkUnit],
    skipped: Sequence[UnitResult],
    pending: Sequence[WorkUnit],
    group_size: int,
    max_parallel: int,
    sequence_records: Path,
    judge_model: str,
    results_root: Path,
) -> None:
    skipped_keys = {result.unit.key: result for result in skipped}
    print(
        f"PLAN total={len(units)} pending={len(pending)} skipped={len(skipped)} "
        f"group_size={group_size} max_parallel={max_parallel} "
        f"judge_model={judge_model} sequence_records={display_path(sequence_records)} "
        f"results_root={display_path(results_root)}"
    )
    for index, unit in enumerate(units, start=1):
        result = skipped_keys.get(unit.key)
        action = "SKIP" if result else "RUN"
        detail = f" {result.detail}" if result else ""
        print(
            f"{action} {index:03d}/{len(units):03d} {unit.label} "
            f"condition={unit.condition} seed={unit.seed} "
            f"group={unit.group_index}/{unit.group_total} seqs={','.join(unit.seq_ids)}{detail}"
        )


def run_pool(
    *,
    units: Sequence[WorkUnit],
    max_parallel: int,
    judge_model: str,
    sequence_records: Path,
    results_root: Path,
    log_root: Path,
) -> List[UnitResult]:
    pending: Deque[WorkUnit] = collections.deque(units)
    running: List[RunningJob] = []
    results: List[UnitResult] = []

    def raise_keyboard_interrupt(signum, frame):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
    try:
        while pending or running:
            while pending and len(running) < max_parallel:
                unit = pending.popleft()
                job = launch_unit(
                    unit=unit,
                    judge_model=judge_model,
                    sequence_records=sequence_records,
                    results_root=results_root,
                    log_root=log_root,
                )
                if job is None:
                    results.append(UnitResult(unit=unit, rc=127, status="launch_failed"))
                else:
                    running.append(job)

            for job in list(running):
                rc = job.proc.poll()
                if rc is None:
                    continue
                running.remove(job)
                close_log(job.log_handle)
                elapsed = time.monotonic() - job.started_at
                status = "done" if rc == 0 else "failed"
                result = UnitResult(
                    unit=job.unit,
                    rc=int(rc),
                    status=status,
                    log_path=job.log_path,
                    elapsed_seconds=elapsed,
                )
                results.append(result)
                print(
                    f"FINISH {job.unit.label} rc={rc} elapsed={elapsed:.1f}s "
                    f"log={display_path(job.log_path)}"
                )
                append_grid_log(
                    job.unit,
                    "FINISH",
                    rc=int(rc),
                    detail=f"elapsed={elapsed:.1f}s log={display_path(job.log_path)}",
                    log_root=log_root,
                )

            if pending or running:
                time.sleep(0.25)
    except KeyboardInterrupt:
        print("INTERRUPT received; terminating running subprocess groups", file=sys.stderr)
        terminate_running(running)
        for job in running:
            close_log(job.log_handle)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    return results


def launch_unit(
    *,
    unit: WorkUnit,
    judge_model: str,
    sequence_records: Path,
    results_root: Path,
    log_root: Path,
) -> Optional[RunningJob]:
    log_path = log_root / f"{unit.label}.log"
    command = bench_command(
        unit=unit,
        judge_model=judge_model,
        sequence_records=sequence_records,
        results_root=results_root,
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    try:
        log_handle = log_path.open("w", encoding="utf-8")
    except OSError as exc:
        print(f"LAUNCH_FAILED {unit.label} cannot_open_log={exc}", file=sys.stderr)
        append_grid_log(unit, "LAUNCH_FAILED", rc=127, detail=f"cannot_open_log={exc}", log_root=log_root)
        return None

    started_at = time.monotonic()
    timestamp = utc_timestamp()
    log_handle.write(f"# {timestamp} START {unit.label}\n")
    log_handle.write(f"# cwd={REPO_ROOT}\n")
    log_handle.write(f"# command={' '.join(command)}\n\n")
    log_handle.flush()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_handle.write(f"\n# LAUNCH_FAILED {type(exc).__name__}: {exc}\n")
        close_log(log_handle)
        print(f"LAUNCH_FAILED {unit.label} error={exc}", file=sys.stderr)
        append_grid_log(unit, "LAUNCH_FAILED", rc=127, detail=f"{type(exc).__name__}: {exc}", log_root=log_root)
        return None

    print(
        f"START {unit.label} pid={proc.pid} condition={unit.condition} seed={unit.seed} "
        f"seqs={','.join(unit.seq_ids)} log={display_path(log_path)}"
    )
    append_grid_log(
        unit,
        "START",
        rc=None,
        detail=f"pid={proc.pid} seqs={','.join(unit.seq_ids)} log={display_path(log_path)}",
        log_root=log_root,
    )
    return RunningJob(unit=unit, proc=proc, log_handle=log_handle, log_path=log_path, started_at=started_at)


def bench_command(
    *,
    unit: WorkUnit,
    judge_model: str,
    sequence_records: Path,
    results_root: Path,
) -> List[str]:
    command = [
        sys.executable,
        "-m",
        "experiments.run_bench",
        "--conditions",
        unit.condition,
        "--sequence-records",
        display_path(sequence_records),
        "--judge-model",
        judge_model,
        "--seed",
        str(unit.seed),
        "--seq",
        ",".join(unit.seq_ids),
        "--results-root",
        display_path(results_root),
    ]
    if unit.condition in LIVE_BASELINE_CONDITIONS:
        command.append("--include-live-baselines")
    return command


def append_grid_log(
    unit: WorkUnit,
    event: str,
    *,
    rc: Optional[int],
    detail: str = "",
    log_root: Path = LOG_ROOT,
) -> None:
    log_root.mkdir(parents=True, exist_ok=True)
    rc_text = "" if rc is None else f" rc={rc}"
    line = (
        f"{utc_timestamp()} {event} {unit.label} condition={unit.condition} "
        f"seed={unit.seed} group={unit.group_index}/{unit.group_total} "
        f"seqs={','.join(unit.seq_ids)}{rc_text}"
    )
    if detail:
        line += f" {detail}"
    with (log_root / "RUN_GRID.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def terminate_running(running: Sequence[RunningJob]) -> None:
    for job in running:
        if job.proc.poll() is not None:
            continue
        try:
            os.killpg(job.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(job.proc.poll() is not None for job in running):
            return
        time.sleep(0.1)
    for job in running:
        if job.proc.poll() is not None:
            continue
        try:
            os.killpg(job.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def close_log(handle: object) -> None:
    close = getattr(handle, "close", None)
    if close is not None:
        close()


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
