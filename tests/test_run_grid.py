from __future__ import annotations

import json
from pathlib import Path

from scripts import run_grid


def _record(
    seq_id: str,
    *,
    ordinal: int,
    oracle_id: str | None = None,
    session_index: int | None = None,
) -> dict:
    task = {"seq_id": seq_id, "sequence_id": seq_id}
    if oracle_id is not None:
        task["oracle_id"] = oracle_id
    if session_index is not None:
        task["session_index"] = session_index
    return {"ordinal": ordinal, "task": task}


def _write_results_tree(
    results_root: Path,
    *,
    condition: str = "A0",
    seed: int = 7,
    records: list[dict],
) -> Path:
    run_dir = results_root / "20260705T000000Z-SLICE-gpt-5.5-test"
    condition_dir = run_dir / condition
    condition_dir.mkdir(parents=True)
    (run_dir / "gate_report.json").write_text("{}", encoding="utf-8")
    results_path = condition_dir / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "manifest": {"condition": condition, "seed": seed},
                "records": records,
            }
        ),
        encoding="utf-8",
    )
    return results_path


def test_scan_completed_units_rejects_second_sequence_missing_s3(tmp_path: Path) -> None:
    results_path = _write_results_tree(
        tmp_path,
        records=[
            _record("alpha", ordinal=1, oracle_id="alpha-s1"),
            _record("alpha", ordinal=2, oracle_id="alpha-s2"),
            _record("alpha", ordinal=3, oracle_id="alpha-s3"),
            _record("beta", ordinal=4, oracle_id="beta-s1"),
            _record("beta", ordinal=5, oracle_id="beta-s2"),
        ],
    )

    completed = run_grid.scan_completed_units(tmp_path)

    assert completed == {}, f"{results_path} must not resume-skip without beta S3"


def test_scan_completed_units_accepts_fully_covered_grouped_ordinals(tmp_path: Path) -> None:
    results_path = _write_results_tree(
        tmp_path,
        records=[
            _record("alpha", ordinal=1),
            _record("alpha", ordinal=2),
            _record("alpha", ordinal=3),
            _record("beta", ordinal=4),
            _record("beta", ordinal=5),
            _record("beta", ordinal=6),
        ],
    )

    completed = run_grid.scan_completed_units(tmp_path)

    assert completed == {("A0", 7, ("alpha", "beta")): [str(results_path)]}
