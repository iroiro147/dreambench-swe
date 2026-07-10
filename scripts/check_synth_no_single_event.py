#!/usr/bin/env python3
"""Check DreamBench-SWE-Synth decisive literals are not in any single event."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEQUENCES = ROOT / "experiments" / "env" / "sequences_synth.jsonl"

DECISIVE_LITERALS = {
    "synth-config-schema-audit-code": ["SCHEMA_AUDIT|paths=3|missing=1|code=CFG-4-R7"],
    "synth-config-csv-width-delta": [
        "CSV_WIDTH_DELTA line 2 delta=1 polarity=under",
        "CSV_WIDTH_DELTA line 2 delta=2 polarity=over",
    ],
    "synth-config-dupsec-code": ["DUPSEC_K9R4 line 5: repeated section 'server'"],
    "synth-expr-bitwise-help": ["& bit_bk7z19f_and precedence=5 associativity=left arity=2 family=bk7z19f mask=10"],
    "synth-expr-precedence-derive": [">> shift_right precedence=12 associativity=left"],
    "synth-expr-help-epoch": ["SUMMARY|ops=5|fmt=v2|epoch=E9-KT3"],
    "synth-todo-overdue-handoff": ["DUE|2026-07-01|RID-K4J9Q-0001|late-3|Pay rent"],
    "synth-todo-report-channel": ["RPT|TDL4.Q3-VX8|1|Alpha"],
}


def load_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence-records", default=str(DEFAULT_SEQUENCES))
    args = parser.parse_args(argv)
    records = load_records(Path(args.sequence_records))
    by_id = {record["seq_id"]: record for record in records}
    ok = True

    missing = sorted(set(DECISIVE_LITERALS) - set(by_id))
    extra = sorted(seq_id for seq_id in by_id if seq_id.startswith("synth-") and seq_id not in DECISIVE_LITERALS)
    if missing:
        print(f"FAIL missing synth sequence records: {', '.join(missing)}")
        ok = False
    if extra:
        print(f"FAIL missing decisive literal mapping: {', '.join(extra)}")
        ok = False

    for seq_id in sorted(DECISIVE_LITERALS):
        record = by_id.get(seq_id)
        if not record:
            continue
        failures: list[str] = []
        for event in record.get("events", []):
            content = str(event.get("content", ""))
            for literal in DECISIVE_LITERALS[seq_id]:
                if literal in content:
                    failures.append(f"{event.get('event_id')} contains {literal!r}")
        if failures:
            ok = False
            print(f"FAIL {seq_id}: " + "; ".join(failures))
        else:
            print(f"PASS {seq_id}: decisive literal absent from every single injected event")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
