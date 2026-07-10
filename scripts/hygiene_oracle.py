#!/usr/bin/env python3
"""Judge-free hygiene oracle for DreamBench-SWE v2 traps.

The oracle consumes hidden trap metadata plus an agent-produced diff/answer. It
does not call an LLM judge. Commission checks are treated as forbidden patterns
whose presence means the agent imported stale, out-of-scope, or irrelevant
memory. Omission checks are treated as required patterns whose absence means the
agent failed to use a required memory. Structured checks can override those
defaults explicitly.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


PRIMARY_HYGIENE_METRICS = (
    "RepeatedErrorRate",
    "StaleUseRate",
    "OverscopeRate",
    "IrrelevantImportRate",
)

_CONSTRUCT_METRIC_MAP = {
    "C2": ("IrrelevantImportRate",),
    "C3": ("StaleUseRate",),
    "C5": ("OverscopeRate",),
    "C9": ("RepeatedErrorRate",),
    "C10": ("IrrelevantImportRate",),
}

_METRIC_ALIASES = {
    "repeated": "RepeatedErrorRate",
    "repeatederror": "RepeatedErrorRate",
    "repeatederrorrate": "RepeatedErrorRate",
    "repeatederrorrateanalog": "RepeatedErrorRate",
    "stale": "StaleUseRate",
    "staleuse": "StaleUseRate",
    "staleuserate": "StaleUseRate",
    "overscope": "OverscopeRate",
    "overscoperate": "OverscopeRate",
    "scope": "OverscopeRate",
    "irrelevant": "IrrelevantImportRate",
    "irrelevantimport": "IrrelevantImportRate",
    "irrelevantimportrate": "IrrelevantImportRate",
    "abstain": "IrrelevantImportRate",
}

_METRIC_KEYWORDS = (
    ("RepeatedErrorRate", ("c9", "repeated", "spurious", "false-lesson", "flaky")),
    ("StaleUseRate", ("c3", "stale", "supersede", "supersession", "anti-recency")),
    ("OverscopeRate", ("c5", "scope", "out-of-scope", "overscope")),
    ("IrrelevantImportRate", ("c2", "c10", "irrelevant", "abstain", "retrieval-interference")),
)

_META_KEYS = {
    "seq_id",
    "sequence_id",
    "trap_id",
    "id",
    "construct",
    "construct_id",
    "construct_label",
    "seq_type",
    "sequence_type",
    "commission_checks",
    "omission_checks",
    "decisive_oracle_literals",
    "required_fact_ids",
    "event_fact_map",
    "metric",
    "metrics",
    "metric_family",
    "metric_families",
    "hygiene_metric",
    "hygiene_metrics",
}

_OBSERVED_KEYS = (
    "produced_diff",
    "production_diff",
    "diff",
    "patch",
    "answer",
    "agent_answer",
    "agent_output",
    "response",
    "final_answer",
    "stdout",
    "stderr",
)

_STRUCTURED_OBSERVED_PATHS = (
    ("score", "production_diff"),
    ("score", "stdout"),
    ("score", "stderr"),
    ("oracle", "stdout"),
    ("oracle", "stderr"),
    ("agent_result", "patch"),
    ("agent_result", "answer"),
    ("agent_result", "stdout"),
    ("agent_result", "stderr"),
    ("result", "patch"),
    ("result", "answer"),
)

_NEGATED_EXPECTATION_RE = re.compile(
    r"\b("
    r"does\s+not|do\s+not|must\s+not|should\s+not|not\s+contain|not\s+emit|"
    r"no\s+|absent|without|forbid(?:den)?|reject(?:ed|s)?|avoid"
    r")\b",
    re.IGNORECASE,
)
_SECRET_LITERAL_RE = re.compile(
    r"\b(?:[A-Z][A-Z0-9]{1,}|[A-Z][A-Z0-9]*[a-z][A-Za-z0-9]*)"
    r"(?:[-_][A-Za-z0-9]+){1,4}\b"
)
_QUOTED_LITERAL_RE = re.compile(r"['\"]([^'\"]{3,})['\"]")


@dataclass(frozen=True)
class PatternCheck:
    raw: Any
    origin: str
    patterns: tuple[str, ...]
    is_regex: bool
    trip_on: str
    failure_kind: str
    match_mode: str


def score_trap(
    metadata: Mapping[str, Any],
    *,
    produced_diff: str = "",
    answer: str = "",
    record: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Score one trap's commission/omission booleans from metadata and output."""

    meta = _extract_hygiene_metadata(metadata)
    observed_text = _observed_text(record or metadata, produced_diff=produced_diff, answer=answer)
    checks = [
        *_normalize_checks(meta.get("commission_checks"), origin="commission"),
        *_normalize_checks(meta.get("omission_checks"), origin="omission"),
    ]
    evaluations = [_evaluate_check(check, observed_text) for check in checks]
    commission_matches = [item for item in evaluations if item["tripped"] and item["failure_kind"] == "commission"]
    omission_matches = [item for item in evaluations if item["tripped"] and item["failure_kind"] == "omission"]
    construct = _construct_label(meta, metadata)
    metric_families = _metric_families(meta, construct=construct, seq_id=_sequence_id(meta, metadata))
    sequence_id = _sequence_id(meta, metadata)
    tripped_commission = bool(commission_matches)
    tripped_omission = bool(omission_matches)
    return {
        "sequence_id": sequence_id,
        "trap_id": str(meta.get("trap_id") or sequence_id),
        "construct": construct,
        "seq_type": str(meta.get("seq_type") or meta.get("sequence_type") or ""),
        "metric_families": metric_families,
        "tripped_commission": tripped_commission,
        "tripped_omission": tripped_omission,
        "tripped": bool(tripped_commission or tripped_omission),
        "checked_commission": sum(1 for check in checks if check.origin == "commission"),
        "checked_omission": sum(1 for check in checks if check.origin == "omission"),
        "commission_matches": commission_matches,
        "omission_matches": omission_matches,
        "check_results": evaluations,
    }


def score_hygiene_records(
    records: Any,
    *,
    sequence_records: Optional[Sequence[Mapping[str, Any]]] = None,
    condition: Optional[str] = None,
) -> dict[str, Any]:
    """Score a collection of stored run records against hidden sequence metadata."""

    record_list = _as_records(records)
    sequence_by_id = {
        _sequence_id(_extract_hygiene_metadata(sequence), sequence): sequence
        for sequence in (sequence_records or [])
        if isinstance(sequence, Mapping)
    }
    details: list[dict[str, Any]] = []
    for record in record_list:
        if not isinstance(record, Mapping):
            continue
        record_meta = _extract_hygiene_metadata(record)
        seq_id = _sequence_id(record_meta, record)
        sequence = sequence_by_id.get(seq_id, {})
        merged_meta = _merged_metadata(sequence, record)
        details.append(score_trap(merged_meta, record=record))

    aggregate = _aggregate(details)
    report = {
        "scorer": "hidden_oracle_hygiene_v1",
        "condition": condition or _infer_condition(records),
        "judge": {
            "llm_used": False,
            "mode": "judge_free_hidden_oracle_patterns",
        },
        "aggregate": aggregate,
        "details": details,
    }
    report["latex"] = render_latex(report)
    return report


def render_latex(report: Mapping[str, Any]) -> str:
    """Render primary hygiene metrics as a compact LaTeX table."""

    metrics = ((report.get("aggregate") or {}).get("metrics") or {})
    lines = [
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"Metric & Tripped & Opportunities & Rate \\",
        r"\midrule",
    ]
    for name in PRIMARY_HYGIENE_METRICS:
        metric = metrics.get(name) or {}
        numerator = int(metric.get("numerator") or 0)
        denominator = int(metric.get("denominator") or 0)
        value = metric.get("value")
        rendered = "--" if value is None else f"{float(value):.3f}"
        lines.append(f"{_latex_escape(name)} & {numerator} & {denominator} & {rendered} \\\\")
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    return "\n".join(lines) + "\n"


def load_records(path: Path | str) -> list[dict[str, Any]]:
    """Load JSON, JSONL, or a directory of sequence JSON records."""

    path = Path(path)
    if path.is_dir():
        records: list[dict[str, Any]] = []
        sidecars = {"manifest.json", "secret_provenance.json", "provenance.json"}
        files = sorted(
            candidate
            for suffix in ("*.json", "*.jsonl")
            for candidate in path.rglob(suffix)
            if candidate.is_file() and candidate.name not in sidecars
        )
        for candidate in files:
            records.extend(load_records(candidate))
        return records

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    return _as_records(payload)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    records = load_records(args.records)
    sequence_records = load_records(args.sequences) if args.sequences else []
    report = score_hygiene_records(records, sequence_records=sequence_records, condition=args.condition)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output_json:
        Path(args.output_json).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    if args.output_tex:
        Path(args.output_tex).write_text(report["latex"], encoding="utf-8")
    return 0


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, help="Stored agent records/results JSON, JSONL, or directory.")
    parser.add_argument("--sequences", help="Hidden sequence metadata JSON, JSONL, or directory.")
    parser.add_argument("--condition", help="Condition label to stamp into the output.")
    parser.add_argument("--output-json", help="Path for JSON report. Defaults to stdout.")
    parser.add_argument("--output-tex", help="Path for LaTeX table.")
    return parser.parse_args(argv)


def _aggregate(details: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metric_totals = {name: {"numerator": 0, "denominator": 0} for name in PRIMARY_HYGIENE_METRICS}
    by_construct: dict[str, dict[str, Any]] = {}
    for detail in details:
        construct = str(detail.get("construct") or "unknown")
        construct_bucket = by_construct.setdefault(
            construct,
            {
                "construct": construct,
                "trap_count": 0,
                "tripped": 0,
                "tripped_commission": 0,
                "tripped_omission": 0,
                "metrics": {name: {"numerator": 0, "denominator": 0} for name in PRIMARY_HYGIENE_METRICS},
            },
        )
        construct_bucket["trap_count"] += 1
        construct_bucket["tripped"] += int(bool(detail.get("tripped")))
        construct_bucket["tripped_commission"] += int(bool(detail.get("tripped_commission")))
        construct_bucket["tripped_omission"] += int(bool(detail.get("tripped_omission")))
        families = [name for name in detail.get("metric_families") or [] if name in metric_totals]
        for name in families:
            numerator = int(bool(detail.get("tripped")))
            metric_totals[name]["numerator"] += numerator
            metric_totals[name]["denominator"] += 1
            construct_bucket["metrics"][name]["numerator"] += numerator
            construct_bucket["metrics"][name]["denominator"] += 1

    aggregate_by_construct = {}
    for construct, payload in sorted(by_construct.items()):
        payload = dict(payload)
        payload["trap_trip_rate"] = _metric_payload(
            "TrapTripRate",
            int(payload["tripped"]),
            int(payload["trap_count"]),
        )
        payload["commission_rate"] = _metric_payload(
            "CommissionTripRate",
            int(payload["tripped_commission"]),
            int(payload["trap_count"]),
        )
        payload["omission_rate"] = _metric_payload(
            "OmissionTripRate",
            int(payload["tripped_omission"]),
            int(payload["trap_count"]),
        )
        payload["metrics"] = {
            name: _metric_payload(name, int(values["numerator"]), int(values["denominator"]))
            for name, values in payload["metrics"].items()
        }
        aggregate_by_construct[construct] = payload

    return {
        "metrics": {
            name: _metric_payload(name, values["numerator"], values["denominator"])
            for name, values in metric_totals.items()
        },
        "by_construct": aggregate_by_construct,
        "trap_count": len(details),
        "tripped_count": sum(1 for detail in details if bool(detail.get("tripped"))),
    }


def _metric_payload(name: str, numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "name": name,
        "numerator": int(numerator),
        "denominator": int(denominator),
        "value": None if int(denominator) == 0 else float(numerator) / float(denominator),
    }


def _normalize_checks(value: Any, *, origin: str) -> list[PatternCheck]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [_check_from_string(str(value), origin=origin)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        checks: list[PatternCheck] = []
        for item in value:
            checks.extend(_normalize_checks(item, origin=origin))
        return checks
    if isinstance(value, Mapping):
        if "checks" in value:
            return _normalize_checks(value["checks"], origin=origin)
        return [_check_from_mapping(value, origin=origin)]
    return [_check_from_string(str(value), origin=origin)]


def _check_from_string(value: str, *, origin: str) -> PatternCheck:
    expected_absent = bool(_NEGATED_EXPECTATION_RE.search(value))
    if expected_absent:
        trip_on = "present"
        failure_kind = "commission"
    elif origin == "commission":
        trip_on = "present"
        failure_kind = "commission"
    else:
        trip_on = "absent"
        failure_kind = "omission"
    return PatternCheck(
        raw=value,
        origin=origin,
        patterns=tuple(_extract_patterns(value)),
        is_regex=False,
        trip_on=trip_on,
        failure_kind=failure_kind,
        match_mode="any",
    )


def _check_from_mapping(value: Mapping[str, Any], *, origin: str) -> PatternCheck:
    is_regex = False
    patterns: list[str] = []
    if value.get("regex") is not None:
        is_regex = True
        patterns.extend(_string_list(value.get("regex")))
    elif value.get("literal") is not None:
        patterns.extend(_string_list(value.get("literal")))
    elif value.get("literals") is not None:
        patterns.extend(_string_list(value.get("literals")))
    elif value.get("pattern") is not None:
        patterns.extend(_string_list(value.get("pattern")))
        is_regex = bool(value.get("is_regex") or value.get("regex_mode"))
    elif value.get("patterns") is not None:
        patterns.extend(_string_list(value.get("patterns")))
        is_regex = bool(value.get("is_regex") or value.get("regex_mode"))
    else:
        patterns.extend(_extract_patterns(str(value.get("description") or value)))

    explicit_trip_on = _presence_word(value.get("trip_on") or value.get("failure_when"))
    expected = _presence_word(value.get("expected") or value.get("expect") or value.get("presence"))
    if explicit_trip_on:
        trip_on = explicit_trip_on
    elif expected == "present":
        trip_on = "absent"
    elif expected == "absent":
        trip_on = "present"
    elif origin == "commission":
        trip_on = "present"
    else:
        trip_on = "absent"

    failure_kind = _failure_kind(value.get("failure_kind") or value.get("kind") or value.get("type"))
    if failure_kind is None:
        failure_kind = "commission" if (origin == "commission" or trip_on == "present") else "omission"

    match_mode = str(value.get("match") or value.get("match_mode") or "all").lower()
    if match_mode not in {"any", "all"}:
        match_mode = "all"

    return PatternCheck(
        raw=dict(value),
        origin=origin,
        patterns=tuple(pattern for pattern in patterns if pattern),
        is_regex=is_regex,
        trip_on=trip_on,
        failure_kind=failure_kind,
        match_mode=match_mode,
    )


def _evaluate_check(check: PatternCheck, observed_text: str) -> dict[str, Any]:
    matches = []
    missing = []
    for pattern in check.patterns:
        matched = _pattern_present(pattern, observed_text, is_regex=check.is_regex)
        if matched:
            matches.append(pattern)
        else:
            missing.append(pattern)
    if not check.patterns:
        present = False
    elif check.match_mode == "all":
        present = len(matches) == len(check.patterns)
    else:
        present = bool(matches)
    tripped = present if check.trip_on == "present" else not present
    return {
        "origin": check.origin,
        "failure_kind": check.failure_kind,
        "trip_on": check.trip_on,
        "match_mode": check.match_mode,
        "is_regex": check.is_regex,
        "raw": check.raw,
        "patterns": list(check.patterns),
        "matched_patterns": matches,
        "missing_patterns": missing,
        "tripped": bool(tripped),
    }


def _pattern_present(pattern: str, observed_text: str, *, is_regex: bool) -> bool:
    if is_regex:
        return re.search(pattern, observed_text, flags=re.MULTILINE) is not None
    return pattern in observed_text


def _extract_patterns(text: str) -> list[str]:
    patterns: list[str] = []
    stripped = text.strip()
    if stripped and not re.search(r"\s", stripped) and _looks_like_specific_literal(stripped):
        return [stripped]
    for quoted in _QUOTED_LITERAL_RE.findall(text):
        if _looks_like_specific_literal(quoted):
            patterns.append(quoted)
    for literal in _SECRET_LITERAL_RE.findall(text):
        patterns.append(literal)
    if not patterns and _looks_like_specific_literal(text):
        patterns.append(text)
    return _dedupe(patterns)


def _looks_like_specific_literal(value: str) -> bool:
    value = value.strip()
    if len(value) < 3:
        return False
    if any(char in value for char in "|=_-/"):
        return True
    if re.search(r"[A-Z].*\d|\d.*[A-Z]", value):
        return True
    return False


def _extract_hygiene_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}

    def merge(source: Any) -> None:
        if not isinstance(source, Mapping):
            return
        for key in _META_KEYS:
            if key in source and source[key] is not None:
                meta[key] = source[key]

    def walk(source: Any) -> None:
        if not isinstance(source, Mapping):
            return
        merge(source)
        for key in ("metadata", "trap_metadata", "authoring_metadata", "validation_metadata"):
            nested = source.get(key)
            if isinstance(nested, Mapping):
                merge(nested)

    walk(value)
    for key in ("task", "sequence", "trap"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            walk(nested)
    return meta


def _merged_metadata(*values: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, Mapping):
            merged.update(_extract_hygiene_metadata(value))
    return merged


def _observed_text(record: Mapping[str, Any], *, produced_diff: str = "", answer: str = "") -> str:
    parts = [produced_diff, answer]
    for key in _OBSERVED_KEYS:
        value = record.get(key)
        if isinstance(value, str):
            parts.append(value)
    for path in _STRUCTURED_OBSERVED_PATHS:
        value: Any = record
        for key in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(key)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(part for part in parts if part)


def _metric_families(meta: Mapping[str, Any], *, construct: str, seq_id: str) -> list[str]:
    explicit: list[str] = []
    for key in ("metric_family", "metric_families", "hygiene_metric", "hygiene_metrics"):
        explicit.extend(_string_list(meta.get(key)))
    mapped = [_metric_alias(value) for value in explicit]
    mapped = [value for value in mapped if value]
    if mapped:
        return _dedupe(mapped)

    construct_upper = construct.upper()
    if construct_upper in _CONSTRUCT_METRIC_MAP:
        return list(_CONSTRUCT_METRIC_MAP[construct_upper])

    haystack = " ".join(
        str(value or "").lower()
        for value in (
            construct,
            seq_id,
            meta.get("seq_type"),
            meta.get("sequence_type"),
            meta.get("construct_id"),
        )
    )
    families = [
        metric
        for metric, keywords in _METRIC_KEYWORDS
        if any(keyword in haystack for keyword in keywords)
    ]
    return _dedupe(families)


def _metric_alias(value: str) -> Optional[str]:
    compact = re.sub(r"[^A-Za-z0-9]+", "", str(value)).lower()
    if value in PRIMARY_HYGIENE_METRICS:
        return str(value)
    return _METRIC_ALIASES.get(compact)


def _construct_label(meta: Mapping[str, Any], source: Mapping[str, Any]) -> str:
    raw = (
        meta.get("construct_label")
        or meta.get("construct")
        or meta.get("construct_id")
        or source.get("construct_label")
        or source.get("construct")
        or ""
    )
    match = re.search(r"\bC(?:10|[0-9])\b", str(raw), flags=re.IGNORECASE)
    if match:
        return match.group(0).upper()
    seq_id = _sequence_id(meta, source)
    match = re.search(r"(?:^|[-_])c(10|[0-9])(?:[-_]|$)", seq_id, flags=re.IGNORECASE)
    if match:
        return f"C{match.group(1)}"
    return str(raw or "unknown")


def _sequence_id(meta: Mapping[str, Any], source: Mapping[str, Any]) -> str:
    task = source.get("task") if isinstance(source.get("task"), Mapping) else {}
    return str(
        meta.get("seq_id")
        or meta.get("sequence_id")
        or source.get("seq_id")
        or source.get("sequence_id")
        or task.get("seq_id")
        or task.get("sequence_id")
        or source.get("id")
        or meta.get("id")
        or ""
    )


def _as_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("records", "sequences", "details", "traps"):
            records = value.get(key)
            if isinstance(records, Sequence) and not isinstance(records, (str, bytes, bytearray)):
                return [dict(record) for record in records if isinstance(record, Mapping)]
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [dict(record) for record in value if isinstance(record, Mapping)]
    return []


def _infer_condition(records: Any) -> Optional[str]:
    if isinstance(records, Mapping):
        manifest = records.get("manifest") if isinstance(records.get("manifest"), Mapping) else {}
        value = records.get("condition") or manifest.get("condition")
        return str(value) if value else None
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _presence_word(value: Any) -> Optional[str]:
    if value is None:
        return None
    compact = re.sub(r"[^a-z]+", "", str(value).lower())
    if compact in {"present", "contains", "contain", "exists", "found", "true"}:
        return "present"
    if compact in {"absent", "missing", "notpresent", "notfound", "false"}:
        return "absent"
    return None


def _failure_kind(value: Any) -> Optional[str]:
    if value is None:
        return None
    compact = re.sub(r"[^a-z]+", "", str(value).lower())
    if compact in {"commission", "baduse", "staleuse", "overscope", "irrelevantimport"}:
        return "commission"
    if compact in {"omission", "missingrequired", "missingmemory"}:
        return "omission"
    return None


def _dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _latex_escape(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


if __name__ == "__main__":
    raise SystemExit(main())
