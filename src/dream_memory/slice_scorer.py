"""Label-grounded scorer for the 08b memory-trap slice.

The scorer consumes hidden ``oracle_labels`` from sequence records.  Its
denominators are label opportunities, not the number of memories a condition
happened to retrieve.
"""
from __future__ import annotations

import ast
import re
import textwrap
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional


HEADLINE_SLICE_METRICS = (
    "StaleMemoryActivationRate",
    "ContradictionRepairAccuracy",
    "RepeatedErrorRate",
    "HumanFeedbackUseAccuracy",
    "HarmfulMemoryRate",
    "UsefulMemoryPrecision",
    "ScopeAccuracy",
    "TransferScore",
    "RegressionAfterUpdate",
)

LabelJudge = Callable[[Mapping[str, Any]], Mapping[str, Any]]

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "can",
    "do",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "must",
    "new",
    "not",
    "of",
    "or",
    "should",
    "that",
    "the",
    "to",
    "use",
    "with",
}

_INACTIVE_MEMORY_STATUSES = {"stale", "superseded", "deleted", "requires_review"}


@dataclass(frozen=True)
class _LabelCase:
    sequence: Mapping[str, Any]
    label: Mapping[str, Any]
    active_session: int
    scope: Mapping[str, Any]
    event_text: str


def score_memory_trap_slice(
    *,
    sequence_records: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    condition: str,
    memory_snapshot: Optional[Sequence[Mapping[str, Any]]] = None,
    label_judge: Optional[LabelJudge] = None,
) -> dict[str, Any]:
    """Return aggregate and per-session label-grounded scorer output.

    ``label_judge`` is the explicit hook for future LLM label judging.  When it
    is absent, behavioral questions fall back to deterministic patch/string/AST
    inspection plus oracle-result evidence so offline tests remain computable.
    """

    cases = _label_cases(sequence_records)
    records_by_key = _records_by_key(records)
    totals = _empty_totals()
    label_counts: dict[str, int] = defaultdict(int)
    details: list[dict[str, Any]] = []

    for case in cases:
        label_counts[str(case.label.get("type") or "")] += 1

    for sequence in sequence_records:
        seq_id = _seq_id(sequence)
        sessions = _sessions(sequence)
        session_indexes = [int(session.get("session_index") or 0) for session in sessions] or [3]
        for session_index in session_indexes:
            record = records_by_key.get((seq_id, session_index))
            session_cases = [
                case for case in cases
                if _seq_id(case.sequence) == seq_id and case.active_session == session_index
            ]
            detail = _base_detail(condition, seq_id, session_index, record)
            for case in session_cases:
                label_result = _score_label_case(
                    case,
                    record,
                    condition,
                    label_judge,
                    records=records,
                    memory_snapshot=memory_snapshot or [],
                )
                detail["label_results"].append(label_result)
                for metric_name, contribution in label_result["metric_contributions"].items():
                    totals[metric_name]["numerator"] += int(bool(contribution.get("numerator")))
                    totals[metric_name]["denominator"] += int(contribution.get("denominator") or 0)
            if session_index == 3 and session_cases:
                regression = _score_regression_after_update(record)
                detail["metric_contributions"]["RegressionAfterUpdate"] = regression
                totals["RegressionAfterUpdate"]["numerator"] += int(bool(regression["numerator"]))
                totals["RegressionAfterUpdate"]["denominator"] += int(regression["denominator"])
            details.append(detail)

    aggregate_metrics = {
        name: _metric_payload(name, totals[name]["numerator"], totals[name]["denominator"])
        for name in HEADLINE_SLICE_METRICS
    }
    return {
        "scorer": "label_grounded_memory_trap_v2",
        "condition": condition,
        "aggregate": {
            "metrics": aggregate_metrics,
            "metric_independence": _metric_independence_notes(),
            "label_counts": dict(sorted(label_counts.items())),
            "sequence_count": len(sequence_records),
            "record_count": len(records),
            "memory_snapshot_count": len(memory_snapshot or []),
        },
        "details": details,
        "judge": {
            "llm_label_judge_hook": "provided" if label_judge else "not_configured",
            "fallback": "deterministic_patch_string_ast_oracle",
            "live_llm_used": bool(label_judge),
        },
    }


def headline_metric_values(report: Mapping[str, Any]) -> dict[str, Optional[float]]:
    """Extract plain metric values suitable for the existing run metric map."""

    metrics = ((report.get("aggregate") or {}).get("metrics") or {})
    return {
        name: metrics.get(name, {}).get("value")
        for name in HEADLINE_SLICE_METRICS
    }


def _metric_independence_notes() -> dict[str, dict[str, Any]]:
    return {
        "StaleMemoryActivationRate": {
            "status": "independent",
            "definition": "stale/superseded memory retrieved into active context and acted on",
        },
        "ContradictionRepairAccuracy": {
            "status": "independent",
            "definition": "pre-active repair or task-time repair reflected in active behavior without contradictory retrieval",
            "time_boundary": "prior_session_writes_only",
        },
        "RepeatedErrorRate": {
            "status": "independent",
            "definition": "same labeled error appears before the active session and recurs in the active patch",
        },
        "HumanFeedbackUseAccuracy": {
            "status": "independent",
            "definition": "useful in-scope feedback retrieved and reflected in the active patch",
        },
        "HarmfulMemoryRate": {
            "status": "independent",
            "definition": "harmful memory retrieved into active context and acted on",
        },
        "UsefulMemoryPrecision": {
            "status": "independent",
            "definition": "useful retrieved memories divided by all retrieved memories for human-feedback opportunities",
        },
        "ScopeAccuracy": {
            "status": "derived",
            "definition": "mixed aggregate of human-feedback use and harmful-memory scope behavior",
            "derived_from": ["HumanFeedbackUseAccuracy", "HarmfulMemoryRate"],
        },
        "TransferScore": {
            "status": "independent",
            "definition": "S3 patch uses useful in-scope memory with provenance before S3; not gated by final_passed",
        },
        "RegressionAfterUpdate": {
            "status": "independent",
            "definition": "failed S3 oracle output indicates regression of prior behavior after update",
        },
    }


def rescore_from_records(
    *,
    records: Any,
    sequence_records: Sequence[Mapping[str, Any]],
    condition: Optional[str] = None,
    memory_snapshot: Optional[Sequence[Mapping[str, Any]]] = None,
    label_judge: Optional[LabelJudge] = None,
) -> dict[str, Any]:
    """Re-score stored ``results.json`` records without re-running agents.

    ``records`` may be either the raw record list or a loaded results payload
    containing ``records`` plus optional manifest/condition metadata.
    """

    payload: Mapping[str, Any] = records if isinstance(records, Mapping) else {}
    record_list = payload.get("records") if payload else records
    if not isinstance(record_list, Sequence) or isinstance(record_list, (str, bytes)):
        record_list = []
    inferred_condition = condition or str(
        payload.get("condition")
        or (payload.get("manifest") if isinstance(payload.get("manifest"), Mapping) else {}).get("condition")
        or "rescored"
    )
    inferred_snapshot = memory_snapshot
    if inferred_snapshot is None and isinstance(payload.get("memory_snapshot"), Sequence):
        inferred_snapshot = payload.get("memory_snapshot")  # type: ignore[assignment]
    return score_memory_trap_slice(
        sequence_records=sequence_records,
        records=[record for record in record_list if isinstance(record, Mapping)],
        condition=inferred_condition,
        memory_snapshot=inferred_snapshot,
        label_judge=label_judge,
    )


def _label_cases(sequence_records: Sequence[Mapping[str, Any]]) -> list[_LabelCase]:
    cases: list[_LabelCase] = []
    for sequence in sequence_records:
        event_text = " ".join(str(event.get("content") or "") for event in sequence.get("events") or [] if isinstance(event, Mapping))
        scope = _merged_event_scope(sequence.get("events") or [])
        for label in sequence.get("oracle_labels") or []:
            if not isinstance(label, Mapping):
                continue
            active_session = int(label.get("active_session") or 0)
            if active_session <= 0:
                continue
            cases.append(
                _LabelCase(
                    sequence=sequence,
                    label=label,
                    active_session=active_session,
                    scope=scope,
                    event_text=event_text,
                )
            )
    return cases


def _merged_event_scope(events: Iterable[Any]) -> dict[str, Any]:
    files: list[str] = []
    symbols: list[str] = []
    repo = None
    sequence_id = None
    for event in events:
        if not isinstance(event, Mapping):
            continue
        scope = event.get("scope")
        if not isinstance(scope, Mapping):
            continue
        repo = repo or scope.get("repo")
        sequence_id = sequence_id or scope.get("sequence_id")
        files.extend(str(value) for value in scope.get("files") or [])
        symbols.extend(str(value) for value in scope.get("symbols") or [])
    return {
        "repo": repo,
        "sequence_id": sequence_id,
        "files": _dedupe(files),
        "symbols": _dedupe(symbols),
    }


def _records_by_key(records: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], Mapping[str, Any]]:
    by_key: dict[tuple[str, int], Mapping[str, Any]] = {}
    for record in records:
        task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
        seq_id = str(task.get("seq_id") or task.get("sequence_id") or record.get("sequence_id") or "")
        session_index = int(task.get("session_index") or record.get("session_index") or 0)
        if seq_id and session_index:
            by_key[(seq_id, session_index)] = record
    return by_key


def _empty_totals() -> dict[str, dict[str, int]]:
    return {name: {"numerator": 0, "denominator": 0} for name in HEADLINE_SLICE_METRICS}


def _base_detail(condition: str, seq_id: str, session_index: int, record: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    task = record.get("task") if isinstance(record, Mapping) and isinstance(record.get("task"), Mapping) else {}
    return {
        "condition": condition,
        "sequence_id": seq_id,
        "session_index": session_index,
        "task_id": task.get("id") or f"{seq_id}-s{session_index:02d}",
        "record_present": record is not None,
        "oracle_passed": bool(record.get("final_passed") if isinstance(record, Mapping) else False),
        "continuation_audit": _continuation_audit(record),
        "metric_contributions": {},
        "label_results": [],
    }


def _continuation_audit(record: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    record = record or {}
    return {
        "start_commit": record.get("start_commit"),
        "end_commit": record.get("end_commit"),
        "started_from_previous_session": record.get("started_from_previous_session"),
        "previous_session_end_commit": record.get("previous_session_end_commit"),
        "forbidden_fresh_base_ref_used": record.get("forbidden_fresh_base_ref_used"),
        "oracle_id": record.get("oracle_id"),
        "continuation_advanced": record.get("continuation_advanced"),
    }


def _score_label_case(
    case: _LabelCase,
    record: Optional[Mapping[str, Any]],
    condition: str,
    label_judge: Optional[LabelJudge],
    *,
    records: Sequence[Mapping[str, Any]],
    memory_snapshot: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    label = case.label
    label_type = str(label.get("type") or "")
    seq_id = _seq_id(case.sequence)
    patch = _production_diff(record)
    signals = _patch_signals(case, patch, record)
    memory = _memory_signals(case, record)
    contributions: dict[str, dict[str, Any]] = {}
    judge_notes: list[dict[str, Any]] = []

    if label_type == "stale":
        activated = memory["stale_or_harmful_retrieved"] and signals["stale_behavior"]
        contributions["StaleMemoryActivationRate"] = _contribution(activated, 1)
    elif label_type == "contradiction":
        maintenance_repair = _memory_maintenance_repair(case, record, records=records, memory_snapshot=memory_snapshot)
        task_repair = signals["correct_behavior"] and not memory["contradictory_retrieved"]
        repaired = task_repair or (
            maintenance_repair
            and signals["correct_behavior"]
            and not memory["contradictory_retrieved"]
        )
        judged = _judge_or_fallback(
            label_judge,
            "contradiction_repair",
            {"condition": condition, "sequence_id": seq_id, "label": dict(label), "signals": signals, "memory": memory},
            repaired,
        )
        judge_notes.append(judged)
        contributions["ContradictionRepairAccuracy"] = _contribution(bool(judged["answer"]), 1)
    elif label_type == "repeated-error-opportunity":
        repeated = _repeated_labeled_error_recurs(case, record, records)
        judged = _judge_or_fallback(
            label_judge,
            "repeated_named_bad_action",
            {"condition": condition, "sequence_id": seq_id, "label": dict(label), "patch": patch[:4000], "signals": signals},
            repeated,
        )
        judge_notes.append(judged)
        contributions["RepeatedErrorRate"] = _contribution(bool(judged["answer"]), 1)
    elif label_type == "human-feedback":
        follows = memory["useful_retrieved_in_scope"] and (
            signals["uses_required_contract"] or signals["correct_behavior"]
        )
        judged = _judge_or_fallback(
            label_judge,
            "patch_and_memory_follow_feedback",
            {"condition": condition, "sequence_id": seq_id, "label": dict(label), "signals": signals, "memory": memory},
            follows,
        )
        judge_notes.append(judged)
        answer = bool(judged["answer"])
        contributions["HumanFeedbackUseAccuracy"] = _contribution(answer, 1)
        contributions["UsefulMemoryPrecision"] = {
            "numerator": int(memory["useful_retrieved_count"]),
            "denominator": int(memory["retrieved_count"]),
        }
        contributions["ScopeAccuracy"] = _contribution(answer, 1)
        transfer = (
            case.active_session == 3
            and memory["useful_prior_retrieved_in_scope"]
            and (signals["uses_required_contract"] or answer)
        )
        contributions["TransferScore"] = _contribution(transfer, 1)
    elif label_type == "harmful-retrieval":
        harmful = memory["harmful_retrieved"] and signals["bad_action"]
        contributions["HarmfulMemoryRate"] = _contribution(harmful, 1)
        scope_correct = not harmful
        if memory["harmful_retrieved"]:
            scope_correct = memory["harmful_retrieved_in_scope"] and not signals["bad_action"]
        contributions["ScopeAccuracy"] = _contribution(scope_correct, 1)

    return {
        "label_id": label.get("label_id"),
        "label_type": label_type,
        "active_session": case.active_session,
        "metric_contributions": contributions,
        "deterministic_signals": signals,
        "memory_signals": memory,
        "judge_notes": judge_notes,
    }


def _memory_signals(case: _LabelCase, record: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    contexts = _memory_contexts(record)
    label = case.label
    stale_text = str(label.get("stale_fact") or "")
    old_text = str(label.get("old_memory") or "")
    harmful_text = str(label.get("harmful_memory") or "")
    required_text = str(label.get("required_memory") or label.get("active_fact") or label.get("new_memory") or "")
    event_text = case.event_text

    stale_or_harmful = []
    stale_fact_asserted = []
    harmful = []
    useful = []
    useful_prior_in_scope = []
    contradictory = []
    for item in contexts:
        content = _memory_content(item)
        item_id = str(item.get("id") or "")
        id_matches = bool(label.get("harmful_memory_id") and item_id == str(label.get("harmful_memory_id")))
        stale_match = bool(stale_text and _text_asserts_fact(content, stale_text))
        old_match = bool(old_text and _text_asserts_fact(content, old_text))
        harmful_match = bool(harmful_text and _text_asserts_fact(content, harmful_text))
        required_match = bool(required_text and _text_matches(content, required_text))
        event_match = bool(event_text and _text_matches(content, event_text, threshold=0.18))
        inactive = _is_inactive_memory(item)

        if stale_match:
            stale_fact_asserted.append(item_id)
        if id_matches or stale_match or old_match or harmful_match or inactive:
            stale_or_harmful.append(item_id)
        if id_matches or harmful_match or (inactive and (stale_match or old_match)):
            harmful.append(item_id)
        if required_match or event_match:
            useful.append(item_id)
            if _memory_item_has_prior_session_provenance(item, case):
                useful_prior_in_scope.append(item_id)
        if old_match:
            contradictory.append(item_id)

    useful_in_scope = [
        item.get("id") for item in contexts
        if str(item.get("id") or "") in useful and _scope_matches_label(item, case)
    ]
    useful_prior_in_scope = [
        item.get("id") for item in contexts
        if str(item.get("id") or "") in useful_prior_in_scope and _scope_matches_label(item, case)
    ]
    harmful_in_scope = [
        item.get("id") for item in contexts
        if str(item.get("id") or "") in harmful and _scope_matches_label(item, case)
    ]
    return {
        "retrieved_count": len(contexts),
        "useful_retrieved_count": len(_dedupe(str(value) for value in useful)),
        "stale_fact_asserted": bool(stale_fact_asserted),
        "stale_or_harmful_retrieved": bool(stale_or_harmful),
        "harmful_retrieved": bool(harmful),
        "harmful_retrieved_in_scope": bool(harmful_in_scope),
        "useful_retrieved": bool(useful),
        "useful_retrieved_in_scope": bool(useful_in_scope),
        "useful_prior_retrieved_in_scope": bool(useful_prior_in_scope),
        "contradictory_retrieved": bool(contradictory),
        "matching_memory_ids": {
            "stale_fact_asserted": _dedupe(stale_fact_asserted),
            "stale_or_harmful": _dedupe(stale_or_harmful),
            "harmful": _dedupe(harmful),
            "useful": _dedupe(str(value) for value in useful),
            "useful_in_scope": _dedupe(str(value) for value in useful_in_scope),
            "useful_prior_in_scope": _dedupe(str(value) for value in useful_prior_in_scope),
        },
    }


def _memory_maintenance_repair(
    case: _LabelCase,
    record: Optional[Mapping[str, Any]],
    *,
    records: Sequence[Mapping[str, Any]] = (),
    memory_snapshot: Sequence[Mapping[str, Any]] = (),
) -> bool:
    label = case.label
    old_text = str(label.get("old_memory") or label.get("stale_fact") or "")
    new_text = str(label.get("new_memory") or label.get("active_fact") or label.get("required_memory") or "")
    if not old_text or not new_text:
        return False

    if _old_fact_inactive_in_snapshot(case, old_text, memory_snapshot):
        return True

    inactive_old = False
    active_new = False
    has_link = False
    for item in _repair_candidate_writes(case, record, records):
        if not _scope_matches_label(item, case):
            continue
        content = _memory_content(item)
        status = str(item.get("status") or "active").lower()
        if _text_matches(content, old_text) and _is_inactive_memory(item):
            inactive_old = True
        if _text_matches(content, new_text) and status == "active":
            active_new = True
        linked_ids = item.get("contradicts") or item.get("supersedes") or item.get("superseded_by") or []
        edge_text = " ".join(
            str(value)
            for key, value in item.items()
            if key in {"contradicts", "supersedes", "superseded_by", "repair_rationale", "rationale", "write_reason"}
        )
        if linked_ids or _text_matches(edge_text, "contradiction supersede stale scope rationale", threshold=0.1):
            has_link = True
    return inactive_old and active_new and has_link


def _repair_candidate_writes(
    case: _LabelCase,
    record: Optional[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    writes: list[Mapping[str, Any]] = []
    seen_record_ids: set[int] = set()
    for candidate_record in records:
        if not _record_matches_case(candidate_record, case):
            continue
        seen_record_ids.add(id(candidate_record))
        writes.extend(_memory_writes(candidate_record))
    if record is not None and id(record) not in seen_record_ids and _record_matches_case(record, case):
        writes.extend(_memory_writes(record))
    return writes


def _record_matches_case(record: Mapping[str, Any], case: _LabelCase) -> bool:
    task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
    seq_id = str(task.get("seq_id") or task.get("sequence_id") or record.get("sequence_id") or "")
    if seq_id != _seq_id(case.sequence):
        return False
    session_index = int(task.get("session_index") or record.get("session_index") or 0)
    return 0 < session_index < case.active_session


def _old_fact_inactive_in_snapshot(
    case: _LabelCase,
    old_text: str,
    memory_snapshot: Sequence[Mapping[str, Any]],
) -> bool:
    for item in memory_snapshot:
        if not isinstance(item, Mapping) or not _scope_matches_label(item, case):
            continue
        if not _memory_item_status_available_before_case(item, case):
            continue
        if _is_inactive_memory(item) and _text_matches(_memory_content(item), old_text):
            return True
    return False


def _memory_item_has_prior_session_provenance(item: Mapping[str, Any], case: _LabelCase) -> bool:
    indexes = _memory_item_session_indices(item)
    return bool(indexes) and min(indexes) < case.active_session


def _memory_item_status_available_before_case(item: Mapping[str, Any], case: _LabelCase) -> bool:
    status_indexes = [
        _as_positive_int(item.get(key))
        for key in (
            "status_session_index",
            "updated_session_index",
            "last_modified_session_index",
            "session_index",
            "created_session_index",
        )
    ]
    status_indexes = [value for value in status_indexes if value is not None]
    if status_indexes:
        return max(status_indexes) < case.active_session
    return False


def _memory_item_session_indices(item: Mapping[str, Any]) -> list[int]:
    indexes: list[int] = []
    for key in ("session_index", "created_session_index", "source_session_index"):
        value = _as_positive_int(item.get(key))
        if value is not None:
            indexes.append(value)
    provenance = item.get("provenance")
    if isinstance(provenance, Mapping):
        for trajectory_id in provenance.get("trajectory_ids") or []:
            indexes.extend(_session_indices_from_text(str(trajectory_id)))
        for task_id in provenance.get("task_ids") or []:
            indexes.extend(_session_indices_from_text(str(task_id)))
    for task_scope in item.get("task_scope") or []:
        indexes.extend(_session_indices_from_text(str(task_scope)))
    return sorted(set(indexes))


def _session_indices_from_text(text: str) -> list[int]:
    indexes: list[int] = []
    for match in re.finditer(r"(?:^|[-_])s0*([1-9]\d*)(?:$|[-_])", text):
        value = _as_positive_int(match.group(1))
        if value is not None:
            indexes.append(value)
    return indexes


def _as_positive_int(value: Any) -> int | None:
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def _repeated_labeled_error_recurs(
    case: _LabelCase,
    record: Optional[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> bool:
    if record is None:
        return False
    active_signals = _patch_signals(case, _production_diff(record), record)
    if not active_signals["bad_action"]:
        return False
    return any(_labeled_error_present(case, candidate) for candidate in records if _record_matches_case(candidate, case))


def _labeled_error_present(case: _LabelCase, record: Mapping[str, Any]) -> bool:
    patch = _production_diff(record)
    if not patch.strip():
        return False
    signals = _patch_signals(case, patch, record)
    if signals["bad_action"] or signals["stale_behavior"]:
        return True
    generic = _generic_contract_signals(case, patch, record)
    return bool(generic["contract_violation"])


def _patch_signals(case: _LabelCase, patch: str, record: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    seq_id = _seq_id(case.sequence)
    label_type = str(case.label.get("type") or "")
    text = _normalize_text(patch)
    files = set(_patch_files(patch))
    ast_info = _patch_ast_info(patch)
    strings = " ".join(ast_info["strings"]).lower()
    names = set(ast_info["names"])
    oracle_passed = bool(record and record.get("final_passed"))
    correct = False
    bad = False
    stale = False

    if seq_id == "config-stale-merge":
        shallow_update = "dict.update" in text or ".update(" in text and "deep_update" not in text
        returns_config = "return config" in text or "config(" in text or "Config" in names
        correct = ("merge_configs" in text or "merge_configs" in names) and returns_config and not shallow_update
        bad = ("merge_configs" in text or "merge_configs" in names) and shallow_update and not returns_config
        stale = bad or (("merge_configs" in text or "merge_configs" in names) and "return {" in text)
    elif seq_id == "todo-convention-aggregate":
        correct = "stats|open=" in text or "STATS|open=" in strings
        bad = "ok|stats" in text or ("stats" in text and "ok|" in text and "stats|open=" not in text)
        stale = bad
    elif seq_id == "config-reviewer-strict-csv":
        correct = "csv_width" in text or "CSV_WIDTH" in strings
        bad = "parse_csv" in text and not correct and ("valueerror" in text or "parseerror" in text or "line " in text)
        stale = bad
    elif seq_id == "expr-generated-category":
        has_modulo = "modulo" in text or "%" in strings or "%" in text
        correct = has_modulo and "remainder" in text and (
            "generate_operator_help" in text or "operator_help_lines" in text or "tools/generate_operator_help.py" in files
        )
        bad = has_modulo and "category" in text and "arithmetic" in text and "remainder" not in text
        if "exprmini/operator_help.py" in files and "tools/generate_operator_help.py" not in files:
            bad = True
        stale = bad or (has_modulo and "category" not in text)
    elif seq_id == "todo-flaky-monotonic-ids":
        correct = "next_id" in text or "nextid" in text or "next id" in text
        id_recompute = ("max(" in text or "len(" in text) and "id" in text and not correct
        bad = ("purge" in text or "delete" in text or "filter" in text) and id_recompute
        stale = bad

    generic = _generic_contract_signals(case, patch, record)
    if not correct and generic["correct_behavior"]:
        correct = True
    if not bad and generic["bad_action"]:
        bad = True
    if not stale and generic["stale_behavior"]:
        stale = True

    if not correct and oracle_passed:
        correct = True
    uses_required_contract = bool(generic["has_required_contract"] or (correct and patch.strip()))
    return {
        "correct_behavior": bool(correct),
        "bad_action": bool(bad),
        "stale_behavior": bool(stale),
        "uses_required_contract": uses_required_contract,
        "oracle_passed": oracle_passed,
        "patch_files": sorted(files),
        "ast_function_names": sorted(name for name in names if not name.startswith("_"))[:20],
        "ast_string_literals": sorted(set(ast_info["strings"]))[:20],
        "generic_has_required_contract": generic["has_required_contract"],
        "generic_contract_violation": generic["contract_violation"],
        "generic_wrong_or_absent_contract": generic["wrong_or_absent_contract"],
        "generic_required_markers": generic["required_markers"],
        "generic_missing_required_markers": generic["missing_required_markers"],
        "generic_forbidden_markers": generic["forbidden_markers"],
        "generic_forbidden_markers_present": generic["forbidden_markers_present"],
        "generic_label_type": label_type,
    }


def _generic_contract_signals(
    case: _LabelCase,
    patch: str,
    record: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    oracle_passed = bool(record and record.get("final_passed"))
    session_index = _record_session_index(record)
    active_record = session_index in (0, case.active_session)
    required_markers = _required_contract_markers(case)
    forbidden_markers = _forbidden_contract_markers(case)
    missing_required = [
        marker for marker in required_markers
        if not _marker_in_text(patch, marker)
    ]
    forbidden_present = [
        marker for marker in forbidden_markers
        if _marker_in_text(patch, marker)
    ]
    has_required_contract = bool(required_markers) and not missing_required
    failed_patch = not oracle_passed
    failed_active_patch = bool(active_record and failed_patch)
    label_type = str(case.label.get("type") or "")
    contract_violation = failed_patch and (
        bool(missing_required) or (not required_markers and bool(forbidden_present))
    )
    wrong_or_absent_contract = active_record and contract_violation

    bad_action = wrong_or_absent_contract and label_type in {
        "harmful-retrieval",
        "repeated-error-opportunity",
    }
    stale_behavior = wrong_or_absent_contract and label_type == "stale"
    return {
        "correct_behavior": bool(oracle_passed and (has_required_contract or not required_markers)),
        "bad_action": bool(bad_action),
        "stale_behavior": bool(stale_behavior),
        "has_required_contract": bool(has_required_contract),
        "contract_violation": bool(contract_violation),
        "wrong_or_absent_contract": bool(wrong_or_absent_contract),
        "required_markers": required_markers,
        "missing_required_markers": missing_required,
        "forbidden_markers": forbidden_markers,
        "forbidden_markers_present": forbidden_present,
    }


def _required_contract_markers(case: _LabelCase) -> list[str]:
    label = case.label
    texts = [
        str(label.get("required_memory") or ""),
        str(label.get("active_fact") or ""),
        str(label.get("new_memory") or ""),
        str(label.get("bad_action") or ""),
        case.event_text,
    ]
    markers: list[str] = []
    for text in texts:
        markers.extend(_extract_contract_markers(text))
    return _dedupe(markers)


def _forbidden_contract_markers(case: _LabelCase) -> list[str]:
    label = case.label
    texts = [
        str(label.get("bad_action") or ""),
        str(label.get("harmful_memory") or ""),
        str(label.get("stale_fact") or ""),
        str(label.get("old_memory") or ""),
    ]
    markers: list[str] = []
    for text in texts:
        markers.extend(_extract_contract_markers(text, include_fallback_terms=False))
    return _dedupe(markers)


def _extract_contract_markers(text: str, *, include_fallback_terms: bool = True) -> list[str]:
    if not text:
        return []
    markers: list[str] = []
    for pattern in (r"`([^`]{2,160})`", r"\"([^\"\n]{2,160})\"", r"'([^'\n]{2,160})'"):
        for quoted in re.findall(pattern, text):
            markers.extend(_marker_fragments(str(quoted)))

    marker_patterns = (
        r"\b[A-Z][A-Z0-9_]{2,}(?:[-_][A-Z0-9]+)*\b",
        r"\b[A-Za-z]+-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b",
        r"\b[a-z]{2,}[a-z0-9]*-[0-9a-z][a-z0-9-]*\b",
        r"\b[a-z_]+=[A-Za-z0-9_<>{}-]+\b",
        r"\b[A-Za-z_]*[a-z][A-Za-z0-9_]*_[A-Za-z0-9_]*\d[A-Za-z0-9_]*\b",
    )
    for pattern in marker_patterns:
        markers.extend(match.group(0) for match in re.finditer(pattern, text))

    if include_fallback_terms and not markers:
        markers.extend(_fallback_contract_terms(text))
    return _dedupe(marker for marker in markers if _useful_contract_marker(marker))


def _marker_fragments(value: str) -> list[str]:
    value = value.strip()
    fragments = [value]
    fragments.extend(match.group(0) for match in re.finditer(r"\b[A-Za-z0-9_]+(?:[-=][A-Za-z0-9_<>{}]+)+\b", value))
    fragments.extend(match.group(0) for match in re.finditer(r"\b[A-Z][A-Z0-9_]{2,}\b", value))
    return fragments


def _fallback_contract_terms(text: str) -> list[str]:
    terms: list[str] = []
    preferred = {
        "casefold",
        "descending",
        "floordiv",
        "lowest",
        "monotonic",
        "next_id",
        "quotient",
        "stability",
    }
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text):
        normalized = token.lower()
        if normalized in preferred or "_" in token or any(char.isdigit() for char in token):
            terms.append(token)
    return terms[:6]


def _useful_contract_marker(marker: str) -> bool:
    marker = marker.strip(".,:;()[]{}")
    if len(marker) < 3:
        return False
    lowered = marker.lower()
    if lowered in _STOPWORDS or lowered in {
        "line",
        "name",
        "title",
        "value",
        "key",
        "status",
        "token",
        "marker",
        "contract",
        "exact",
        "message",
        "reviewer",
        "format",
        "command",
        "without",
    }:
        return False
    return True


def _marker_in_text(text: str, marker: str) -> bool:
    marker = marker.strip()
    if not marker:
        return False
    normalized_text = _normalize_text(text)
    normalized_marker = _normalize_text(marker)
    if normalized_marker in normalized_text:
        return True
    compact_text = re.sub(r"\s+", "", normalized_text)
    compact_marker = re.sub(r"\s+", "", normalized_marker)
    return bool(compact_marker and compact_marker in compact_text)


def _record_session_index(record: Optional[Mapping[str, Any]]) -> int:
    if not record:
        return 0
    task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
    try:
        return int(task.get("session_index") or record.get("session_index") or 0)
    except (TypeError, ValueError):
        return 0


def _score_regression_after_update(record: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if record is None:
        return _contribution(False, 1)
    if bool(record.get("final_passed")):
        return _contribution(False, 1)
    oracle = record.get("oracle") if isinstance(record.get("oracle"), Mapping) else {}
    output = f"{oracle.get('stdout') or ''}\n{oracle.get('stderr') or ''}".lower()
    markers = ("regress", "prior", "unchanged", "still", "preserve", "survive", "generated_from_source")
    return _contribution(any(marker in output for marker in markers), 1)


def _judge_or_fallback(
    label_judge: Optional[LabelJudge],
    question: str,
    payload: Mapping[str, Any],
    fallback_answer: bool,
) -> dict[str, Any]:
    judge_input = {
        "question": question,
        "payload": dict(payload),
        "fallback_answer": bool(fallback_answer),
    }
    if label_judge is None:
        return {
            "question": question,
            "source": "deterministic_fallback",
            "answer": bool(fallback_answer),
            "raw": None,
        }
    raw = label_judge(judge_input)
    return {
        "question": question,
        "source": "llm_label_judge_hook",
        "answer": bool(raw.get("answer")),
        "raw": dict(raw),
    }


def _contribution(numerator: bool, denominator: int) -> dict[str, Any]:
    return {"numerator": 1 if numerator else 0, "denominator": int(denominator)}


def _metric_payload(name: str, numerator: int, denominator: int) -> dict[str, Any]:
    value = None if denominator == 0 else float(numerator) / float(denominator)
    return {
        "name": name,
        "numerator": int(numerator),
        "denominator": int(denominator),
        "value": value,
    }


def _sessions(sequence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [session for session in sequence.get("sessions") or [] if isinstance(session, Mapping)]


def _seq_id(sequence: Mapping[str, Any]) -> str:
    return str(sequence.get("seq_id") or sequence.get("sequence_id") or "")


def _production_diff(record: Optional[Mapping[str, Any]]) -> str:
    if not record:
        return ""
    score = record.get("score")
    if isinstance(score, Mapping) and score.get("production_diff") is not None:
        return str(score.get("production_diff") or "")
    agent_result = record.get("agent_result")
    if isinstance(agent_result, Mapping):
        return str(agent_result.get("patch") or "")
    return ""


def _memory_contexts(record: Optional[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if not record:
        return []
    contexts = record.get("memory_context")
    if not isinstance(contexts, list):
        return []
    return [item for item in contexts if isinstance(item, Mapping)]


def _memory_writes(record: Optional[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if not record:
        return []
    writes = record.get("memory_writes")
    if not isinstance(writes, list):
        return []
    return [item for item in writes if isinstance(item, Mapping)]


def _memory_content(item: Mapping[str, Any]) -> str:
    parts = [str(item.get("content") or "")]
    provenance = item.get("provenance")
    if isinstance(provenance, Mapping):
        parts.extend(str(value) for value in provenance.values())
    parts.extend(str(value) for value in item.get("retrieval_tags") or [])
    return " ".join(parts)


def _scope_matches_label(item: Mapping[str, Any], case: _LabelCase) -> bool:
    scope = case.scope
    seq_id = _seq_id(case.sequence)
    repo_scope = item.get("repo_scope") or item.get("sequence_id")
    if repo_scope and str(repo_scope) not in {seq_id, str(scope.get("sequence_id") or "")}:
        return False
    label_files = set(str(value) for value in scope.get("files") or [])
    item_files = set(str(value) for value in item.get("file_scope") or item.get("files") or [])
    if label_files and item_files and not (label_files & item_files):
        return False
    return True


def _is_inactive_memory(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status") or "active").lower()
    try:
        staleness_score = float(item.get("staleness_score") or 0.0)
    except (TypeError, ValueError):
        staleness_score = 0.0
    return status in _INACTIVE_MEMORY_STATUSES or staleness_score >= 0.95


def _text_asserts_fact(haystack: str, needle: str) -> bool:
    hay = _normalize_text(haystack)
    need = _normalize_text(needle)
    if not hay or not need:
        return False
    if need in hay:
        return True
    quoted = re.findall(r"`([^`]+)`|\"([^\"]+)\"", str(needle or ""))
    for left, right in quoted:
        phrase = _normalize_text(left or right)
        if phrase and phrase in hay:
            return True
    return False


def _text_matches(haystack: str, needle: str, *, threshold: float = 0.28) -> bool:
    hay = _normalize_text(haystack)
    need = _normalize_text(needle)
    if not hay or not need:
        return False
    if need in hay:
        return True
    quoted = re.findall(r"`([^`]+)`|\"([^\"]+)\"", needle)
    for left, right in quoted:
        phrase = _normalize_text(left or right)
        if phrase and phrase in hay:
            return True
    hay_tokens = set(_tokens(hay))
    need_tokens = [token for token in _tokens(need) if token not in _STOPWORDS]
    if not need_tokens:
        return False
    overlap = sum(1 for token in need_tokens if token in hay_tokens)
    return overlap >= max(2, int(len(need_tokens) * threshold))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _patch_files(patch: str) -> list[str]:
    files: list[str] = []
    for match in re.finditer(r"^diff --git a/(.*?) b/(.*?)$", patch, flags=re.MULTILINE):
        files.append(match.group(2))
    return _dedupe(files)


def _patch_ast_info(patch: str) -> dict[str, list[str]]:
    by_file: dict[str, list[str]] = defaultdict(list)
    current_file = ""
    for line in patch.splitlines():
        header = re.match(r"^diff --git a/(.*?) b/(.*?)$", line)
        if header:
            current_file = header.group(2)
            continue
        if not current_file.endswith(".py"):
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            by_file[current_file].append(line[1:])

    names: set[str] = set()
    strings: set[str] = set()
    for added_lines in by_file.values():
        source = textwrap.dedent("\n".join(added_lines))
        if not source.strip():
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
    return {"names": sorted(names), "strings": sorted(strings)}


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            output.append(value)
            seen.add(value)
    return output
