"""Fixture tests for the admission-funnel transparency tool."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import admission_funnel


PENDING_MARKER = "[" + "V2-" + "PENDING]"


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    return path


def _dry_batch(path: Path, verdicts: list[dict]) -> Path:
    return _write_json(
        path,
        {
            "mode": "dry",
            "dry": True,
            "sequence_count": len(verdicts),
            "valid_count": sum(1 for verdict in verdicts if verdict.get("valid") is True),
            "invalid_count": sum(1 for verdict in verdicts if verdict.get("valid") is False),
            "verdicts": verdicts,
        },
    )


def _admitted(path: Path, records: list[dict]) -> Path:
    return _write_jsonl(path, records)


def test_funnel_uses_latest_dry_verdict_and_marks_live_pending(tmp_path: Path) -> None:
    first_batch = _dry_batch(
        tmp_path / "batch-20260705T000000000000Z.json",
        [
            {"seq_id": "v2-c2-alpha", "valid": False},
            {"seq_id": "v2-c5-beta", "valid": True},
            {"seq_id": "synth-not-v2", "valid": True},
        ],
    )
    second_batch = _dry_batch(
        tmp_path / "batch-20260705T010000000000Z.json",
        [
            {"seq_id": "v2-c2-alpha", "valid": True},
            {"seq_id": "v2-c2-gamma", "valid": False},
            {"seq_id": "v2-c5-beta", "valid": True},
        ],
    )
    admitted_path = _admitted(
        tmp_path / "sequences_v2.jsonl",
        [
            {"seq_id": "v2-c2-alpha", "construct_label": "C2"},
            {"seq_id": "v2-c5-beta", "construct_label": "C5"},
            {"seq_id": "v2-c7-admitted-only", "construct_label": "C7"},
        ],
    )

    report = admission_funnel.build_funnel(
        dry_validation_paths=[first_batch, second_batch],
        admitted_path=admitted_path,
        live_validation_paths=[],
    )

    assert report["live_validation_status"] == "pending"
    assert report["totals"]["authored"] == 3
    assert report["totals"]["dry_valid"] == 2
    assert report["totals"]["live_valid"] is None
    assert report["totals"]["admitted"] == 3
    assert report["drops"]["authored_not_admitted"] == ["v2-c2-gamma"]
    assert report["drops"]["admitted_without_authored"] == ["v2-c7-admitted-only"]

    rows = {row["construct"]: row for row in report["constructs"]}
    assert rows["C2"]["authored"] == 2
    assert rows["C2"]["dry_valid"] == 1
    assert rows["C2"]["admitted"] == 1
    assert rows["C7"]["authored"] == 0
    assert rows["C7"]["dry_valid"] == 0
    assert rows["C7"]["admitted"] == 1

    markdown = admission_funnel.render_markdown(report)
    latex = admission_funnel.render_latex(report)
    assert f"| Total | 3 | 2 | {PENDING_MARKER} | 3 |" in markdown
    assert f"Total & 3 & 2 & {PENDING_MARKER} & 3" in latex


def test_live_counts_require_explicit_live_payload(tmp_path: Path) -> None:
    dry_batch = _dry_batch(
        tmp_path / "batch-20260705T000000000000Z.json",
        [
            {"seq_id": "v2-c2-alpha", "valid": True},
            {"seq_id": "v2-c5-beta", "valid": True},
        ],
    )
    dry_shaped_like_validation = _write_json(
        tmp_path / "20260705T000100000000Z-validation.json",
        {
            "mode": "dry",
            "live": False,
            "sequence_count": 1,
            "valid_count": 1,
            "invalid_count": 0,
            "verdicts": [{"seq_id": "v2-c2-alpha", "valid": True}],
        },
    )
    live_validation = _write_json(
        tmp_path / "20260705T000200000000Z-validation.json",
        {
            "mode": "live",
            "live": True,
            "sequence_count": 3,
            "valid_count": 2,
            "invalid_count": 1,
            "verdicts": [
                {"seq_id": "v2-c2-alpha", "valid": True},
                {"seq_id": "v2-c5-beta", "valid": False},
                {"seq_id": "v2-c9-live-only", "valid": True},
            ],
        },
    )
    admitted_path = _admitted(
        tmp_path / "sequences_v2.jsonl",
        [
            {"seq_id": "v2-c2-alpha", "construct_label": "C2"},
            {"seq_id": "v2-c5-beta", "construct_label": "C5"},
        ],
    )

    report = admission_funnel.build_funnel(
        dry_validation_paths=[dry_batch],
        admitted_path=admitted_path,
        live_validation_paths=[dry_shaped_like_validation, live_validation],
    )

    assert report["live_validation_status"] == "available"
    assert "pending_marker" not in report
    assert report["inputs"]["live_validation_paths_used"] == [str(live_validation)]
    assert report["inputs"]["ignored_live_validation_paths"] == [str(dry_shaped_like_validation)]
    assert report["totals"]["live_valid"] == 2
    assert report["drops"]["dry_valid_not_live_valid"] == ["v2-c5-beta"]
    assert report["drops"]["live_valid_not_admitted"] == ["v2-c9-live-only"]
    assert PENDING_MARKER not in admission_funnel.render_markdown(report)

    rows = {row["construct"]: row for row in report["constructs"]}
    assert rows["C2"]["live_valid"] == 1
    assert rows["C5"]["live_valid"] == 0
    assert rows["C9"]["authored"] == 0
    assert rows["C9"]["live_valid"] == 1
    assert rows["C9"]["admitted"] == 0


def test_cli_writes_markdown_latex_and_json_outputs(tmp_path: Path) -> None:
    dry_batch = _dry_batch(
        tmp_path / "batch-20260705T000000000000Z.json",
        [{"seq_id": "v2-c2-alpha", "valid": True}],
    )
    admitted_path = _admitted(
        tmp_path / "sequences_v2.jsonl",
        [{"seq_id": "v2-c2-alpha", "construct_label": "C2"}],
    )
    output_dir = tmp_path / "out"

    rc = admission_funnel.main(
        [
            "--dry-validation",
            str(dry_batch),
            "--admitted",
            str(admitted_path),
            "--validation-root",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert rc == 0
    markdown = output_dir / "admission_funnel.md"
    latex = output_dir / "admission_funnel.tex"
    summary = output_dir / "admission_funnel.json"
    assert markdown.exists()
    assert latex.exists()
    assert summary.exists()
    assert f"| C2 | 1 | 1 | {PENDING_MARKER} | 1 |" in markdown.read_text(encoding="utf-8")
    assert f"C2 & 1 & 1 & {PENDING_MARKER} & 1" in latex.read_text(encoding="utf-8")
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["totals"]["authored"] == 1
    assert payload["totals"]["dry_valid"] == 1
    assert payload["totals"]["live_valid"] is None
    assert payload["totals"]["admitted"] == 1
