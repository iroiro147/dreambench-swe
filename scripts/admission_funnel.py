#!/usr/bin/env python3
"""Emit the DreamBench-SWE v2 trap admission funnel.

The funnel is intentionally conservative:
- authored = unique v2 sequence ids seen in dry batch validation summaries
- dry-valid = latest dry verdict per sequence id is valid
- live-valid = latest explicit live verdict per sequence id is valid
- admitted = unique sequence ids present in experiments/env/sequences_v2.jsonl

If no explicit live validation payload is present, live-valid is reported with
an explicit pending sentinel instead of a fabricated zero.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALIDATION_ROOT = ROOT / "experiments" / "validation"
DEFAULT_DRY_GLOB = DEFAULT_VALIDATION_ROOT / "batch-*.json"
DEFAULT_ADMITTED_PATH = ROOT / "experiments" / "env" / "sequences_v2.jsonl"
PENDING_MARKER = "[" + "V2-" + "PENDING]"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        dry_paths = _expand_paths(args.dry_validation or [str(DEFAULT_DRY_GLOB)])
        live_paths = (
            [Path(path) for path in args.live_validation_json]
            if args.live_validation_json
            else discover_live_validation_paths(Path(args.validation_root))
        )
        report = build_funnel(
            dry_validation_paths=dry_paths,
            admitted_path=Path(args.admitted),
            live_validation_paths=live_paths,
            seq_prefix=None if args.include_all else args.seq_prefix,
        )
        outputs = {
            "markdown": render_markdown(report),
            "latex": render_latex(report),
            "json": json.dumps(report, indent=2, sort_keys=True),
        }
        written = write_outputs(
            outputs,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            markdown_output=Path(args.markdown_output) if args.markdown_output else None,
            latex_output=Path(args.latex_output) if args.latex_output else None,
            json_output=Path(args.json_output) if args.json_output else None,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should report compactly.
        print(f"admission funnel failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if written:
        for kind, path in written:
            print(f"wrote {kind}: {path}")
    else:
        print(outputs["markdown"])
        print()
        print("LaTeX:")
        print(outputs["latex"])
        print()
        print("JSON:")
        print(outputs["json"])
    return 0


def build_funnel(
    *,
    dry_validation_paths: Sequence[Path],
    admitted_path: Path,
    live_validation_paths: Sequence[Path] = (),
    seq_prefix: str | None = "v2-",
) -> dict[str, Any]:
    """Build a JSON-serializable admission-funnel summary."""

    dry_latest, ignored_dry = load_dry_verdicts(dry_validation_paths, seq_prefix=seq_prefix)
    admitted = load_admitted_sequences(admitted_path, seq_prefix=seq_prefix)
    live_latest, live_paths_used, ignored_live = load_live_verdicts(live_validation_paths, seq_prefix=seq_prefix)
    live_available = bool(live_paths_used)

    authored_ids = set(dry_latest)
    dry_valid_ids = {seq_id for seq_id, entry in dry_latest.items() if entry["valid"] is True}
    admitted_ids = set(admitted)
    live_valid_ids = (
        {seq_id for seq_id, entry in live_latest.items() if entry["valid"] is True}
        if live_available
        else None
    )

    construct_ids = _construct_id_sets(dry_latest, admitted, live_latest if live_available else {})
    constructs = [
        _construct_row(
            construct,
            ids,
            authored_ids=authored_ids,
            dry_valid_ids=dry_valid_ids,
            live_valid_ids=live_valid_ids,
            admitted_ids=admitted_ids,
        )
        for construct, ids in sorted(construct_ids.items(), key=lambda item: _construct_sort_key(item[0]))
    ]

    totals = _totals_row(
        authored_ids=authored_ids,
        dry_valid_ids=dry_valid_ids,
        live_valid_ids=live_valid_ids,
        admitted_ids=admitted_ids,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "seq_prefix": seq_prefix,
            "dry_policy": "unique sequence ids from dry batch summaries; latest verdict by sorted file path wins",
            "live_policy": "latest verdict from explicit live validation payloads only; dry validation payloads do not count",
            "admitted_policy": "unique sequence ids in the admitted sequence JSONL",
        },
        "inputs": {
            "dry_validation_paths": [str(path) for path in sorted(map(Path, dry_validation_paths), key=str)],
            "ignored_dry_validation_paths": [str(path) for path in ignored_dry],
            "live_validation_paths": [str(path) for path in sorted(map(Path, live_validation_paths), key=str)],
            "live_validation_paths_used": [str(path) for path in live_paths_used],
            "ignored_live_validation_paths": [str(path) for path in ignored_live],
            "admitted_path": str(admitted_path),
        },
        "live_validation_status": "available" if live_available else "pending",
        "constructs": constructs,
        "totals": totals,
        "drops": {
            "authored_not_dry_valid": sorted(authored_ids - dry_valid_ids),
            "authored_not_admitted": sorted(authored_ids - admitted_ids),
            "dry_valid_not_admitted": sorted(dry_valid_ids - admitted_ids),
            "admitted_without_authored": sorted(admitted_ids - authored_ids),
            "admitted_without_dry_valid": sorted(admitted_ids - dry_valid_ids),
            "dry_valid_not_live_valid": None
            if live_valid_ids is None
            else sorted(dry_valid_ids - live_valid_ids),
            "live_valid_not_admitted": None
            if live_valid_ids is None
            else sorted(live_valid_ids - admitted_ids),
        },
    }
    if not live_available:
        report["pending_marker"] = PENDING_MARKER
    return report


def load_dry_verdicts(paths: Sequence[Path], *, seq_prefix: str | None = "v2-") -> tuple[dict[str, dict], list[Path]]:
    """Load latest dry verdicts keyed by seq_id."""

    latest: dict[str, dict] = {}
    ignored: list[Path] = []
    for path in sorted(map(Path, paths), key=str):
        payload = _load_json(path)
        if not _is_dry_payload(payload):
            ignored.append(path)
            continue
        for verdict in payload.get("verdicts") or []:
            if not isinstance(verdict, Mapping):
                continue
            seq_id = _seq_id(verdict)
            if not seq_id or not _included(seq_id, seq_prefix):
                continue
            latest[seq_id] = {
                "seq_id": seq_id,
                "construct": _construct_for(seq_id, verdict),
                "valid": _valid_value(verdict),
                "source_path": str(path),
                "status": verdict.get("status"),
            }
    if not latest:
        raise ValueError("no matching dry validation verdicts found")
    return latest, ignored


def load_live_verdicts(paths: Sequence[Path], *, seq_prefix: str | None = "v2-") -> tuple[dict[str, dict], list[Path], list[Path]]:
    """Load latest explicit live verdicts keyed by seq_id.

    Dry validation payloads are ignored, even if they have the same verdict
    schema as live validation payloads.
    """

    latest: dict[str, dict] = {}
    used: list[Path] = []
    ignored: list[Path] = []
    for path in sorted(map(Path, paths), key=str):
        payload = _load_json(path)
        if not _is_live_payload(payload):
            ignored.append(path)
            continue
        used.append(path)
        for verdict in payload.get("verdicts") or []:
            if not isinstance(verdict, Mapping):
                continue
            seq_id = _seq_id(verdict)
            if not seq_id or not _included(seq_id, seq_prefix):
                continue
            latest[seq_id] = {
                "seq_id": seq_id,
                "construct": _construct_for(seq_id, verdict),
                "valid": _valid_value(verdict),
                "source_path": str(path),
                "status": verdict.get("status"),
            }
    return latest, used, ignored


def load_admitted_sequences(path: Path, *, seq_prefix: str | None = "v2-") -> dict[str, dict]:
    """Load admitted sequence records keyed by seq_id."""

    admitted: dict[str, dict] = {}
    for line_number, record in _read_jsonl(path):
        seq_id = _seq_id(record)
        if not seq_id or not _included(seq_id, seq_prefix):
            continue
        if seq_id in admitted:
            raise ValueError(f"duplicate admitted seq_id {seq_id!r} at {path}:{line_number}")
        admitted[seq_id] = {
            "seq_id": seq_id,
            "construct": _construct_for(seq_id, record),
            "source_path": str(path),
        }
    if not admitted:
        raise ValueError(f"no admitted sequences found in {path}")
    return admitted


def discover_live_validation_paths(validation_root: Path) -> list[Path]:
    """Find validation JSONs that are explicitly live."""

    paths: list[Path] = []
    for path in sorted(Path(validation_root).glob("*-validation.json"), key=str):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if _is_live_payload(payload):
            paths.append(path)
    return paths


def render_markdown(report: Mapping[str, Any]) -> str:
    headers = ["Construct", "Authored", "Dry-valid", "Live-valid", "Admitted"]
    rows = [
        [
            str(row["construct"]),
            str(row["authored"]),
            str(row["dry_valid"]),
            _live_cell(report, row),
            str(row["admitted"]),
        ]
        for row in report["constructs"]
    ]
    totals = report["totals"]
    rows.append(
        [
            "Total",
            str(totals["authored"]),
            str(totals["dry_valid"]),
            _live_cell(report, totals),
            str(totals["admitted"]),
        ]
    )
    lines = [
        "| " + " | ".join(headers) + " |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def render_latex(report: Mapping[str, Any]) -> str:
    lines = [
        r"\begin{tabular}{lrrrr}",
        r"\hline",
        r"Construct & Authored & Dry-valid & Live-valid & Admitted \\",
        r"\hline",
    ]
    for row in report["constructs"]:
        lines.append(
            f"{_latex_escape(str(row['construct']))} & {row['authored']} & {row['dry_valid']} & "
            f"{_latex_escape(_live_cell(report, row))} & {row['admitted']} \\\\"
        )
    totals = report["totals"]
    lines.extend(
        [
            r"\hline",
            f"Total & {totals['authored']} & {totals['dry_valid']} & "
            f"{_latex_escape(_live_cell(report, totals))} & {totals['admitted']} \\\\",
            r"\hline",
            r"\end{tabular}",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    outputs: Mapping[str, str],
    *,
    output_dir: Path | None = None,
    markdown_output: Path | None = None,
    latex_output: Path | None = None,
    json_output: Path | None = None,
) -> list[tuple[str, Path]]:
    paths: dict[str, Path] = {}
    if output_dir is not None:
        paths.update(
            {
                "markdown": output_dir / "admission_funnel.md",
                "latex": output_dir / "admission_funnel.tex",
                "json": output_dir / "admission_funnel.json",
            }
        )
    if markdown_output is not None:
        paths["markdown"] = markdown_output
    if latex_output is not None:
        paths["latex"] = latex_output
    if json_output is not None:
        paths["json"] = json_output

    written: list[tuple[str, Path]] = []
    for kind, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[kind] + "\n", encoding="utf-8")
        written.append((kind, path))
    return written


def _construct_id_sets(
    dry_latest: Mapping[str, Mapping[str, Any]],
    admitted: Mapping[str, Mapping[str, Any]],
    live_latest: Mapping[str, Mapping[str, Any]],
) -> dict[str, set[str]]:
    by_construct: dict[str, set[str]] = defaultdict(set)
    for source in (dry_latest, admitted, live_latest):
        for seq_id, entry in source.items():
            by_construct[str(entry["construct"])].add(seq_id)
    return dict(by_construct)


def _construct_row(
    construct: str,
    ids: set[str],
    *,
    authored_ids: set[str],
    dry_valid_ids: set[str],
    live_valid_ids: set[str] | None,
    admitted_ids: set[str],
) -> dict[str, Any]:
    authored = ids & authored_ids
    dry_valid = ids & dry_valid_ids
    admitted = ids & admitted_ids
    live_valid = None if live_valid_ids is None else ids & live_valid_ids
    return {
        "construct": construct,
        "authored": len(authored),
        "dry_valid": len(dry_valid),
        "live_valid": None if live_valid is None else len(live_valid),
        "live_valid_status": PENDING_MARKER if live_valid is None else "available",
        "admitted": len(admitted),
        "authored_ids": sorted(authored),
        "dry_valid_ids": sorted(dry_valid),
        "live_valid_ids": None if live_valid is None else sorted(live_valid),
        "admitted_ids": sorted(admitted),
        "authored_not_admitted_ids": sorted(authored - admitted),
        "admitted_without_dry_valid_ids": sorted(admitted - dry_valid),
    }


def _totals_row(
    *,
    authored_ids: set[str],
    dry_valid_ids: set[str],
    live_valid_ids: set[str] | None,
    admitted_ids: set[str],
) -> dict[str, Any]:
    return {
        "authored": len(authored_ids),
        "dry_valid": len(dry_valid_ids),
        "live_valid": None if live_valid_ids is None else len(live_valid_ids),
        "live_valid_status": PENDING_MARKER if live_valid_ids is None else "available",
        "admitted": len(admitted_ids),
        "authored_ids": sorted(authored_ids),
        "dry_valid_ids": sorted(dry_valid_ids),
        "live_valid_ids": None if live_valid_ids is None else sorted(live_valid_ids),
        "admitted_ids": sorted(admitted_ids),
    }


def _live_cell(report: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    if report["live_validation_status"] == "pending":
        return PENDING_MARKER
    return str(row["live_valid"])


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _read_jsonl(path: Path) -> list[tuple[int, Mapping[str, Any]]]:
    records: list[tuple[int, Mapping[str, Any]]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        records.append((line_number, payload))
    return records


def _is_dry_payload(payload: Mapping[str, Any]) -> bool:
    return payload.get("dry") is True or str(payload.get("mode") or "").lower() == "dry"


def _is_live_payload(payload: Mapping[str, Any]) -> bool:
    return payload.get("live") is True or str(payload.get("mode") or "").lower() == "live"


def _seq_id(record: Mapping[str, Any]) -> str:
    return str(record.get("seq_id") or record.get("sequence_id") or "").strip()


def _included(seq_id: str, seq_prefix: str | None) -> bool:
    return seq_prefix is None or seq_id.startswith(seq_prefix)


def _valid_value(verdict: Mapping[str, Any]) -> bool | None:
    if verdict.get("valid") is True:
        return True
    if verdict.get("valid") is False:
        return False
    return None


def _construct_for(seq_id: str, record: Mapping[str, Any]) -> str:
    for key in ("construct_label", "construct", "construct_id"):
        value = record.get(key)
        if value:
            return _normalize_construct(str(value))
    match = re.search(r"(?:^|-)c(\d+)(?=-|$)", seq_id.lower())
    if match:
        return f"C{int(match.group(1))}"
    return "UNKNOWN"


def _normalize_construct(value: str) -> str:
    text = value.strip().upper()
    match = re.fullmatch(r"C?0*(\d+)", text)
    if match:
        return f"C{int(match.group(1))}"
    return text or "UNKNOWN"


def _construct_sort_key(construct: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"C(\d+)", construct)
    if match:
        return (0, int(match.group(1)), construct)
    return (1, 0, construct)


def _latex_escape(value: str) -> str:
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(replacements.get(char, char) for char in value)


def _expand_paths(patterns: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            paths.extend(Path(match) for match in matches)
            continue
        path = Path(pattern)
        if path.exists():
            paths.append(path)
    if not paths:
        raise ValueError(f"no paths matched: {', '.join(patterns)}")
    return paths


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-validation",
        action="append",
        default=None,
        help="Dry batch validation JSON path or glob. May be repeated.",
    )
    parser.add_argument(
        "--live-validation-json",
        action="append",
        default=[],
        help="Explicit live validation JSON path. May be repeated. Dry payloads are ignored.",
    )
    parser.add_argument(
        "--validation-root",
        default=str(DEFAULT_VALIDATION_ROOT),
        help="Directory to auto-scan for *-validation.json live payloads when no live JSON is provided.",
    )
    parser.add_argument(
        "--admitted",
        default=str(DEFAULT_ADMITTED_PATH),
        help="Admitted sequence JSONL path.",
    )
    parser.add_argument("--seq-prefix", default="v2-", help="Sequence id prefix to include.")
    parser.add_argument("--include-all", action="store_true", help="Include sequences outside --seq-prefix.")
    parser.add_argument("--output-dir", help="Write admission_funnel.{md,tex,json} under this directory.")
    parser.add_argument("--markdown-output", help="Write markdown table to this path.")
    parser.add_argument("--latex-output", help="Write LaTeX table to this path.")
    parser.add_argument("--json-output", help="Write JSON summary to this path.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
