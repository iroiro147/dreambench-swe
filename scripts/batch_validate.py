#!/usr/bin/env python3
"""Batch trap-validation harness for DreamBench-SWE candidate sequences."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import multiprocessing as mp
import os
import queue
import re
import shutil
import sys
import tempfile
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments import run_bench, validate_trap  # noqa: E402
from experiments.env import load_env  # noqa: E402
from scripts import check_synth_no_single_event  # noqa: E402
from scripts import construct_validity  # noqa: E402


VALIDATION_ROOT = ROOT / "experiments" / "validation"
DEFAULT_TIMEOUT_SECONDS = 1800.0

BatchValidateFunc = Callable[[Mapping[str, Any]], Mapping[str, Any]]
BatchConstructValidateFunc = Callable[..., Mapping[str, Any]]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        summary = run_batch(
            Path(args.sequence_records),
            dry=bool(args.dry),
            workers=int(args.workers),
            timeout_seconds=float(args.timeout_seconds),
            resume=bool(args.resume),
            resume_from=[Path(path) for path in args.resume_from],
            validation_root=Path(args.validation_root),
            output_path=Path(args.output) if args.output else None,
            construct_check=bool(args.construct_check),
        )
    except Exception as exc:  # noqa: BLE001 - command-line tool should print a compact failure.
        print(f"batch validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(_summary_table(summary["verdicts"]))
    print(
        "summary "
        f"mode={summary['mode']} "
        f"traps={summary['sequence_count']} "
        f"evaluated={summary['evaluated_count']} "
        f"skipped={summary['skipped_count']} "
        f"valid={summary['valid_count']} "
        f"invalid={summary['invalid_count']} "
        f"construct_checked={summary['construct_validity']['checked_count']} "
        f"construct_invalid={summary['construct_validity']['invalid_count']}"
    )
    print(f"wrote {summary['output_path']}")
    return 0 if summary["invalid_count"] == 0 and summary["timeout_count"] == 0 else 1


def run_batch(
    source_path: Path,
    *,
    dry: bool = False,
    workers: int | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    resume: bool = False,
    resume_from: Sequence[Path] = (),
    validation_root: Path = VALIDATION_ROOT,
    output_path: Path | None = None,
    validate_sequence: Callable[..., Mapping[str, Any]] | None = None,
    construct_check: bool = False,
    construct_validator: BatchConstructValidateFunc | None = None,
) -> dict:
    """Validate all candidate sequences from one file or directory.

    ``validate_sequence`` is an injection seam for unit tests.  Production CLI
    runs leave it unset, which uses process-isolated workers and hard timeouts.
    """

    source_path = Path(source_path)
    records, source_files = load_candidate_records(source_path)
    worker_count = _bounded_workers(workers, len(records))
    timeout_seconds = float(timeout_seconds)
    if timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")
    if dry and construct_check:
        raise ValueError("--construct-check is only supported on the live validation path")

    previous = _load_resume_index(
        validation_root=Path(validation_root),
        resume=resume,
        resume_from=resume_from,
    )
    pending: list[tuple[int, Mapping[str, Any]]] = []
    verdicts: list[dict | None] = [None] * len(records)
    for index, record in enumerate(records):
        key = _resume_key(record)
        prior = previous.get(key)
        if prior is not None:
            skipped = dict(prior)
            skipped["status"] = "skipped"
            skipped["skipped"] = True
            skipped["duration"] = 0.0
            skipped.setdefault("record_hash", _record_hash(record))
            skipped.setdefault("seq_id", _seq_id(record))
            skipped.setdefault("bad_memory_s3_failed", None)
            verdicts[index] = skipped
        else:
            pending.append((index, record))

    if pending:
        if validate_sequence is not None:
            completed = [
                (
                    index,
                    _validate_record(
                        record,
                        dry=dry,
                        validate_sequence=validate_sequence,
                        construct_check=construct_check,
                        construct_validator=construct_validator,
                    ),
                )
                for index, record in pending
            ]
        else:
            completed = _run_in_processes(
                pending,
                dry=dry,
                workers=worker_count,
                timeout_seconds=timeout_seconds,
                construct_check=construct_check,
            )
        for index, verdict in completed:
            verdicts[index] = verdict

    completed_verdicts = [verdict for verdict in verdicts if verdict is not None]
    summary = _summary(
        source_path=source_path,
        source_files=source_files,
        dry=dry,
        workers=worker_count,
        timeout_seconds=timeout_seconds,
        verdicts=completed_verdicts,
        construct_check=construct_check,
    )
    output = _write_summary(summary, validation_root=Path(validation_root), output_path=output_path)
    summary["output_path"] = str(output)
    return summary


def load_candidate_records(source_path: Path) -> tuple[list[dict], list[str]]:
    """Load normalized candidate sequence records from a JSON/JSONL file or directory."""

    source_path = Path(source_path)
    if source_path.is_dir():
        # Only sequence records: injected trap dirs also hold manifest.json /
        # secret_provenance.json sidecars, which are not sequence records.
        _SIDECAR_NAMES = {"manifest.json", "secret_provenance.json", "provenance.json"}
        files = sorted(
            path
            for suffix in ("*.jsonl", "*.json")
            for path in source_path.rglob(suffix)
            if path.is_file() and path.name not in _SIDECAR_NAMES
        )
        if not files:
            raise FileNotFoundError(f"no .json or .jsonl sequence files found under {source_path}")
    else:
        files = [source_path]

    records: list[dict] = []
    seen: dict[str, Path] = {}
    for path in files:
        normalized = load_env.load_sequence_records(path)
        raw_records = _read_json_records(path)
        if len(normalized) != len(raw_records):
            raise ValueError(f"{path} normalized {len(normalized)} records from {len(raw_records)} raw records")
        for ordinal, (raw, record) in enumerate(zip(raw_records, normalized), start=1):
            enriched = dict(raw)
            enriched.update(record)
            if not enriched.get("seq_type"):
                enriched["seq_type"] = raw.get("type") or raw.get("trap_type") or raw.get("sequence_type")
            enriched["_batch_source_path"] = str(path)
            enriched["_batch_source_ordinal"] = ordinal
            seq_id = str(enriched["seq_id"])
            if seq_id in seen:
                raise ValueError(f"duplicate sequence id {seq_id!r} in {path} and {seen[seq_id]}")
            seen[seq_id] = path
            records.append(enriched)

    return records, [str(path) for path in files]


def _validate_record(
    record: Mapping[str, Any],
    *,
    dry: bool,
    validate_sequence: Callable[..., Mapping[str, Any]] | None = None,
    construct_check: bool = False,
    construct_validator: BatchConstructValidateFunc | None = None,
) -> dict:
    start = time.monotonic()
    try:
        verdict = (
            _dry_validate_record(record)
            if dry
            else _live_validate_record(
                record,
                validate_sequence=validate_sequence,
                construct_live=construct_check,
            )
        )
        verdict["status"] = "completed"
    except Exception as exc:  # noqa: BLE001 - one bad trap should produce one bad verdict.
        verdict = _exception_verdict(record, exc)
    _apply_no_single_event(record, verdict)
    if construct_check and not dry:
        _apply_construct_validity(record, verdict, construct_validator=construct_validator)
    verdict["duration"] = round(time.monotonic() - start, 3)
    verdict["record_hash"] = _record_hash(record)
    verdict["mode"] = "dry" if dry else "live"
    verdict.setdefault("skipped", False)
    return validate_trap._redact_hidden_report(verdict)


def _apply_construct_validity(
    record: Mapping[str, Any],
    verdict: dict,
    *,
    construct_validator: BatchConstructValidateFunc | None,
) -> None:
    validator = construct_validator or _default_construct_validator
    try:
        construct_verdict = dict(
            _call_construct_validator(
                validator,
                record,
                verdict.get("b0_s3_failed"),
                verdict.get("bad_memory_s3_failed"),
            )
        )
    except Exception as exc:  # noqa: BLE001 - construct-gate errors should invalidate one trap, not crash the batch.
        construct_verdict = {
            "seq_id": _seq_id(record),
            "valid": False,
            "checked": True,
            "skipped": False,
            "checks": {},
            "single_event_results": [],
            "reasons": [f"construct-validity gate errored: {type(exc).__name__}: {exc}"],
        }
    verdict["construct_validity"] = construct_verdict
    verdict["construct_validity_valid"] = bool(construct_verdict.get("valid"))
    _drop_construct_superseded_reasons(verdict, construct_verdict)
    if construct_verdict.get("valid") is True:
        verdict["valid"] = _construct_checked_raw_hygiene_passes(verdict)
    else:
        verdict["valid"] = False
        verdict.setdefault("reasons", []).extend(
            f"construct-validity: {reason}"
            for reason in construct_verdict.get("reasons") or ["construct-validity gate failed"]
        )


def _construct_checked_raw_hygiene_passes(verdict: Mapping[str, Any]) -> bool:
    """Return the non-construct live-gate hygiene required for admission.

    In construct-check mode the construct scorer owns the B0 signature.  The
    raw live validator still owns reference-solution and oracle hermeticity.
    """

    if verdict.get("refsol_passes") is not True:
        return False
    if verdict.get("oracle_hidden") is not True:
        return False
    if verdict.get("no_single_event") is False:
        return False
    return not bool(verdict.get("reasons"))


_SUPERSEDED_B0_MUST_FAIL_REASON_RE = re.compile(r"^B0 S3 task success was \d+/1; expected 0/1$")


def _drop_construct_superseded_reasons(verdict: dict, construct_verdict: Mapping[str, Any]) -> None:
    """Remove legacy B0-must-fail reasons when construct doctrine expects B0 pass."""

    checks = construct_verdict.get("checks")
    if not isinstance(checks, Mapping):
        return
    if checks.get("b0_expectation") != "PASS" or checks.get("b0_expectation_met") is not True:
        return
    verdict["reasons"] = [
        reason
        for reason in verdict.get("reasons") or []
        if not _SUPERSEDED_B0_MUST_FAIL_REASON_RE.match(str(reason))
    ]


def _call_construct_validator(
    validator: BatchConstructValidateFunc,
    record: Mapping[str, Any],
    b0_s3_failed: Any,
    bad_memory_s3_failed: Any,
) -> Mapping[str, Any]:
    if _construct_validator_accepts_bad_memory(validator):
        return validator(record, b0_s3_failed, bad_memory_s3_failed)
    return validator(record, b0_s3_failed)


def _construct_validator_accepts_bad_memory(validator: BatchConstructValidateFunc) -> bool:
    try:
        signature = inspect.signature(validator)
    except (TypeError, ValueError):
        return True

    positional_count = 0
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            positional_count += 1
    return positional_count >= 3


def _default_construct_validator(
    record: Mapping[str, Any],
    b0_s3_failed: Any,
    bad_memory_s3_failed: Any = None,
) -> Mapping[str, Any]:
    return construct_validity.validate_record(
        record,
        b0_s3_failed=b0_s3_failed,
        bad_memory_s3_failed=bad_memory_s3_failed,
    )


def _live_validate_record(
    record: Mapping[str, Any],
    *,
    validate_sequence: Callable[..., Mapping[str, Any]] | None,
    construct_live: bool = False,
) -> dict:
    validator = validate_sequence or validate_trap.validate_sequence
    raw = dict(validator(record, live=True))
    reasons = list(raw.get("reasons") or [])
    bad_memory_s3_failed = None
    if construct_live and _requires_bad_memory_condition(record):
        try:
            bad_memory_s3_failed, bad_memory_reason = _bad_memory_s3_failed(record)
            if bad_memory_reason:
                reasons.append(bad_memory_reason)
        except Exception as exc:  # noqa: BLE001 - one diagnostic failure should invalidate only this trap.
            reasons.append(f"BAD-MEMORY check errored: {type(exc).__name__}: {exc}")
    return {
        "seq_id": str(raw.get("seq_id") or _seq_id(record)),
        "valid": bool(raw.get("valid")),
        "b0_s3_failed": bool(raw.get("b0_s3_failed")),
        "bad_memory_s3_failed": bad_memory_s3_failed,
        "refsol_passes": bool(raw.get("refsol_passes")),
        "oracle_hidden": bool(raw.get("oracle_hidden")),
        "reasons": reasons,
    }


def _requires_bad_memory_condition(record: Mapping[str, Any]) -> bool:
    return construct_validity._construct_label(record) in construct_validity.BAD_MEMORY_MUST_FAIL


def _bad_memory_s3_failed(record: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Run the C9/C10 BAD-MEMORY diagnostic via the existing B5 policy."""

    with tempfile.TemporaryDirectory(prefix="dreambench-trap-bad-memory-") as tmp:
        tmp_path = Path(tmp)
        sequence_path = tmp_path / "sequence.json"
        sequence_path.write_text(json.dumps(record, sort_keys=True, default=str), encoding="utf-8")
        payload = run_bench.run_sequence_conditions(
            conditions=["B5"],
            model=validate_trap.DEFAULT_MODEL,
            judge_model=run_bench.DEFAULT_JUDGE_MODEL,
            sequence_records_path=sequence_path,
            results_root=tmp_path / "results",
            seed=run_bench.DEFAULT_SEED,
            dry_run=False,
            cleanup_worktrees=True,
        )
    b5_payload = payload["conditions"]["B5"]
    records = [dict(item) for item in b5_payload.get("records") or []]
    s3_records = validate_trap._records_for_session(records, 3)
    if len(s3_records) != 1:
        return False, f"BAD-MEMORY B5 S3 run produced {len(s3_records)} S3 records; expected 1"
    s3_failures = sum(1 for item in s3_records if not bool(item.get("final_passed")))
    if s3_failures != 1:
        return False, f"BAD-MEMORY B5 S3 task failure was {s3_failures}/1; expected 1/1"
    return True, None


def _dry_validate_record(record: Mapping[str, Any]) -> dict:
    seq_id = _seq_id(record)
    verdict = {
        "seq_id": seq_id,
        "valid": False,
        "b0_s3_failed": None,
        "bad_memory_s3_failed": None,
        "refsol_passes": False,
        "oracle_hidden": None,
        "oracle_files_exist": False,
        "dry_checks": {
            "parse": False,
            "oracle_files_exist": False,
            "refsol_applies": False,
        },
        "reasons": [],
    }
    try:
        sequence = validate_trap._normalize_single_sequence(record)
        seq_id = str(sequence["seq_id"])
        verdict["seq_id"] = seq_id
        sessions = validate_trap._sessions_by_index(sequence)
        verdict["dry_checks"]["parse"] = True
    except Exception as exc:  # noqa: BLE001
        verdict["reasons"].append(f"sequence structure failed: {type(exc).__name__}: {exc}")
        return verdict

    missing_oracles = []
    for session_index in sorted(sessions):
        session = sessions[session_index]
        oracle_path = load_env.resolve_oracle_path(
            str(session["oracle_id"]),
            seq_id=seq_id,
            session_index=session_index,
            oracles_root=validate_trap.ORACLES_ROOT,
        )
        if oracle_path is None or not oracle_path.exists():
            missing_oracles.append(f"s{session_index}")
    if missing_oracles:
        verdict["reasons"].append(f"missing oracle file(s): {', '.join(missing_oracles)}")
    else:
        verdict["oracle_files_exist"] = True
        verdict["dry_checks"]["oracle_files_exist"] = True

    refsol_applies, refsol_reasons = _refsol_applies(sequence)
    verdict["refsol_passes"] = bool(refsol_applies)
    verdict["dry_checks"]["refsol_applies"] = bool(refsol_applies)
    verdict["reasons"].extend(refsol_reasons)
    verdict["valid"] = bool(
        verdict["dry_checks"]["parse"]
        and verdict["dry_checks"]["oracle_files_exist"]
        and verdict["dry_checks"]["refsol_applies"]
    )
    return verdict


def _refsol_applies(sequence: Mapping[str, Any]) -> tuple[bool, list[str]]:
    seq_id = str(sequence["seq_id"])
    sessions = validate_trap._sessions_by_index(sequence)
    reasons: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dreambench-batch-refsol-") as tmp:
        tmp_path = Path(tmp)
        state_repo = load_env.materialize(sequence["repo"], sequence["initial_commit"], dest=tmp_path / "state")
        try:
            validate_trap._git_checked(state_repo, "config", "user.email", "dreambench@example.invalid")
            validate_trap._git_checked(state_repo, "config", "user.name", "DreamBench Batch Validator")
            for session_index in sorted(sessions):
                patch_path = validate_trap._refsol_path(seq_id, session_index)
                patch_result = load_env.apply_patch(state_repo, patch_path.read_text(encoding="utf-8"))
                if not patch_result["passed"]:
                    reasons.append(
                        f"reference solution s{session_index} failed to apply: "
                        f"{patch_result.get('stderr', '').strip()}"
                    )
                    return False, reasons
                validate_trap._commit_all(
                    state_repo,
                    f"DreamBench batch dry refsol continuation: {seq_id} s{session_index}",
                )
            return True, reasons
        finally:
            shutil.rmtree(state_repo, ignore_errors=True)


def _apply_no_single_event(record: Mapping[str, Any], verdict: dict) -> None:
    if not _is_synthesis(record):
        return
    ok, reasons = _no_single_event(record)
    verdict["no_single_event"] = bool(ok)
    if not ok:
        verdict["valid"] = False
        verdict.setdefault("reasons", []).extend(reasons)


def _no_single_event(record: Mapping[str, Any]) -> tuple[bool, list[str]]:
    seq_id = _seq_id(record)
    literals = check_synth_no_single_event.DECISIVE_LITERALS.get(seq_id)
    if not literals:
        return False, [f"no-single-event literal mapping missing for synthesis sequence {seq_id}"]

    failures: list[str] = []
    for event in record.get("events") or []:
        if not isinstance(event, Mapping):
            failures.append("non-object event in synthesis sequence")
            continue
        content = str(event.get("content") or "")
        for literal in literals:
            if literal in content:
                failures.append(f"{event.get('event_id') or '<unknown-event>'} contains decisive literal")
    return not failures, failures


def _run_in_processes(
    pending: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    dry: bool,
    workers: int,
    timeout_seconds: float,
    construct_check: bool,
) -> list[tuple[int, dict]]:
    ctx = _multiprocessing_context()
    result_queue = ctx.Queue()
    waiting = deque(pending)
    running: dict[int, dict[str, Any]] = {}
    results: dict[int, dict] = {}

    try:
        while waiting or running:
            while waiting and len(running) < workers:
                index, record = waiting.popleft()
                proc = ctx.Process(target=_worker_entry, args=(index, dict(record), dry, construct_check, result_queue))
                proc.start()
                running[index] = {
                    "process": proc,
                    "record": record,
                    "started_at": time.monotonic(),
                }

            _drain_results(result_queue, results)
            now = time.monotonic()
            for index, info in list(running.items()):
                proc = info["process"]
                elapsed = now - float(info["started_at"])
                if index in results:
                    proc.join(timeout=0.1)
                    running.pop(index, None)
                    continue
                if proc.is_alive() and elapsed > timeout_seconds:
                    proc.terminate()
                    proc.join(timeout=5)
                    if proc.is_alive() and hasattr(proc, "kill"):
                        proc.kill()
                        proc.join(timeout=5)
                    results[index] = _timeout_verdict(info["record"], dry=dry, duration=elapsed, timeout_seconds=timeout_seconds)
                    running.pop(index, None)
                    continue
                if not proc.is_alive():
                    proc.join(timeout=0.1)
                    _drain_results(result_queue, results)
                    if index not in results:
                        results[index] = _crash_verdict(info["record"], dry=dry, exitcode=proc.exitcode, duration=elapsed)
                    running.pop(index, None)
            if waiting or running:
                time.sleep(0.05)
    finally:
        for info in running.values():
            proc = info["process"]
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)

    return [(index, results[index]) for index, _ in pending]


def _worker_entry(index: int, record: Mapping[str, Any], dry: bool, construct_check: bool, result_queue: Any) -> None:
    result_queue.put((index, _validate_record(record, dry=dry, construct_check=construct_check)))


def _drain_results(result_queue: Any, results: dict[int, dict]) -> None:
    while True:
        try:
            index, verdict = result_queue.get_nowait()
        except queue.Empty:
            return
        results[int(index)] = dict(verdict)


def _timeout_verdict(record: Mapping[str, Any], *, dry: bool, duration: float, timeout_seconds: float) -> dict:
    verdict = {
        "seq_id": _seq_id(record),
        "valid": False,
        "b0_s3_failed": None if dry else False,
        "bad_memory_s3_failed": None,
        "refsol_passes": False,
        "oracle_hidden": None if dry else False,
        "duration": round(duration, 3),
        "record_hash": _record_hash(record),
        "mode": "dry" if dry else "live",
        "status": "timeout",
        "skipped": False,
        "timed_out": True,
        "reasons": [f"validation timed out after {timeout_seconds:g}s"],
    }
    if _is_synthesis(record):
        verdict["no_single_event"] = None
    return validate_trap._redact_hidden_report(verdict)


def _crash_verdict(record: Mapping[str, Any], *, dry: bool, exitcode: int | None, duration: float) -> dict:
    return {
        "seq_id": _seq_id(record),
        "valid": False,
        "b0_s3_failed": None if dry else False,
        "bad_memory_s3_failed": None,
        "refsol_passes": False,
        "oracle_hidden": None if dry else False,
        "duration": round(duration, 3),
        "record_hash": _record_hash(record),
        "mode": "dry" if dry else "live",
        "status": "crashed",
        "skipped": False,
        "reasons": [f"validation worker exited without a verdict (exitcode={exitcode})"],
    }


def _exception_verdict(record: Mapping[str, Any], exc: BaseException) -> dict:
    return {
        "seq_id": _seq_id(record),
        "valid": False,
        "b0_s3_failed": False,
        "bad_memory_s3_failed": None,
        "refsol_passes": False,
        "oracle_hidden": False,
        "status": "errored",
        "reasons": [
            f"validation errored: {type(exc).__name__}: {exc}",
            traceback.format_exc(limit=5),
        ],
    }


def _summary(
    *,
    source_path: Path,
    source_files: Sequence[str],
    dry: bool,
    workers: int,
    timeout_seconds: float,
    verdicts: Sequence[Mapping[str, Any]],
    construct_check: bool,
) -> dict:
    evaluated = [verdict for verdict in verdicts if not verdict.get("skipped")]
    valid_count = sum(1 for verdict in verdicts if verdict.get("valid") is True)
    invalid_count = sum(1 for verdict in verdicts if verdict.get("valid") is False)
    construct_verdicts = [
        verdict["construct_validity"]
        for verdict in verdicts
        if isinstance(verdict.get("construct_validity"), Mapping)
    ]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source_path),
        "source_files": list(source_files),
        "mode": "dry" if dry else "live",
        "dry": bool(dry),
        "construct_check": bool(construct_check),
        "construct_validity": construct_validity.summarize(construct_verdicts),
        "workers": int(workers),
        "timeout_seconds": float(timeout_seconds),
        "sequence_count": len(verdicts),
        "evaluated_count": len(evaluated),
        "skipped_count": len(verdicts) - len(evaluated),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "timeout_count": sum(1 for verdict in verdicts if verdict.get("status") == "timeout"),
        "verdicts": list(verdicts),
    }


def _write_summary(summary: Mapping[str, Any], *, validation_root: Path, output_path: Path | None) -> Path:
    validation_root.mkdir(parents=True, exist_ok=True)
    path = output_path
    if path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = validation_root / f"batch-{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(summary)
    payload["output_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _load_resume_index(
    *,
    validation_root: Path,
    resume: bool,
    resume_from: Sequence[Path],
) -> dict[tuple[str, str], dict]:
    paths = list(resume_from)
    if resume:
        paths.extend(sorted(validation_root.glob("batch-*.json")))
    index: dict[tuple[str, str], dict] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for verdict in payload.get("verdicts") or []:
            if not isinstance(verdict, Mapping):
                continue
            seq_id = str(verdict.get("seq_id") or "")
            record_hash = str(verdict.get("record_hash") or "")
            if seq_id and record_hash and verdict.get("status") in {None, "completed", "skipped"}:
                copied = dict(verdict)
                copied["resume_source"] = str(path)
                index[(seq_id, record_hash)] = copied
    return index


def _read_json_records(path: Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        return []
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return [dict(record) for record in payload]
    if isinstance(payload, dict) and isinstance(payload.get("sequences"), list):
        return [dict(record) for record in payload["sequences"]]
    if isinstance(payload, dict):
        return [dict(payload)]
    raise ValueError(f"{path} must contain a JSON object, JSON list, or JSONL records")


def _record_hash(record: Mapping[str, Any]) -> str:
    payload = {
        str(key): value
        for key, value in record.items()
        if not str(key).startswith("_batch_")
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _resume_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return (_seq_id(record), _record_hash(record))


def _seq_id(record: Mapping[str, Any]) -> str:
    return str(record.get("seq_id") or record.get("sequence_id") or "unknown")


def _is_synthesis(record: Mapping[str, Any]) -> bool:
    values = [
        record.get("seq_type"),
        record.get("sequence_type"),
        record.get("type"),
        record.get("trap_type"),
    ]
    return any(str(value or "").strip().lower() == "synthesis" for value in values)


def _bounded_workers(requested: int | None, task_count: int) -> int:
    if task_count <= 0:
        return 1
    if requested is None:
        requested = _default_workers()
    if int(requested) <= 0:
        raise ValueError("--workers must be positive")
    return max(1, min(int(requested), task_count))


def _default_workers() -> int:
    env_value = os.environ.get("DREAMBENCH_BATCH_VALIDATE_WORKERS")
    if env_value:
        return max(1, int(env_value))
    return max(1, min(4, os.cpu_count() or 1))


def _multiprocessing_context() -> mp.context.BaseContext:
    try:
        return mp.get_context("fork")
    except ValueError:
        return mp.get_context()


def _summary_table(verdicts: Sequence[Mapping[str, Any]]) -> str:
    headers = [
        "seq_id",
        "valid",
        "b0_s3_failed",
        "bad_memory_s3_failed",
        "refsol_passes",
        "oracle_hidden",
        "no_single_event",
        "construct_validity",
        "status",
        "duration",
    ]
    rows = []
    for verdict in verdicts:
        rows.append(
            [
                str(verdict.get("seq_id", "")),
                _status(verdict.get("valid")),
                _status(verdict.get("b0_s3_failed")),
                _status(verdict.get("bad_memory_s3_failed")),
                _status(verdict.get("refsol_passes")),
                _status(verdict.get("oracle_hidden")),
                _status(verdict.get("no_single_event")) if "no_single_event" in verdict else "-",
                _status(verdict.get("construct_validity_valid")) if "construct_validity_valid" in verdict else "-",
                str(verdict.get("status") or ""),
                f"{float(verdict.get('duration') or 0.0):.3f}s",
            ]
        )
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(headers[index])
        for index in range(len(headers))
    ]
    lines = [
        "  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))),
        "  ".join("-" * widths[index] for index in range(len(headers))),
    ]
    lines.extend("  ".join(row[index].ljust(widths[index]) for index in range(len(headers))) for row in rows)
    return "\n".join(lines)


def _status(value: Any) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "SKIP"


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sequence_records", help="JSON/JSONL sequence file or directory of candidate sequence files.")
    parser.add_argument("--workers", type=int, default=_default_workers(), help="Parallel workers. Defaults to min(4, CPU count).")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-trap timeout in seconds.",
    )
    parser.add_argument("--dry", action="store_true", help="Run parse/oracle-existence/refsol-apply checks only.")
    parser.add_argument(
        "--construct-check",
        "--construct-live",
        dest="construct_check",
        action="store_true",
        help="Run the construct-aware live gate, including C9/C10 BAD-MEMORY diagnostics.",
    )
    parser.add_argument("--resume", action="store_true", help="Skip seq_id+record_hash entries found in prior batch summaries.")
    parser.add_argument(
        "--resume-from",
        action="append",
        default=[],
        help="Specific prior batch summary to use for resume. May be repeated.",
    )
    parser.add_argument("--validation-root", default=str(VALIDATION_ROOT), help="Directory for batch summary output.")
    parser.add_argument("--output", help="Optional explicit summary JSON path.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
