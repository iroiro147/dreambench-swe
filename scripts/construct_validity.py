#!/usr/bin/env python3
"""Construct-validity gate for DreamBench-SWE v2 traps.

The gate is intentionally separate from the batch runner so it can be tested
against fixture records while production runs still use the real hidden S3
oracle path and scorer.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
import tempfile
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


ORACLES_ROOT = validate_trap.ORACLES_ROOT
REQUIRED_METADATA_KEYS = (
    "insufficiency_mechanism",
    "required_fact_ids",
    "event_fact_map",
    "decisive_oracle_literals",
    "commission_checks",
    "omission_checks",
)

B0_MUST_FAIL = frozenset({"C1", "C2", "C3", "C4", "C5", "C6", "C7"})
B0_MUST_PASS = frozenset({"C9", "C10"})
SINGLE_EVENT_COPY_MUST_FAIL = frozenset({"C2", "C3", "C6", "C7"})
BAD_MEMORY_MUST_FAIL = frozenset({"C9", "C10"})
REQUIRED_FACTS_MUST_SPAN_EVENTS = frozenset({"C3", "C6", "C7"})
SUPPORTED_CONSTRUCTS = B0_MUST_FAIL | B0_MUST_PASS
_STORED_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,15}-[A-Za-z0-9]{3,12}(?:-[A-Za-z0-9]{2,12})+\b")

SingleEventScorer = Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]]


def validate_record(
    record: Mapping[str, Any],
    *,
    b0_s3_failed: Any,
    bad_memory_s3_failed: Any = None,
    oracles_root: Path | str = ORACLES_ROOT,
    single_event_scorer: SingleEventScorer | None = None,
) -> dict:
    """Return one construct-validity verdict for ``record``.

    ``b0_s3_failed`` is supplied by the live trap gate.  C1-C7 expect that
    value to be true; C9/C10 expect it to be false and also require a
    bad-memory diagnostic whose S3 oracle fails.  The single-event check scores
    each pre-S3 event payload as though it were the S3 agent answer, but only
    for constructs whose signature requires single-event-copy failure.
    """

    seq_id = _seq_id(record)
    construct_label = _construct_label(record)
    metadata = _metadata(record)
    resolved_bad_memory_s3_failed = _resolve_bad_memory_s3_failed(
        explicit_value=bad_memory_s3_failed,
        record=record,
        metadata=metadata,
    )
    reasons: list[str] = []
    checks: dict[str, Any] = {
        "construct_label": construct_label or None,
        "b0_s3_failed": _optional_bool(b0_s3_failed),
        "b0_expectation": _b0_expectation(construct_label),
        "b0_expectation_met": False,
        "metadata_present": False,
        "metadata_consistent": False,
        "decisive_literals_in_oracle": False,
        "required_facts_span_events": False,
        "single_event_copy_required": construct_label in SINGLE_EVENT_COPY_MUST_FAIL,
        "single_event_copy_rejected": None,
        "bad_memory_condition_required": construct_label in BAD_MEMORY_MUST_FAIL,
        "bad_memory_s3_failed": _optional_bool(resolved_bad_memory_s3_failed),
        "bad_memory_condition_failed": False,
        "stored_token_required_by_s3_oracle": False,
        "stored_tokens_required_by_s3_oracle": [],
    }

    if construct_label not in SUPPORTED_CONSTRUCTS:
        reasons.append(f"unsupported or missing construct label: {construct_label or '<missing>'}")

    if construct_label in B0_MUST_FAIL:
        if b0_s3_failed is True:
            checks["b0_expectation_met"] = True
        else:
            reasons.append(f"{construct_label} expects B0 no-memory S3 to fail")
    elif construct_label in B0_MUST_PASS:
        if b0_s3_failed is False:
            checks["b0_expectation_met"] = True
        else:
            reasons.append(f"{construct_label} expects B0 no-memory S3 to pass")

    metadata_ok = True
    if construct_label != "C1":
        metadata_ok = _validate_metadata_presence(metadata, reasons)
    checks["metadata_present"] = bool(metadata_ok)

    oracle_text: str | None = None
    if metadata_ok:
        consistency = _validate_metadata_consistency(
            record,
            metadata,
            reasons,
            oracles_root=oracles_root,
            require_required_facts_span=construct_label in REQUIRED_FACTS_MUST_SPAN_EVENTS,
        )
        checks.update(consistency)
        oracle_text = consistency.get("_oracle_text")
        checks.pop("_oracle_text", None)

    single_event_results: list[dict[str, Any]] = []
    if construct_label in SINGLE_EVENT_COPY_MUST_FAIL:
        events = _pre_s3_events(record, reasons)
        scorer = single_event_scorer or score_single_event_copies
        try:
            single_event_results = [_normalize_event_result(result) for result in scorer(record, events)]
        except Exception as exc:  # noqa: BLE001 - a gate error invalidates this trap.
            reasons.append(f"single-event verbatim-copy check errored: {type(exc).__name__}: {exc}")

        passed_events = [result for result in single_event_results if result.get("passed") is True]
        if passed_events:
            ids = ", ".join(str(result.get("event_id") or "<unknown-event>") for result in passed_events)
            reasons.append(f"single-event verbatim copy passed S3 oracle: {ids}")
            checks["single_event_copy_rejected"] = False
        elif events and len(single_event_results) != len(events):
            reasons.append(
                f"single-event verbatim-copy check returned {len(single_event_results)} result(s) "
                f"for {len(events)} event(s)"
            )
            checks["single_event_copy_rejected"] = False
        elif events and len(single_event_results) == len(events):
            checks["single_event_copy_rejected"] = True

    if construct_label in BAD_MEMORY_MUST_FAIL:
        if resolved_bad_memory_s3_failed is True:
            checks["bad_memory_condition_failed"] = True
        elif resolved_bad_memory_s3_failed is False:
            reasons.append(f"{construct_label} expects the bad-memory diagnostic condition to fail")
        else:
            reasons.append(f"{construct_label} requires a bad-memory diagnostic failure verdict")

        if oracle_text is None:
            try:
                oracle_text = _s3_oracle_text(record, oracles_root=oracles_root)
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"S3 oracle text unavailable for stored-token inversion check: {type(exc).__name__}: {exc}")
                oracle_text = ""
        required_tokens = _stored_tokens_required_by_s3_oracle(record, metadata, oracle_text)
        checks["stored_tokens_required_by_s3_oracle"] = required_tokens
        checks["stored_token_required_by_s3_oracle"] = bool(required_tokens)
        if required_tokens:
            reasons.append(
                "S3 oracle requires stored token(s), forcing B0 to fail: " + ", ".join(required_tokens)
            )

    valid_requirements = [
        construct_label in SUPPORTED_CONSTRUCTS,
        checks["b0_expectation_met"],
        checks["metadata_present"],
        checks["metadata_consistent"],
        checks["decisive_literals_in_oracle"] if construct_label != "C1" else True,
    ]
    if construct_label in REQUIRED_FACTS_MUST_SPAN_EVENTS:
        valid_requirements.append(checks["required_facts_span_events"])
    if construct_label in SINGLE_EVENT_COPY_MUST_FAIL:
        valid_requirements.append(checks["single_event_copy_rejected"] is True)
    if construct_label in BAD_MEMORY_MUST_FAIL:
        valid_requirements.extend(
            [
                checks["bad_memory_condition_failed"],
                checks["stored_token_required_by_s3_oracle"] is False,
            ]
        )

    valid = bool(all(valid_requirements) and not reasons)
    return {
        "seq_id": seq_id,
        "construct_label": construct_label or None,
        "valid": valid,
        "checked": True,
        "skipped": False,
        "signature": _signature_for(construct_label),
        "checks": checks,
        "single_event_results": single_event_results,
        "reasons": reasons,
    }


def score_single_event_copies(
    record: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Score each event payload as the exact S3 patch/answer.

    Production uses the same S3 state construction as the trap validator: apply
    S1 and S2 reference patches, commit them, then score the candidate text
    through ``load_env.score_agent_diff`` with the S3 hidden oracle.
    """

    sequence = validate_trap._normalize_single_sequence(record)
    seq_id = str(sequence["seq_id"])
    sessions = validate_trap._sessions_by_index(sequence)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="dreambench-construct-copy-") as tmp:
        tmp_path = Path(tmp)
        state_repo = load_env.materialize(sequence["repo"], sequence["initial_commit"], dest=tmp_path / "state")
        try:
            validate_trap._git_checked(state_repo, "config", "user.email", "dreambench@example.invalid")
            validate_trap._git_checked(state_repo, "config", "user.name", "DreamBench Construct Validator")
            for session_index in (1, 2):
                patch_path = validate_trap._refsol_path(seq_id, session_index)
                patch_result = load_env.apply_patch(state_repo, patch_path.read_text(encoding="utf-8"))
                if not patch_result["passed"]:
                    raise RuntimeError(
                        f"reference solution s{session_index} failed to apply before single-event check: "
                        f"{patch_result.get('stderr', '').strip()}"
                    )
                oracle_result = load_env.run_oracle(
                    state_repo,
                    sessions[session_index]["oracle_cmd"],
                    timeout_seconds=run_bench.ORACLE_TIMEOUT_SECONDS,
                )
                if not oracle_result["passed"]:
                    raise RuntimeError(
                        f"reference solution s{session_index} failed hidden oracle before single-event check"
                    )
                validate_trap._commit_all(
                    state_repo,
                    f"DreamBench construct single-event setup: {seq_id} s{session_index}",
                )

            base_ref = validate_trap._rev_parse(state_repo, "HEAD")
            for event in events:
                payload = _event_payload(event)
                result = load_env.score_agent_diff(
                    repo=state_repo,
                    base_ref=base_ref,
                    agent_diff=payload,
                    oracle_cmd=sessions[3]["oracle_cmd"],
                    scorer_root=tmp_path / "scorer",
                    timeout_seconds=run_bench.ORACLE_TIMEOUT_SECONDS,
                )
                results.append(
                    {
                        "event_id": _event_id(event),
                        "passed": bool(result.get("passed")),
                        "error_type": result.get("error_type"),
                        "empty_production_diff": bool(result.get("empty_production_diff")),
                        "production_files": list(result.get("production_files") or []),
                    }
                )
            return results
        finally:
            shutil.rmtree(state_repo, ignore_errors=True)


def summarize(verdicts: Sequence[Mapping[str, Any]]) -> dict:
    checked = [verdict for verdict in verdicts if verdict.get("checked")]
    skipped = [verdict for verdict in verdicts if verdict.get("skipped")]
    by_construct: dict[str, dict[str, int]] = {}
    for verdict in verdicts:
        construct = _verdict_construct_label(verdict)
        bucket = by_construct.setdefault(
            construct,
            {
                "checked_count": 0,
                "skipped_count": 0,
                "valid_count": 0,
                "invalid_count": 0,
            },
        )
        if verdict.get("skipped"):
            bucket["skipped_count"] += 1
        if verdict.get("checked"):
            bucket["checked_count"] += 1
            if verdict.get("valid") is True:
                bucket["valid_count"] += 1
            elif verdict.get("valid") is False:
                bucket["invalid_count"] += 1
    return {
        "checked_count": len(checked),
        "skipped_count": len(skipped),
        "valid_count": sum(1 for verdict in checked if verdict.get("valid") is True),
        "invalid_count": sum(1 for verdict in checked if verdict.get("valid") is False),
        "by_construct": dict(sorted(by_construct.items())),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    records = _read_json_records(Path(args.sequence_records))
    condition_verdicts_by_seq = _read_condition_verdicts(Path(args.batch_summary)) if args.batch_summary else {}
    verdicts = [
        validate_record(
            record,
            b0_s3_failed=condition_verdicts_by_seq.get(_seq_id(record), {}).get("b0_s3_failed"),
            bad_memory_s3_failed=condition_verdicts_by_seq.get(_seq_id(record), {}).get("bad_memory_s3_failed"),
            oracles_root=Path(args.oracles_root),
        )
        for record in records
    ]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sequence_records": str(args.sequence_records),
        "summary": summarize(verdicts),
        "verdicts": verdicts,
    }
    print(json.dumps(validate_trap._redact_hidden_report(report), indent=2, sort_keys=True))
    return 0 if report["summary"]["invalid_count"] == 0 else 1


def _validate_metadata_presence(metadata: Mapping[str, Any], reasons: list[str]) -> bool:
    missing = [key for key in REQUIRED_METADATA_KEYS if key not in metadata]
    if missing:
        reasons.append("missing construct metadata key(s): " + ", ".join(missing))
        return False

    ok = True
    if not _non_empty_string(metadata.get("insufficiency_mechanism")):
        reasons.append("insufficiency_mechanism must be a non-empty string")
        ok = False
    for key in ("required_fact_ids", "decisive_oracle_literals", "commission_checks", "omission_checks"):
        values = metadata.get(key)
        if not _non_empty_string_list(values):
            reasons.append(f"{key} must be a non-empty list of strings")
            ok = False
    event_fact_map = metadata.get("event_fact_map")
    if not isinstance(event_fact_map, Mapping) or not event_fact_map:
        reasons.append("event_fact_map must be a non-empty object")
        ok = False
    elif not all(_non_empty_string_list(value) for value in event_fact_map.values()):
        reasons.append("event_fact_map values must be non-empty lists of strings")
        ok = False
    return ok


def _validate_metadata_consistency(
    record: Mapping[str, Any],
    metadata: Mapping[str, Any],
    reasons: list[str],
    *,
    oracles_root: Path | str = ORACLES_ROOT,
    require_required_facts_span: bool,
) -> dict[str, Any]:
    checks = {
        "metadata_consistent": True,
        "decisive_literals_in_oracle": False,
        "required_facts_span_events": False,
    }
    event_ids = {_event_id(event) for event in record.get("events") or [] if isinstance(event, Mapping)}
    pre_s3_event_ids = {
        _event_id(event)
        for event in record.get("events") or []
        if isinstance(event, Mapping) and _is_pre_s3_event(event)
    }
    event_fact_map = metadata.get("event_fact_map") if isinstance(metadata.get("event_fact_map"), Mapping) else {}
    unknown_events = sorted(str(event_id) for event_id in event_fact_map if str(event_id) not in event_ids)
    if unknown_events:
        checks["metadata_consistent"] = False
        reasons.append("event_fact_map references unknown event id(s): " + ", ".join(unknown_events))

    required_facts = {str(value) for value in metadata.get("required_fact_ids") or []}
    mapped_facts: set[str] = set()
    spanning_events: set[str] = set()
    for event_id, values in event_fact_map.items():
        facts = {str(value) for value in values or []}
        mapped_facts.update(facts)
        if str(event_id) in pre_s3_event_ids and facts & required_facts:
            spanning_events.add(str(event_id))
    missing_facts = sorted(required_facts - mapped_facts)
    if missing_facts:
        checks["metadata_consistent"] = False
        reasons.append("required_fact_ids missing from event_fact_map: " + ", ".join(missing_facts))
    if len(spanning_events) >= 2:
        checks["required_facts_span_events"] = True
    elif require_required_facts_span:
        reasons.append("required_fact_ids must span at least two S1/S2 events")

    literals = [str(value) for value in metadata.get("decisive_oracle_literals") or []]
    try:
        oracle_text = _s3_oracle_text(record, oracles_root=oracles_root)
    except Exception as exc:  # noqa: BLE001 - missing/unreadable oracle invalidates the construct gate.
        checks["metadata_consistent"] = False
        reasons.append(f"S3 oracle text unavailable for decisive literal check: {type(exc).__name__}: {exc}")
        return checks

    missing_literals = [literal for literal in literals if literal not in oracle_text]
    if missing_literals:
        checks["metadata_consistent"] = False
        reasons.append("decisive_oracle_literals absent from S3 oracle text: " + ", ".join(missing_literals))
    else:
        checks["decisive_literals_in_oracle"] = True
    checks["_oracle_text"] = oracle_text
    return checks


def _pre_s3_events(record: Mapping[str, Any], reasons: list[str]) -> list[Mapping[str, Any]]:
    events: list[Mapping[str, Any]] = []
    for event in record.get("events") or []:
        if not isinstance(event, Mapping):
            reasons.append("events must be objects for single-event verbatim-copy check")
            continue
        if not _is_pre_s3_event(event):
            continue
        if not _event_payload(event):
            reasons.append(f"{_event_id(event)} is missing a non-empty payload/content string")
            continue
        events.append(event)
    if not events:
        reasons.append("no S1/S2 events available for single-event verbatim-copy check")
    return events


def _is_pre_s3_event(event: Mapping[str, Any]) -> bool:
    after_session = event.get("after_session")
    try:
        return after_session is None or int(after_session) in {1, 2}
    except (TypeError, ValueError):
        return False


def _s3_oracle_text(record: Mapping[str, Any], *, oracles_root: Path | str = ORACLES_ROOT) -> str:
    seq_id = _seq_id(record)
    session = _session(record, 3)
    oracle_path = load_env.resolve_oracle_path(
        str(session.get("oracle_id") or f"{seq_id}-s3"),
        seq_id=seq_id,
        session_index=3,
        oracles_root=oracles_root,
    )
    if oracle_path is None or not oracle_path.exists():
        raise FileNotFoundError(f"S3 oracle file missing for {seq_id}")
    return oracle_path.read_text(encoding="utf-8")


def _metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("validation_metadata", "construct_validation", "construct_validity"):
        value = record.get(key)
        if isinstance(value, Mapping):
            return value
    if any(key in record for key in REQUIRED_METADATA_KEYS):
        return {key: record.get(key) for key in REQUIRED_METADATA_KEYS if key in record}
    return {}


def _session(record: Mapping[str, Any], session_index: int) -> Mapping[str, Any]:
    for session in record.get("sessions") or []:
        if not isinstance(session, Mapping):
            continue
        try:
            if int(session.get("session_index") or 0) == session_index:
                return session
        except (TypeError, ValueError):
            continue
    raise ValueError(f"sequence {_seq_id(record)!r} missing session {session_index}")


def _event_payload(event: Mapping[str, Any]) -> str:
    for key in ("payload", "patch", "answer", "content", "text", "message"):
        value = event.get(key)
        if isinstance(value, Mapping):
            for nested_key in ("patch", "answer", "content", "text", "message"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    return nested
        elif isinstance(value, str) and value.strip():
            return value
    return ""


def _normalize_event_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(result.get("event_id") or "<unknown-event>"),
        "passed": bool(result.get("passed")),
        "error_type": result.get("error_type"),
        "empty_production_diff": bool(result.get("empty_production_diff")),
        "production_files": list(result.get("production_files") or []),
    }


def _event_id(event: Mapping[str, Any]) -> str:
    return str(event.get("event_id") or event.get("id") or "<unknown-event>")


def _seq_id(record: Mapping[str, Any]) -> str:
    return str(record.get("seq_id") or record.get("sequence_id") or "unknown")


def _construct_label(record: Mapping[str, Any]) -> str:
    for key in ("construct_label", "construct", "construct_id"):
        value = str(record.get(key) or "").strip().upper()
        if value:
            return value
    seq_id = _seq_id(record).lower()
    for index in range(1, 11):
        marker = f"c{index}"
        if f"-{marker}-" in seq_id or seq_id.startswith(f"{marker}-") or seq_id.startswith(f"v2-{marker}-"):
            return marker.upper()
    return ""


def _b0_expectation(construct_label: str) -> str | None:
    if construct_label in B0_MUST_FAIL:
        return "FAIL"
    if construct_label in B0_MUST_PASS:
        return "PASS"
    return None


def _signature_for(construct_label: str) -> dict[str, Any]:
    return {
        "b0": _b0_expectation(construct_label),
        "single_event_copy": "FAIL" if construct_label in SINGLE_EVENT_COPY_MUST_FAIL else "N/A",
        "bad_memory_condition": "FAIL" if construct_label in BAD_MEMORY_MUST_FAIL else "N/A",
        "required_facts_span_events": construct_label in REQUIRED_FACTS_MUST_SPAN_EVENTS,
    }


def _resolve_bad_memory_s3_failed(
    *,
    explicit_value: Any,
    record: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> Any:
    explicit_bool = _coerce_optional_bool(explicit_value)
    if explicit_bool is not None:
        return explicit_bool

    for source in (metadata, record):
        for key in (
            "bad_memory_s3_failed",
            "bad_memory_condition_failed",
            "bad_memory_failed",
            "diagnostic_policy_s3_failed",
            "diagnostic_policy_failed",
        ):
            value = _coerce_optional_bool(source.get(key))
            if value is not None:
                return value
        verdict = source.get("bad_memory_verdict")
        if isinstance(verdict, Mapping):
            for key in ("s3_failed", "failed", "oracle_failed"):
                value = _coerce_optional_bool(verdict.get(key))
                if value is not None:
                    return value
    return None


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is True or value is False:
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "fail", "failed", "yes", "1"}:
            return True
        if normalized in {"false", "pass", "passed", "no", "0"}:
            return False
    return None


def _optional_bool(value: Any) -> bool | None:
    return _coerce_optional_bool(value)


def _stored_tokens_required_by_s3_oracle(
    record: Mapping[str, Any],
    metadata: Mapping[str, Any],
    oracle_text: str,
) -> list[str]:
    stored_tokens = _stored_event_tokens(record)
    if not stored_tokens:
        return []

    required_tokens: set[str] = set()
    decisive_text = "\n".join(str(value) for value in metadata.get("decisive_oracle_literals") or [])
    for token in _tokens_in_text(decisive_text):
        if token in stored_tokens and token in oracle_text:
            required_tokens.add(token)

    for token in _positive_assert_tokens(oracle_text):
        if token in stored_tokens:
            required_tokens.add(token)
    return sorted(required_tokens)


def _stored_event_tokens(record: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for event in record.get("events") or []:
        if isinstance(event, Mapping) and _is_pre_s3_event(event):
            tokens.update(_tokens_in_text(_event_search_text(event)))
    return tokens


def _event_search_text(event: Mapping[str, Any]) -> str:
    try:
        return json.dumps(event, sort_keys=True, default=str)
    except TypeError:
        return " ".join(str(value) for value in event.values())


def _tokens_in_text(text: str) -> set[str]:
    return set(_STORED_TOKEN_RE.findall(text or ""))


def _positive_assert_tokens(oracle_text: str) -> set[str]:
    try:
        tree = ast.parse(oracle_text)
    except SyntaxError:
        return set()
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            tokens.update(_positive_tokens_in_expr(node.test))
    return tokens


def _positive_tokens_in_expr(node: ast.AST) -> set[str]:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return set()
    if isinstance(node, ast.BoolOp):
        tokens: set[str] = set()
        for value in node.values:
            tokens.update(_positive_tokens_in_expr(value))
        return tokens
    if isinstance(node, ast.Compare):
        if any(isinstance(op, (ast.NotIn, ast.NotEq, ast.IsNot)) for op in node.ops):
            return set()
        return _string_tokens_in_ast(node)
    return _string_tokens_in_ast(node)


def _string_tokens_in_ast(node: ast.AST) -> set[str]:
    tokens: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            tokens.update(_tokens_in_text(child.value))
    return tokens


def _verdict_construct_label(verdict: Mapping[str, Any]) -> str:
    value = verdict.get("construct_label")
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    checks = verdict.get("checks")
    if isinstance(checks, Mapping):
        value = checks.get("construct_label")
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return "<unknown>"


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_non_empty_string(item) for item in value)


def _read_json_records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
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


def _read_condition_verdicts(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for verdict in payload.get("verdicts") or []:
        if isinstance(verdict, Mapping):
            seq_id = str(verdict.get("seq_id") or "")
            if not seq_id:
                continue
            out[seq_id] = {
                "b0_s3_failed": verdict.get("b0_s3_failed"),
                "bad_memory_s3_failed": verdict.get("bad_memory_s3_failed"),
            }
    return out


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sequence_records", help="JSON/JSONL sequence records to check.")
    parser.add_argument(
        "--batch-summary",
        help="Optional batch_validate summary whose b0_s3_failed verdicts should seed the gate.",
    )
    parser.add_argument("--oracles-root", default=str(ORACLES_ROOT), help="Hidden oracle root.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
