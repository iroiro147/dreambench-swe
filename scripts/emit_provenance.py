#!/usr/bin/env python3
"""Emit provenance manifests for the BARRIER-2 folded result table.

This script replays the same validity gates and latest-mtime union rule used by
scripts/barrier2_rescore.py, then records the exact raw result records selected
for analysis/fold/final_tables.json plus rejected candidates.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(os.environ.get("DREAMFORGE_ROOT", Path(__file__).resolve().parents[1])).resolve()
os.environ.setdefault("DREAMFORGE_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts import barrier2_rescore  # noqa: E402


RESULTS_ROOT = ROOT / "experiments" / "results"
OUTPUT_DIR = ROOT / "analysis" / "fold"
JSON_OUT = OUTPUT_DIR / "PROVENANCE.json"
MD_OUT = OUTPUT_DIR / "PROVENANCE.md"
FINAL_TABLES = OUTPUT_DIR / "final_tables.json"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return _sha256_bytes(path.read_bytes())


def _record_hash(record: Mapping[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256_bytes(payload)


def _rel(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _mtime_payload(path: Path) -> dict[str, Any]:
    mtime = path.stat().st_mtime
    return {
        "mtime_epoch": mtime,
        "mtime_utc": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _load_final_tables() -> Mapping[str, Any]:
    return json.loads(FINAL_TABLES.read_text(encoding="utf-8"))


def _entry_base(
    *,
    condition: str,
    condition_dir: Path,
    results_path: Path,
    gate_path: Path | None,
    record: Mapping[str, Any],
    record_index: int,
) -> dict[str, Any]:
    sequence_id = barrier2_rescore._sequence_id(record)
    session_index = barrier2_rescore._session_index(record)
    mtime = _mtime_payload(results_path)
    return {
        "condition": condition,
        "sequence_id": sequence_id or None,
        "session_index": session_index or None,
        "record_key": f"{sequence_id}:S{session_index}" if sequence_id and session_index else None,
        "record_index": record_index,
        "source_results_path": _rel(results_path),
        "source_run_id": condition_dir.parent.name,
        "source_condition_dir": _rel(condition_dir),
        "source_results_sha256": _sha256_file(results_path),
        "record_json_sha256": _record_hash(record),
        "mtime_epoch": mtime["mtime_epoch"],
        "mtime_utc": mtime["mtime_utc"],
        "gate_report_path": _rel(gate_path),
        "gate_report_sha256": _sha256_file(gate_path),
        "isolation_mode": str(record.get("isolation_mode") or ""),
    }


def _excluded(entry: dict[str, Any], reason: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out = dict(entry)
    out["selection"] = "excluded"
    out["reason"] = f"excluded:{reason}"
    if extra:
        out.update(extra)
    return out


def collect_condition(
    condition: str,
    *,
    sequence_records: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    sequences = barrier2_rescore._sequence_map(sequence_records)
    entries: list[dict[str, Any]] = []
    selectable: list[dict[str, Any]] = []
    winners: dict[tuple[str, int], tuple[float, int]] = {}
    pattern = str(RESULTS_ROOT / "*-gpt-5.5-*" / condition)

    for condition_dir in sorted(Path(path) for path in glob.glob(pattern)):
        results_path = condition_dir / "results.json"
        if not results_path.exists():
            continue
        gate_path = barrier2_rescore._gate_report_path(condition_dir)
        gate_errors = barrier2_rescore._gate_report_errors(condition_dir)
        try:
            payload = barrier2_rescore._read_json(results_path)
            records = payload.get("records") or []
        except Exception as exc:  # noqa: BLE001 - provenance should preserve unreadable inputs.
            mtime = _mtime_payload(results_path)
            entries.append(
                {
                    "condition": condition,
                    "selection": "excluded",
                    "reason": f"excluded:unreadable_results:{type(exc).__name__}",
                    "source_results_path": _rel(results_path),
                    "source_run_id": condition_dir.parent.name,
                    "source_condition_dir": _rel(condition_dir),
                    "source_results_sha256": _sha256_file(results_path),
                    "mtime_epoch": mtime["mtime_epoch"],
                    "mtime_utc": mtime["mtime_utc"],
                    "gate_report_path": _rel(gate_path),
                    "gate_report_sha256": _sha256_file(gate_path),
                }
            )
            continue

        if gate_errors:
            for index, record in enumerate(records):
                if not isinstance(record, Mapping):
                    continue
                base = _entry_base(
                    condition=condition,
                    condition_dir=condition_dir,
                    results_path=results_path,
                    gate_path=gate_path,
                    record=record,
                    record_index=index,
                )
                entries.append(_excluded(base, ",".join(gate_errors)))
            continue

        mtime = results_path.stat().st_mtime
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                continue
            base = _entry_base(
                condition=condition,
                condition_dir=condition_dir,
                results_path=results_path,
                gate_path=gate_path,
                record=record,
                record_index=index,
            )
            key = barrier2_rescore._record_key(record)
            if not key[0] or not key[1]:
                entries.append(_excluded(base, "missing_sequence_or_session"))
                continue
            record_errors = barrier2_rescore._record_validity_errors(record)
            if record_errors:
                entries.append(_excluded(base, ",".join(record_errors)))
                continue
            contamination = barrier2_rescore._record_contamination(record, sequences.get(key[0]))
            if contamination.get("contaminated"):
                entries.append(
                    _excluded(
                        base,
                        barrier2_rescore._contamination_reason(contamination),
                        {"contamination_evidence": contamination.get("evidence") or []},
                    )
                )
                continue
            selectable_index = len(selectable)
            candidate = dict(base)
            candidate["_key"] = key
            selectable.append(candidate)
            if key not in winners or mtime > winners[key][0]:
                winners[key] = (mtime, selectable_index)

    winning_indexes = {index for _, index in winners.values()}
    for index, candidate in enumerate(selectable):
        key = candidate.pop("_key")
        if index in winning_indexes:
            candidate["selection"] = "included"
            candidate["reason"] = "included"
            entries.append(candidate)
        else:
            winner = selectable[winners[key][1]]
            candidate["selection"] = "excluded"
            candidate["reason"] = "excluded:superseded_by_latest"
            candidate["superseded_by_source_results_path"] = winner["source_results_path"]
            candidate["superseded_by_record_json_sha256"] = winner["record_json_sha256"]
            entries.append(candidate)

    return sorted(
        entries,
        key=lambda item: (
            str(item.get("condition") or ""),
            str(item.get("sequence_id") or ""),
            int(item.get("session_index") or 0),
            str(item.get("selection") or ""),
            str(item.get("source_results_path") or ""),
            int(item.get("record_index") or 0),
        ),
    )


def _summarize(entries: list[dict[str, Any]], final_tables: Mapping[str, Any]) -> dict[str, Any]:
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        by_condition[str(entry.get("condition"))].append(entry)

    summary: dict[str, Any] = {}
    for condition in barrier2_rescore.CONDS:
        items = by_condition.get(condition, [])
        included = [item for item in items if item.get("selection") == "included"]
        excluded = [item for item in items if item.get("selection") == "excluded"]
        included_s3 = [item for item in included if int(item.get("session_index") or 0) == 3]
        excluded_s3 = [item for item in excluded if int(item.get("session_index") or 0) == 3]
        final_n = int(((final_tables.get(condition) or {}).get("n_S3") or 0))
        hist_all = Counter(str(item.get("reason") or "") for item in excluded)
        hist_s3 = Counter(str(item.get("reason") or "") for item in excluded_s3)
        summary[condition] = {
            "n_included": len(included_s3),
            "n_excluded": len(excluded_s3),
            "n_included_records_all_sessions": len(included),
            "n_excluded_records_all_sessions": len(excluded),
            "final_tables_n_S3": final_n,
            "n_included_matches_final_tables_n_S3": len(included_s3) == final_n,
            "exclusion_reason_histogram": dict(sorted(hist_all.items())),
            "exclusion_reason_histogram_s3": dict(sorted(hist_s3.items())),
        }
    return summary


def build_manifest() -> dict[str, Any]:
    sequences = barrier2_rescore.load_sequences()
    final_tables = _load_final_tables()
    entries: list[dict[str, Any]] = []
    for condition in barrier2_rescore.CONDS:
        entries.extend(collect_condition(condition, sequence_records=sequences))
    summary = _summarize(entries, final_tables)
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repo_root": str(ROOT),
        "results_root": _rel(RESULTS_ROOT),
        "final_tables_path": _rel(FINAL_TABLES),
        "final_tables_sha256": _sha256_file(FINAL_TABLES),
        "selection_rule": (
            "For each condition, scan experiments/results/*-gpt-5.5-*/<COND>/results.json; "
            "exclude failing gate reports, invalid continuation/sleep/isolation records, and contamination; "
            "among remaining records keep the record with the latest source results.json mtime per "
            "(sequence_id, session_index)."
        ),
        "summary": summary,
        "records": entries,
    }


def render_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest["summary"]
    records = manifest["records"]
    lines: list[str] = [
        "# PROVENANCE MANIFEST",
        "",
        f"- Generated at UTC: `{manifest['generated_at_utc']}`",
        f"- Repo root: `{manifest['repo_root']}`",
        f"- Results root: `{manifest['results_root']}`",
        f"- Final table: `{manifest['final_tables_path']}`",
        f"- Final table sha256: `{manifest['final_tables_sha256']}`",
        f"- Selection rule: {manifest['selection_rule']}",
        "",
        "## Final Table Check",
        "",
        "| condition | n_included S3 | final_tables n_S3 | match | n_excluded S3 | included all sessions | excluded all sessions |",
        "|---|---:|---:|---|---:|---:|---:|",
    ]
    for condition in barrier2_rescore.CONDS:
        item = summary[condition]
        lines.append(
            "| {condition} | {n_included} | {final_n} | {match} | {n_excluded} | {all_included} | {all_excluded} |".format(
                condition=condition,
                n_included=item["n_included"],
                final_n=item["final_tables_n_S3"],
                match="yes" if item["n_included_matches_final_tables_n_S3"] else "NO",
                n_excluded=item["n_excluded"],
                all_included=item["n_included_records_all_sessions"],
                all_excluded=item["n_excluded_records_all_sessions"],
            )
        )

    lines.extend(["", "## Exclusion Histograms", ""])
    for condition in barrier2_rescore.CONDS:
        hist = summary[condition]["exclusion_reason_histogram_s3"]
        if not hist:
            lines.append(f"- {condition}: none for S3")
            continue
        parts = [f"{reason}={count}" for reason, count in hist.items()]
        lines.append(f"- {condition}: " + "; ".join(parts))

    included_s3 = [
        item
        for item in records
        if item.get("selection") == "included" and int(item.get("session_index") or 0) == 3
    ]
    lines.extend(
        [
            "",
            "## Included S3 Records",
            "",
            "| condition | record_key | source run | results.json | record sha256 | gate sha256 | isolation |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in included_s3:
        lines.append(
            "| {condition} | `{record_key}` | `{run}` | `{path}` | `{record_hash}` | `{gate_hash}` | `{isolation}` |".format(
                condition=item["condition"],
                record_key=item["record_key"],
                run=item["source_run_id"],
                path=item["source_results_path"],
                record_hash=item["record_json_sha256"],
                gate_hash=item.get("gate_report_sha256") or "absent",
                isolation=item.get("isolation_mode") or "",
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    os.chdir(ROOT)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    JSON_OUT.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MD_OUT.write_text(render_markdown(manifest), encoding="utf-8")

    print("PROVENANCE SUMMARY")
    all_match = True
    for condition in barrier2_rescore.CONDS:
        item = manifest["summary"][condition]
        match = bool(item["n_included_matches_final_tables_n_S3"])
        all_match = all_match and match
        hist = item["exclusion_reason_histogram_s3"]
        hist_text = ", ".join(f"{key}={value}" for key, value in hist.items()) if hist else "none"
        print(
            f"{condition}: n_included={item['n_included']} final_tables_n_S3={item['final_tables_n_S3']} "
            f"match={'yes' if match else 'NO'} n_excluded={item['n_excluded']} exclusion_histogram_s3={hist_text}"
        )
    print(f"Wrote {JSON_OUT.relative_to(ROOT)}")
    print(f"Wrote {MD_OUT.relative_to(ROOT)}")
    print(f"provenance_n_match={'yes' if all_match else 'no'}")
    return 0 if all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
