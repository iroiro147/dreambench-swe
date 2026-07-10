#!/usr/bin/env python3
"""Trap-validity gate for DreamBench-SWE memory-trap sequences."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments import run_bench  # noqa: E402
from experiments.env import load_env  # noqa: E402


DEFAULT_SEQUENCE_RECORDS = ROOT / "experiments" / "env" / "sequences.jsonl"
ORACLES_ROOT = ROOT / "experiments" / "env" / "oracles"
REFSOL_ROOT = ROOT / "experiments" / "env" / "refsol"
VALIDATION_ROOT = ROOT / "experiments" / "validation"
DEFAULT_MODEL = "gpt-5.5"
REQUIRED_VERDICT_KEYS = {
    "seq_id",
    "valid",
    "b0_s3_failed",
    "refsol_passes",
    "oracle_hidden",
    "reasons",
}


def validate_sequence(seq_record: Mapping[str, Any], *, live: bool) -> dict:
    """Return the validity verdict for one sequence record.

    The dry path still exercises the real sequence-run plumbing, but uses
    StubAgent and the harness stub oracle for the B0 difficulty check.
    """

    seq_id = str(seq_record.get("seq_id") or seq_record.get("sequence_id") or "unknown")
    reasons: list[str] = []
    b0_records: list[Mapping[str, Any]] = []

    try:
        sequence = _normalize_single_sequence(seq_record)
        seq_id = str(sequence["seq_id"])
    except Exception as exc:  # noqa: BLE001 - verdicts should report invalid records.
        return {
            "seq_id": seq_id,
            "valid": False,
            "b0_s3_failed": False,
            "refsol_passes": False,
            "oracle_hidden": False,
            "reasons": [f"sequence normalization failed: {type(exc).__name__}: {exc}"],
        }

    try:
        b0_s3_failed, b0_reason, b0_records = _b0_s3_failed(sequence, live=live)
        if b0_reason:
            reasons.append(b0_reason)
    except Exception as exc:  # noqa: BLE001 - keep one bad run from hiding other checks.
        b0_s3_failed = False
        reasons.append(f"B0-MUST-FAIL check errored: {type(exc).__name__}: {exc}")

    try:
        refsol_passes, refsol_reasons = _s3_refsol_passes(sequence)
        reasons.extend(refsol_reasons)
    except Exception as exc:  # noqa: BLE001
        refsol_passes = False
        reasons.append(f"REFSOL-MUST-PASS check errored: {type(exc).__name__}: {exc}")

    try:
        oracle_hidden, oracle_reasons = _s3_oracle_hidden(sequence, b0_records)
        reasons.extend(oracle_reasons)
    except Exception as exc:  # noqa: BLE001
        oracle_hidden = False
        reasons.append(f"ORACLE-HIDDEN check errored: {type(exc).__name__}: {exc}")

    # Prune the agent worktrees kept on disk for the oracle-hidden scan (_b0_s3_failed
    # ran with cleanup_worktrees=False). Precise, record-derived paths only -- never a
    # broad /tmp glob (a broad glob once deleted live continuation state mid-run).
    for _rec in b0_records:
        _task = _rec.get("task") if isinstance(_rec.get("task"), Mapping) else {}
        _wt = _task.get("worktree") if isinstance(_task, Mapping) else None
        if _wt:
            _root = Path(str(_wt)).parent
            if _root.name.startswith("dreambench-agent-"):
                shutil.rmtree(_root, ignore_errors=True)

    valid = bool(b0_s3_failed and refsol_passes and oracle_hidden)
    verdict = {
        "seq_id": seq_id,
        "valid": valid,
        "b0_s3_failed": bool(b0_s3_failed),
        "refsol_passes": bool(refsol_passes),
        "oracle_hidden": bool(oracle_hidden),
        "reasons": reasons,
    }
    assert set(verdict) == REQUIRED_VERDICT_KEYS
    return verdict


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    records_path = DEFAULT_SEQUENCE_RECORDS if args.selftest else Path(args.sequence_records)
    records = load_env.load_sequence_records(records_path)
    records = _filter_sequences(records, args.seq)
    verdicts = [validate_sequence(record, live=bool(args.live)) for record in records]
    report = _redact_hidden_report(_report(
        records_path=records_path,
        seq_filter=args.seq,
        live=bool(args.live),
        verdicts=verdicts,
    ))
    output_path = _write_report(report)
    print(_verdict_table(report["verdicts"]))
    print(f"wrote {output_path}")
    return 0 if all(verdict["valid"] for verdict in verdicts) else 1


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate DreamBench-SWE memory-trap sequence integrity.")
    parser.add_argument("--sequence-records", help="Path to memory-trap sequence JSON/JSONL records.")
    parser.add_argument("--seq", help="Optional comma-separated seq_id filter.")
    parser.add_argument("--live", action="store_true", help="Use the real B0 harness run instead of the dry StubAgent run.")
    parser.add_argument("--selftest", action="store_true", help="Run dry validation against experiments/env/sequences.jsonl.")
    args = parser.parse_args(argv)
    if not args.selftest and not args.sequence_records:
        parser.error("--sequence-records is required unless --selftest is used")
    return args


def _normalize_single_sequence(seq_record: Mapping[str, Any]) -> dict:
    with tempfile.TemporaryDirectory(prefix="dreambench-trap-normalize-") as tmp:
        path = Path(tmp) / "sequence.json"
        path.write_text(json.dumps(seq_record, sort_keys=True), encoding="utf-8")
        records = load_env.load_sequence_records(path)
    if len(records) != 1:
        raise ValueError(f"expected exactly one sequence record, got {len(records)}")
    return records[0]


def _b0_s3_failed(sequence: Mapping[str, Any], *, live: bool) -> tuple[bool, Optional[str], list[Mapping[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="dreambench-trap-b0-") as tmp:
        tmp_path = Path(tmp)
        sequence_path = tmp_path / "sequence.json"
        sequence_path.write_text(json.dumps(sequence, sort_keys=True), encoding="utf-8")
        payload = run_bench.run_sequence_conditions(
            conditions=["B0"],
            model=DEFAULT_MODEL,
            judge_model=run_bench.DEFAULT_JUDGE_MODEL,
            sequence_records_path=sequence_path,
            results_root=tmp_path / "results",
            seed=run_bench.DEFAULT_SEED,
            dry_run=not live,
            # Keep worktrees on disk so the hermeticity check below (oracle-contents-
            # copied-into-worktree, _s3_oracle_hidden) can still scan them; the grid
            # run path leaves cleanup_worktrees=True. Roots are pruned after the checks.
            cleanup_worktrees=False,
        )
    b0_payload = payload["conditions"]["B0"]
    records = [dict(record) for record in b0_payload.get("records") or []]
    s3_records = _records_for_session(records, 3)
    if len(s3_records) != 1:
        return False, f"B0 S3 run produced {len(s3_records)} S3 records; expected 1", records
    s3_successes = sum(1 for record in s3_records if bool(record.get("final_passed")))
    if s3_successes != 0:
        return False, f"B0 S3 task success was {s3_successes}/1; expected 0/1", records
    return True, None, records


def _s3_refsol_passes(sequence: Mapping[str, Any]) -> tuple[bool, list[str]]:
    seq_id = str(sequence["seq_id"])
    sessions = _sessions_by_index(sequence)
    reasons: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dreambench-trap-refsol-") as tmp:
        tmp_path = Path(tmp)
        state_repo = load_env.materialize(sequence["repo"], sequence["initial_commit"], dest=tmp_path / "state")
        try:
            _git_checked(state_repo, "config", "user.email", "dreambench@example.invalid")
            _git_checked(state_repo, "config", "user.name", "DreamBench Trap Validator")
            for session_index in (1, 2):
                patch_path = _refsol_path(seq_id, session_index)
                patch_result = load_env.apply_patch(state_repo, patch_path.read_text(encoding="utf-8"))
                if not patch_result["passed"]:
                    reasons.append(
                        f"reference solution s{session_index} failed to apply: "
                        f"{patch_result.get('stderr', '').strip()}"
                    )
                    return False, reasons
                oracle_result = load_env.run_oracle(
                    state_repo,
                    sessions[session_index]["oracle_cmd"],
                    timeout_seconds=run_bench.ORACLE_TIMEOUT_SECONDS,
                )
                if not oracle_result["passed"]:
                    reasons.append(_oracle_failure_reason(seq_id, session_index, oracle_result))
                    return False, reasons
                _commit_all(state_repo, f"DreamBench trap refsol continuation: {seq_id} s{session_index}")

            s3_patch_path = _refsol_path(seq_id, 3)
            s3_score = load_env.score_agent_diff(
                repo=state_repo,
                base_ref=_rev_parse(state_repo, "HEAD"),
                agent_diff=s3_patch_path.read_text(encoding="utf-8"),
                oracle_cmd=sessions[3]["oracle_cmd"],
                scorer_root=tmp_path / "scorer",
                timeout_seconds=run_bench.ORACLE_TIMEOUT_SECONDS,
            )
            if not s3_score["passed"]:
                reasons.append(_oracle_failure_reason(seq_id, 3, s3_score))
                return False, reasons
            return True, reasons
        finally:
            shutil.rmtree(state_repo, ignore_errors=True)


def _s3_oracle_hidden(sequence: Mapping[str, Any], b0_records: Sequence[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    seq_id = str(sequence["seq_id"])
    session = _sessions_by_index(sequence)[3]
    oracle_path = load_env.resolve_oracle_path(
        str(session["oracle_id"]),
        seq_id=seq_id,
        session_index=3,
        oracles_root=ORACLES_ROOT,
    )
    reasons: list[str] = []
    if oracle_path is None or not oracle_path.exists():
        return False, [f"S3 oracle file missing for {seq_id}"]

    s3_records = _records_for_session(b0_records, 3)
    if len(s3_records) != 1:
        return False, [f"oracle-hidden check saw {len(s3_records)} B0 S3 records; expected 1"]
    public_task = s3_records[0].get("task") if isinstance(s3_records[0].get("task"), Mapping) else {}
    worktree_value = public_task.get("worktree")
    if not worktree_value:
        return False, ["B0 S3 public task did not include a harness worktree"]
    worktree = Path(str(worktree_value)).resolve()

    resolved_oracle = oracle_path.resolve()
    if _is_relative_to(resolved_oracle, worktree):
        reasons.append(f"S3 oracle is inside the agent worktree: {resolved_oracle}")
    if "oracle_cmd" in public_task:
        reasons.append("agent-visible public task leaked oracle_cmd")
    serialized_task = json.dumps(public_task, sort_keys=True)
    if str(resolved_oracle) in serialized_task:
        reasons.append("agent-visible public task leaked the absolute S3 oracle path")
    if str(session.get("oracle_cmd") or "") in serialized_task:
        reasons.append("agent-visible public task leaked the S3 oracle command")
    if worktree.exists():
        copied_oracle = _matching_file_by_digest(worktree, resolved_oracle)
        if copied_oracle is not None:
            reasons.append(f"S3 oracle contents are present in the agent worktree at {copied_oracle}")
    return not reasons, reasons


def _filter_sequences(records: Sequence[Mapping[str, Any]], seq_filter: Optional[str]) -> list[Mapping[str, Any]]:
    if not seq_filter:
        return [dict(record) for record in records]
    wanted = {part.strip() for part in seq_filter.split(",") if part.strip()}
    selected = [dict(record) for record in records if str(record.get("seq_id") or record.get("sequence_id")) in wanted]
    missing = sorted(wanted - {str(record.get("seq_id") or record.get("sequence_id")) for record in selected})
    if missing:
        raise SystemExit(f"unknown seq id(s): {', '.join(missing)}")
    return selected


def _sessions_by_index(sequence: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    sessions = {
        int(session.get("session_index") or 0): session
        for session in sequence.get("sessions") or []
        if isinstance(session, Mapping)
    }
    missing = [index for index in (1, 2, 3) if index not in sessions]
    if missing:
        raise ValueError(f"sequence {sequence.get('seq_id')!r} missing session(s): {missing}")
    return sessions


def _records_for_session(records: Sequence[Mapping[str, Any]], session_index: int) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for record in records:
        task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
        value = task.get("session_index") or record.get("session_index") or 0
        if int(value) == session_index:
            out.append(record)
    return out


def _refsol_path(seq_id: str, session_index: int) -> Path:
    root = REFSOL_ROOT / seq_id
    exact = root / f"s{session_index}.py-diff"
    if exact.exists():
        return exact
    matches = sorted(root.glob(f"s{session_index}*.py-diff"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"missing reference solution {root}/s{session_index}*.py-diff")
    raise ValueError(f"ambiguous reference solutions for {seq_id} s{session_index}: {matches}")


def _oracle_failure_reason(seq_id: str, session_index: int, result: Mapping[str, Any]) -> str:
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    detail = stderr or stdout or str(result.get("error_type") or "oracle failed")
    redacted = _redact_hidden_text(detail[:1000])
    return f"reference solution s{session_index} failed hidden oracle for {seq_id}: {redacted}"


def _git(cwd: Path, *args: str, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "diff.external=", *args],
        cwd=str(cwd),
        input=input_text,
        text=True,
        capture_output=True,
    )


def _git_checked(cwd: Path, *args: str, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    proc = _git(cwd, *args, input_text=input_text)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr}")
    return proc


def _rev_parse(cwd: Path, ref: str) -> str:
    return _git_checked(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _commit_all(cwd: Path, message: str) -> str:
    _git_checked(cwd, "add", "-A")
    staged = _git(cwd, "diff", "--cached", "--quiet")
    if staged.returncode == 0:
        return _rev_parse(cwd, "HEAD")
    _git_checked(cwd, "commit", "--quiet", "-m", message)
    return _rev_parse(cwd, "HEAD")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _matching_file_by_digest(worktree: Path, oracle_path: Path) -> Optional[Path]:
    oracle_digest = hashlib.sha256(oracle_path.read_bytes()).hexdigest()
    for path in worktree.rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(worktree).parts:
            continue
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() == oracle_digest:
                return path
        except OSError:
            continue
    return None


def _report(
    *,
    records_path: Path,
    seq_filter: Optional[str],
    live: bool,
    verdicts: Sequence[Mapping[str, Any]],
) -> dict:
    valid_count = sum(1 for verdict in verdicts if verdict["valid"])
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sequence_records_path": str(records_path),
        "seq_filter": seq_filter,
        "live": bool(live),
        "mode": "live" if live else "dry",
        "sequence_count": len(verdicts),
        "valid_count": valid_count,
        "invalid_count": len(verdicts) - valid_count,
        "verdicts": list(verdicts),
    }


def _redact_hidden_report(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_hidden_text(value)
    if isinstance(value, list):
        return [_redact_hidden_report(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_hidden_report(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _redact_hidden_report(item) for key, item in value.items()}
    return value


def _redact_hidden_text(text: str) -> str:
    redacted = str(text or "")
    root = str(ROOT)
    if root:
        redacted = redacted.replace(root, "[repo-root]")
    redacted = re.sub(r"(?:\[repo-root\]/)?<REVIEWER_ONLY_ORACLES>/[^\s\"']+", "[hidden-oracle-path]", redacted)
    redacted = re.sub(r"(?:\[repo-root\]/)?<REVIEWER_ONLY_REFSOL>/[^\s\"']+", "[hidden-refsol-path]", redacted)
    redacted = re.sub(r"(?:\[repo-root\]/)?experiments/env/sequences\.jsonl", "[hidden-sequences-path]", redacted)
    redacted = re.sub(r"(?:\[repo-root\]/)?experiments/secrets\.env", "[hidden-secrets-path]", redacted)
    redacted = re.sub(r"\bEXPORT-[A-Za-z0-9-]+\b", "[hidden-contract-token]", redacted)
    redacted = re.sub(r"\b[A-Z]{2,}[A-Z0-9]*-[A-Za-z0-9][A-Za-z0-9-]{3,}\b", "[hidden-contract-token]", redacted)
    redacted = re.sub(
        r"(?i)(exact [^.:\n]{0,80} contract\s*:\s*)[^\n]+",
        r"\1[hidden-contract-redacted]",
        redacted,
    )
    return redacted


def _write_report(report: Mapping[str, Any]) -> Path:
    VALIDATION_ROOT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = VALIDATION_ROOT / f"{ts}-validation.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _verdict_table(verdicts: Sequence[Mapping[str, Any]]) -> str:
    rows = [
        [
            str(verdict["seq_id"]),
            _status(bool(verdict["valid"])),
            _status(bool(verdict["b0_s3_failed"])),
            _status(bool(verdict["refsol_passes"])),
            _status(bool(verdict["oracle_hidden"])),
            "; ".join(str(reason) for reason in verdict.get("reasons") or []) or "-",
        ]
        for verdict in verdicts
    ]
    headers = ["seq_id", "valid", "b0_s3_failed", "refsol_passes", "oracle_hidden", "reasons"]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(headers[index])
        for index in range(len(headers))
    ]
    lines = [
        "  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))),
        "  ".join("-" * widths[index] for index in range(len(headers))),
    ]
    lines.extend(
        "  ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        for row in rows
    )
    return "\n".join(lines)


def _status(value: bool) -> str:
    return "PASS" if value else "FAIL"


if __name__ == "__main__":
    raise SystemExit(main())
