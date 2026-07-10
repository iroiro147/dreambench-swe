"""Unit tests for the batch trap-validation harness."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts import batch_validate


def _record(seq_id: str, *, seq_type: str | None = None, events: list[dict] | None = None) -> dict:
    record = {
        "seq_id": seq_id,
        "repo": "configly",
        "initial_commit": "seed",
        "sessions": [
            {"session_index": 1, "instruction": "s1", "oracle_id": f"{seq_id}-s1"},
            {"session_index": 2, "instruction": "s2", "oracle_id": f"{seq_id}-s2"},
            {"session_index": 3, "instruction": "s3", "oracle_id": f"{seq_id}-s3"},
        ],
        "events": events or [],
        "oracle_labels": [],
    }
    if seq_type is not None:
        record["seq_type"] = seq_type
    return record


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _passing_stub(record: dict, *, live: bool) -> dict:
    assert live is True
    return {
        "seq_id": record["seq_id"],
        "valid": True,
        "b0_s3_failed": True,
        "refsol_passes": True,
        "oracle_hidden": True,
        "reasons": [],
    }


def test_batch_validate_uses_stubbed_validator_and_writes_summary(tmp_path: Path) -> None:
    source = _write_jsonl(tmp_path / "candidates.jsonl", [_record("seq-a"), _record("seq-b")])
    calls: list[str] = []

    def stub(record: dict, *, live: bool) -> dict:
        calls.append(record["seq_id"])
        return _passing_stub(record, live=live)

    summary = batch_validate.run_batch(
        source,
        dry=False,
        workers=4,
        timeout_seconds=10,
        validation_root=tmp_path / "validation",
        validate_sequence=stub,
    )

    assert calls == ["seq-a", "seq-b"]
    assert summary["mode"] == "live"
    assert summary["sequence_count"] == 2
    assert summary["workers"] == 2
    assert summary["valid_count"] == 2
    assert summary["invalid_count"] == 0

    output_path = Path(summary["output_path"])
    assert output_path.name.startswith("batch-")
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["sequence_count"] == 2
    assert [verdict["seq_id"] for verdict in payload["verdicts"]] == ["seq-a", "seq-b"]
    assert all(verdict["record_hash"] for verdict in payload["verdicts"])


def test_batch_validate_resume_skips_prior_record_hash(tmp_path: Path) -> None:
    source = _write_jsonl(tmp_path / "candidates.jsonl", [_record("seq-a")])
    validation_root = tmp_path / "validation"

    first = batch_validate.run_batch(
        source,
        dry=False,
        workers=1,
        validation_root=validation_root,
        validate_sequence=_passing_stub,
    )
    assert first["evaluated_count"] == 1

    def forbidden_stub(record: dict, *, live: bool) -> dict:
        raise AssertionError(f"resume should not revalidate {record['seq_id']}")

    second = batch_validate.run_batch(
        source,
        dry=False,
        workers=1,
        resume=True,
        validation_root=validation_root,
        validate_sequence=forbidden_stub,
    )

    assert second["evaluated_count"] == 0
    assert second["skipped_count"] == 1
    assert second["valid_count"] == 1
    assert second["verdicts"][0]["status"] == "skipped"
    assert second["verdicts"][0]["skipped"] is True


def test_synthesis_no_single_event_failure_is_integrated(tmp_path: Path) -> None:
    decisive_literal = "SCHEMA_AUDIT|paths=3|missing=1|code=CFG-4-R7"
    source = _write_jsonl(
        tmp_path / "candidates.jsonl",
        [
            {
                **_record(
                    "synth-config-schema-audit-code",
                    events=[{"event_id": "e1", "content": f"bad leak: {decisive_literal}"}],
                ),
                "type": "synthesis",
            }
        ],
    )

    summary = batch_validate.run_batch(
        source,
        dry=False,
        workers=1,
        validation_root=tmp_path / "validation",
        validate_sequence=_passing_stub,
    )

    verdict = summary["verdicts"][0]
    assert summary["valid_count"] == 0
    assert summary["invalid_count"] == 1
    assert verdict["seq_id"] == "synth-config-schema-audit-code"
    assert verdict["no_single_event"] is False
    assert verdict["valid"] is False
    assert "contains decisive literal" in "; ".join(verdict["reasons"])


def test_construct_live_populates_bad_memory_for_c9_only(tmp_path: Path, monkeypatch: Any) -> None:
    c9_record = {
        **_record(
            "v2-c9-fixture-spurious-lesson-01",
            events=[
                {"event_id": "e-false", "after_session": 1, "content": "bad lesson"},
                {"event_id": "e-disconfirm", "after_session": 2, "content": "lesson disconfirmed"},
            ],
        ),
        "construct_label": "C9",
    }
    c3_record = {**_record("v2-c3-fixture-supersession-01"), "construct_label": "C3"}
    source = _write_jsonl(tmp_path / "candidates.jsonl", [c9_record, c3_record])
    bad_memory_calls: list[str] = []
    construct_calls: list[tuple[str, Any, Any]] = []

    def bad_memory_stub(record: Mapping[str, Any]) -> tuple[bool, str | None]:
        bad_memory_calls.append(str(record["seq_id"]))
        return True, None

    def live_stub(record: Mapping[str, Any], *, live: bool) -> dict[str, Any]:
        assert live is True
        is_c9 = record["seq_id"] == c9_record["seq_id"]
        return {
            "seq_id": record["seq_id"],
            "valid": True,
            "b0_s3_failed": not is_c9,
            "refsol_passes": True,
            "oracle_hidden": True,
            "reasons": [],
        }

    def construct_stub(
        record: Mapping[str, Any],
        b0_s3_failed: Any,
        bad_memory_s3_failed: Any,
    ) -> dict[str, Any]:
        construct_calls.append((str(record["seq_id"]), b0_s3_failed, bad_memory_s3_failed))
        return {
            "seq_id": record["seq_id"],
            "valid": True,
            "checked": True,
            "skipped": False,
            "checks": {
                "b0_s3_failed": b0_s3_failed,
                "bad_memory_s3_failed": bad_memory_s3_failed,
            },
            "single_event_results": [],
            "reasons": [],
        }

    monkeypatch.setattr(batch_validate, "_bad_memory_s3_failed", bad_memory_stub)

    summary = batch_validate.run_batch(
        source,
        dry=False,
        workers=1,
        validation_root=tmp_path / "validation",
        validate_sequence=live_stub,
        construct_check=True,
        construct_validator=construct_stub,
    )

    verdicts = {verdict["seq_id"]: verdict for verdict in summary["verdicts"]}
    assert bad_memory_calls == [c9_record["seq_id"]]
    assert verdicts[c9_record["seq_id"]]["bad_memory_s3_failed"] is True
    assert verdicts[c3_record["seq_id"]]["bad_memory_s3_failed"] is None
    assert construct_calls == [
        (c9_record["seq_id"], False, True),
        (c3_record["seq_id"], True, None),
    ]

    payload = json.loads(Path(summary["output_path"]).read_text(encoding="utf-8"))
    payload_verdicts = {verdict["seq_id"]: verdict for verdict in payload["verdicts"]}
    assert payload_verdicts[c9_record["seq_id"]]["bad_memory_s3_failed"] is True
    assert payload_verdicts[c3_record["seq_id"]]["bad_memory_s3_failed"] is None


def test_construct_check_promotes_c9_when_construct_signature_is_valid(tmp_path: Path, monkeypatch: Any) -> None:
    record = {
        **_record("v2-c9-fixture-valid-signature-01"),
        "construct_label": "C9",
    }
    source = _write_jsonl(tmp_path / "candidates.jsonl", [record])

    def bad_memory_stub(checked_record: Mapping[str, Any]) -> tuple[bool, str | None]:
        assert checked_record["seq_id"] == record["seq_id"]
        return True, None

    def live_stub(checked_record: Mapping[str, Any], *, live: bool) -> dict[str, Any]:
        assert live is True
        return {
            "seq_id": checked_record["seq_id"],
            "valid": False,
            "b0_s3_failed": False,
            "refsol_passes": True,
            "oracle_hidden": True,
            "reasons": ["B0 S3 task success was 1/1; expected 0/1"],
        }

    def construct_stub(
        checked_record: Mapping[str, Any],
        b0_s3_failed: Any,
        bad_memory_s3_failed: Any,
    ) -> dict[str, Any]:
        assert checked_record["seq_id"] == record["seq_id"]
        assert b0_s3_failed is False
        assert bad_memory_s3_failed is True
        return {
            "seq_id": checked_record["seq_id"],
            "valid": True,
            "checked": True,
            "skipped": False,
            "checks": {
                "b0_expectation": "PASS",
                "b0_expectation_met": True,
                "b0_s3_failed": False,
                "bad_memory_s3_failed": True,
            },
            "single_event_results": [],
            "reasons": [],
        }

    monkeypatch.setattr(batch_validate, "_bad_memory_s3_failed", bad_memory_stub)

    summary = batch_validate.run_batch(
        source,
        dry=False,
        workers=1,
        validation_root=tmp_path / "validation",
        validate_sequence=live_stub,
        construct_check=True,
        construct_validator=construct_stub,
    )

    verdict = summary["verdicts"][0]
    assert summary["valid_count"] == 1
    assert summary["invalid_count"] == 0
    assert verdict["valid"] is True
    assert verdict["construct_validity_valid"] is True
    assert verdict["reasons"] == []


def test_construct_check_does_not_promote_when_refsol_or_oracle_hygiene_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    record = {
        **_record("v2-c10-fixture-valid-construct-bad-refsol-01"),
        "construct_label": "C10",
    }
    source = _write_jsonl(tmp_path / "candidates.jsonl", [record])

    def bad_memory_stub(checked_record: Mapping[str, Any]) -> tuple[bool, str | None]:
        return True, None

    def live_stub(checked_record: Mapping[str, Any], *, live: bool) -> dict[str, Any]:
        return {
            "seq_id": checked_record["seq_id"],
            "valid": False,
            "b0_s3_failed": False,
            "refsol_passes": False,
            "oracle_hidden": True,
            "reasons": ["reference solution s3 failed hidden oracle for fixture"],
        }

    def construct_stub(
        checked_record: Mapping[str, Any],
        b0_s3_failed: Any,
        bad_memory_s3_failed: Any,
    ) -> dict[str, Any]:
        return {
            "seq_id": checked_record["seq_id"],
            "valid": True,
            "checked": True,
            "skipped": False,
            "checks": {
                "b0_expectation": "PASS",
                "b0_expectation_met": True,
                "b0_s3_failed": False,
                "bad_memory_s3_failed": True,
            },
            "single_event_results": [],
            "reasons": [],
        }

    monkeypatch.setattr(batch_validate, "_bad_memory_s3_failed", bad_memory_stub)

    summary = batch_validate.run_batch(
        source,
        dry=False,
        workers=1,
        validation_root=tmp_path / "validation",
        validate_sequence=live_stub,
        construct_check=True,
        construct_validator=construct_stub,
    )

    verdict = summary["verdicts"][0]
    assert summary["valid_count"] == 0
    assert summary["invalid_count"] == 1
    assert verdict["valid"] is False
    assert verdict["construct_validity_valid"] is True
    assert "reference solution s3 failed hidden oracle for fixture" in verdict["reasons"]
