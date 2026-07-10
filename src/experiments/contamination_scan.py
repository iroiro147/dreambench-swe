"""Post-run contamination scanner for DreamBench-SWE records.

The scanner is intentionally empirical: it does not delete raw episodes, but it
flags records whose stored agent transcript shows hidden benchmark-material
reads or searches.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from dream_memory import slice_scorer


DEFAULT_HIDDEN_PATH_PATTERNS = (
    r"experiments/env/oracles(?:/|\b)",
    r"experiments/env/refsol(?:/|\b)",
    r"<REVIEWER_ONLY_ORACLES>(?:/|\b)",
    r"<REVIEWER_ONLY_REFSOL>(?:/|\b)",
    r"experiments/env/sequences\.jsonl\b",
    r"experiments/secrets\.env\b",
    r"(?:^|[\s\"'])/[^\"'\s]*/logs/codex/[^\"'\s]+\.log\b",
    r"(?:^|[\s\"'])[^\"'\s]*/logs/codex/[^\"'\s]+\.log\b",
    r"<REVIEWER_ONLY_REFSOL>/[^\"'\s]+\.py-diff\b",
    r"(?:^|[\s\"'])[^\"'\s]+\.py-diff\b",
)

_PATCH_KEYS = {
    "patch",
    "diff",
    "file_diffs",
    "production_diff",
    "response_excerpt",
}
_HARNESS_INPUT_KEYS = {
    "argv",
    "command",
    "prompt",
    "instruction",
    "observations",
    "failure_observations",
    "memory_context",
    "memory_reads",
    "memory_writes",
    "memory_snapshot",
    "retrieval_decisions",
    "injected_memory_event",
    "injected_memory_events",
}
_SCORER_KEYS = {"oracle", "score"}
_MAX_EVIDENCE = 20


def hidden_markers_for_sequence(sequence: Mapping[str, Any]) -> list[str]:
    """Return high-signal hidden contract markers using slice_scorer's parser."""

    markers: list[str] = []
    for case in slice_scorer._label_cases([sequence]):  # noqa: SLF001 - deliberate scorer coupling.
        markers.extend(slice_scorer._required_contract_markers(case))  # noqa: SLF001
        markers.extend(slice_scorer._forbidden_contract_markers(case))  # noqa: SLF001
    return _dedupe(marker for marker in markers if _looks_hidden_marker(marker))


def scan_record(
    record: Mapping[str, Any],
    sequence: Mapping[str, Any],
    *,
    hidden_markers: Sequence[str] | None = None,
    hidden_path_patterns: Sequence[str] | None = None,
) -> dict[str, Any]:
    markers = list(hidden_markers) if hidden_markers is not None else hidden_markers_for_sequence(sequence)
    path_patterns = list(hidden_path_patterns or DEFAULT_HIDDEN_PATH_PATTERNS)
    evidence: list[dict[str, Any]] = []
    memory_context_text = _memory_context_text(record.get("memory_context"))
    has_hidden_path = False
    has_unexplained_marker = False

    for field, text in _agent_surfaces(record):
        if not text:
            continue
        path_text = _path_surface_text(field, text)
        marker_text = _marker_surface_text(field, text)
        field_has_hidden_path = False
        for pattern in path_patterns:
            match = re.search(pattern, path_text)
            if match:
                has_hidden_path = True
                field_has_hidden_path = True
                _append_evidence(
                    evidence,
                    {
                        "type": "hidden_path",
                        "field": field,
                        "match": match.group(0).strip(),
                        "excerpt": _excerpt(path_text, match.start(), match.end()),
                    },
                )
        for marker in markers:
            index = marker_text.find(marker)
            if index >= 0 and _marker_context_contaminating(marker_text, marker, index, field_has_hidden_path):
                memory_sourced = marker in memory_context_text
                if not memory_sourced:
                    has_unexplained_marker = True
                _append_evidence(
                    evidence,
                    {
                        "type": "hidden_marker",
                        "field": field,
                        "match": marker,
                        "memory_sourced": memory_sourced,
                        "excerpt": _excerpt(marker_text, index, index + len(marker)),
                    },
                )
    return {"contaminated": has_hidden_path or has_unexplained_marker, "evidence": evidence}


def scan_results(
    records: Sequence[Mapping[str, Any]],
    sequence_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sequences = {
        str(sequence.get("seq_id") or sequence.get("sequence_id") or ""): sequence
        for sequence in sequence_records
        if isinstance(sequence, Mapping)
    }
    contaminated_keys: list[tuple[str, int]] = []
    per_record: dict[str, dict[str, Any]] = {}
    marker_cache: dict[str, list[str]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        seq_id = _sequence_id(record)
        session_index = _session_index(record)
        sequence = sequences.get(seq_id)
        if sequence is None:
            result = {"contaminated": False, "evidence": []}
        else:
            markers = marker_cache.setdefault(seq_id, hidden_markers_for_sequence(sequence))
            result = scan_record(record, sequence, hidden_markers=markers, hidden_path_patterns=DEFAULT_HIDDEN_PATH_PATTERNS)
        key = f"{seq_id}:S{session_index}"
        per_record[key] = result
        if result.get("contaminated"):
            contaminated_keys.append((seq_id, session_index))
    return {"contaminated_keys": contaminated_keys, "per_record": per_record}


def _agent_surfaces(record: Mapping[str, Any]) -> list[tuple[str, str]]:
    surfaces: list[tuple[str, str]] = []
    _collect_surfaces(record.get("agent_metadata"), "agent_metadata", surfaces)
    _collect_surfaces(record.get("trajectory"), "trajectory", surfaces)
    _collect_surfaces(record.get("agent_result"), "agent_result", surfaces)
    return surfaces


def _collect_surfaces(value: Any, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(value, str):
        if _include_text_path(path):
            out.append((path, value))
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_s = str(key)
            if key_s in _PATCH_KEYS or key_s in _HARNESS_INPUT_KEYS or key_s in _SCORER_KEYS:
                continue
            _collect_surfaces(item, f"{path}.{key_s}", out)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for index, item in enumerate(value):
            _collect_surfaces(item, f"{path}[{index}]", out)


def _include_text_path(path: str) -> bool:
    lowered = path.lower()
    if any(f".{key}" in lowered or lowered.endswith(key) for key in _PATCH_KEYS):
        return False
    if ".raw_episode.prompt" in lowered or ".metadata.argv" in lowered:
        return False
    return any(
        term in lowered
        for term in (
            "stdout",
            "stderr",
            "observation",
            "tool_input",
            "tool_output",
            "transcript",
            "history",
        )
    )


def _path_surface_text(field: str, text: str) -> str:
    if field == "agent_metadata.stderr":
        return _strip_codex_prompt(text)
    return text


def _marker_surface_text(field: str, text: str) -> str:
    if field == "agent_metadata.stdout":
        return ""
    normalized = _strip_codex_prompt(text) if field == "agent_metadata.stderr" else text
    return _strip_patch_blocks(normalized)


def _strip_codex_prompt(text: str) -> str:
    for marker in ("\ncodex\n", "\nexec\n", "\napply patch\n"):
        index = text.find(marker)
        if index >= 0:
            return text[index + 1 :]
    return text


def _strip_patch_blocks(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("diff --git "):
            skipping = True
            continue
        if skipping:
            if line in {"codex", "exec", "apply patch"} or line.startswith("tokens used"):
                skipping = False
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out)


def _memory_context_text(memory_context: Any) -> str:
    if memory_context is None:
        return ""
    if isinstance(memory_context, str):
        return memory_context
    try:
        import json

        return json.dumps(memory_context, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(memory_context)


def _marker_context_contaminating(text: str, marker: str, index: int, field_has_hidden_path: bool) -> bool:
    if field_has_hidden_path:
        return True
    line_start = text.rfind("\n", 0, index) + 1
    line_end = text.find("\n", index + len(marker))
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].lower()
    command_terms = (
        "rg ",
        "grep ",
        "cat ",
        "sed ",
        "find ",
        "awk ",
        "python",
        "open(",
        "read_text",
        "/private/tmp",
        "/private/var",
        "$tmpdir",
        "logs/codex",
    )
    return any(term in line for term in command_terms)


def _looks_hidden_marker(marker: str) -> bool:
    marker = str(marker or "").strip()
    if len(marker) < 5:
        return False
    lowered = marker.lower()
    if lowered in {"task", "csv", "cli", "export", "quote_all"}:
        return False
    has_digit = any(char.isdigit() for char in marker)
    has_separator = any(char in marker for char in "-_=~<>|:;")
    has_mixed_case = any(char.islower() for char in marker) and any(char.isupper() for char in marker)
    return bool((has_digit and (has_separator or has_mixed_case)) or ("~" in marker and ";" in marker))


def _append_evidence(evidence: list[dict[str, Any]], item: dict[str, Any]) -> None:
    if len(evidence) >= _MAX_EVIDENCE:
        return
    key = (item.get("type"), item.get("field"), item.get("match"))
    if any((old.get("type"), old.get("field"), old.get("match")) == key for old in evidence):
        return
    evidence.append(item)


def _excerpt(text: str, start: int, end: int, *, radius: int = 160) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    excerpt = text[left:right].replace("\n", "\\n")
    return _redact_secret_like(excerpt)


def _redact_secret_like(text: str) -> str:
    redacted = re.sub(r"(?i)(api[_-]?key|token|secret|authorization)(=|:)[^\s\"']+", r"\1\2[redacted]", text)
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [redacted]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-[redacted]", redacted)
    return redacted


def _sequence_id(record: Mapping[str, Any]) -> str:
    task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
    return str(task.get("sequence_id") or task.get("seq_id") or record.get("sequence_id") or "")


def _session_index(record: Mapping[str, Any]) -> int:
    task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
    try:
        return int(task.get("session_index") or record.get("session_index") or 0)
    except (TypeError, ValueError):
        return 0


def _dedupe(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        marker = str(value or "").strip(".,:;()[]{}")
        if not marker or marker in seen:
            continue
        out.append(marker)
        seen.add(marker)
    return out


__all__ = [
    "DEFAULT_HIDDEN_PATH_PATTERNS",
    "hidden_markers_for_sequence",
    "scan_record",
    "scan_results",
]
