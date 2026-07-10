"""Tests for the v2 construct-validity gate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts import batch_validate, construct_validity


def _write_oracle(oracles_root: Path, seq_id: str, literals: Sequence[str], extra_text: str = "") -> None:
    oracle_dir = oracles_root / seq_id
    oracle_dir.mkdir(parents=True)
    oracle_text = "\n".join(f"assert {literal!r}" for literal in literals)
    if extra_text:
        oracle_text = f"{oracle_text}\n{extra_text}" if oracle_text else extra_text
    (oracle_dir / "s3_test.py").write_text(oracle_text, encoding="utf-8")


def _fixture_record(
    seq_id: str = "v2-c7-fixture-compose-01",
    *,
    construct_label: str = "C7",
    events: Sequence[Mapping[str, Any]] | None = None,
    required_fact_ids: Sequence[str] = ("alpha_fact", "beta_fact"),
    event_fact_map: Mapping[str, Sequence[str]] | None = None,
    decisive_oracle_literals: Sequence[str] = ("ALPHA_BETA_OK",),
    metadata_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fixture_events = list(
        events
        or [
            {
                "event_id": "e1",
                "after_session": 1,
                "content": "remember alpha contract",
            },
            {
                "event_id": "e2",
                "after_session": 2,
                "content": "remember beta contract",
            },
        ]
    )
    fixture_event_fact_map = dict(
        event_fact_map
        or {
            "e1": ["alpha_fact"],
            "e2": ["beta_fact"],
        }
    )
    metadata = {
        "insufficiency_mechanism": "fixture composition requires facts from two events",
        "required_fact_ids": list(required_fact_ids),
        "event_fact_map": fixture_event_fact_map,
        "decisive_oracle_literals": list(decisive_oracle_literals),
        "commission_checks": ["S3 output contains the expected construct behavior"],
        "omission_checks": ["S3 output omits the construct-specific trap behavior"],
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    return {
        "seq_id": seq_id,
        "seq_type": "reviewer-preference",
        "construct_label": construct_label,
        "repo": "fixture",
        "initial_commit": "seed",
        "sessions": [
            {"session_index": 1, "instruction": "s1", "oracle_id": f"{seq_id}-s1"},
            {"session_index": 2, "instruction": "s2", "oracle_id": f"{seq_id}-s2"},
            {"session_index": 3, "instruction": "s3", "oracle_id": f"{seq_id}-s3"},
        ],
        "events": fixture_events,
        "validation_metadata": metadata,
    }


def _rejecting_single_event_scorer(
    record: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event["event_id"],
            "passed": False,
            "error_type": "oracle_failed",
            "empty_production_diff": False,
            "production_files": ["fixture.py"],
        }
        for event in events
    ]


def test_construct_validity_passes_valid_c3_b0_fail_signature(tmp_path: Path) -> None:
    record = _fixture_record(
        "v2-c3-fixture-supersession-01",
        construct_label="C3",
        events=[
            {"event_id": "e-stale", "after_session": 1, "content": "old suffix fact"},
            {"event_id": "e-active", "after_session": 2, "content": "new suffix fact"},
        ],
        required_fact_ids=["stale_suffix", "active_suffix"],
        event_fact_map={"e-stale": ["stale_suffix"], "e-active": ["active_suffix"]},
        decisive_oracle_literals=["ACTIVE_SUFFIX_OK"],
        metadata_extra={"insufficiency_mechanism": "C3 supersession requires stale and active facts"},
    )
    _write_oracle(tmp_path, record["seq_id"], ["ACTIVE_SUFFIX_OK"])

    verdict = construct_validity.validate_record(
        record,
        b0_s3_failed=True,
        oracles_root=tmp_path,
        single_event_scorer=_rejecting_single_event_scorer,
    )

    assert verdict["valid"] is True
    assert verdict["signature"]["b0"] == "FAIL"
    assert verdict["checks"]["b0_expectation_met"] is True
    assert verdict["checks"]["single_event_copy_required"] is True
    assert verdict["checks"]["single_event_copy_rejected"] is True
    assert verdict["reasons"] == []


def test_construct_validity_passes_valid_c9_b0_pass_bad_memory_fail(tmp_path: Path) -> None:
    record = _fixture_record(
        "v2-c9-fixture-spurious-lesson-01",
        construct_label="C9",
        events=[
            {
                "event_id": "e-false",
                "after_session": 1,
                "content": "False diagnosis FALSE-aBcD1-123 says date parsing is broken.",
            },
            {
                "event_id": "e-disconfirm",
                "after_session": 2,
                "content": "Replay passed twice with no code change; use the normal parser path.",
            },
        ],
        required_fact_ids=["false_lesson", "disconfirmation"],
        event_fact_map={"e-false": ["false_lesson"], "e-disconfirm": ["disconfirmation"]},
        decisive_oracle_literals=["coerce_dates"],
        metadata_extra={
            "insufficiency_mechanism": "C9 rejects a disconfirmed false lesson",
            "bad_memory_s3_failed": True,
            "diagnostic_policy_failures": ["apply the false date parser lesson"],
        },
    )
    _write_oracle(
        tmp_path,
        record["seq_id"],
        ["coerce_dates"],
        extra_text='source = ""\nassert "FALSE-aBcD1-123" not in source',
    )

    verdict = construct_validity.validate_record(record, b0_s3_failed=False, oracles_root=tmp_path)

    assert verdict["valid"] is True
    assert verdict["signature"]["b0"] == "PASS"
    assert verdict["checks"]["b0_expectation_met"] is True
    assert verdict["checks"]["bad_memory_condition_failed"] is True
    assert verdict["checks"]["stored_token_required_by_s3_oracle"] is False
    assert verdict["checks"]["single_event_copy_required"] is False
    assert verdict["reasons"] == []


def test_construct_validity_flags_invalid_c9_required_stored_token(tmp_path: Path) -> None:
    record = _fixture_record(
        "v2-c9-fixture-token-inversion-01",
        construct_label="C9",
        events=[
            {
                "event_id": "e-false",
                "after_session": 1,
                "content": "False diagnosis FALSE-qWer1-234 says archive order is flaky.",
            },
            {
                "event_id": "e-disconfirm",
                "after_session": 2,
                "content": "Disconfirmation marker DISC-xYzA9-777 documents the stale lesson.",
            },
        ],
        required_fact_ids=["false_lesson", "disconfirmation"],
        event_fact_map={"e-false": ["false_lesson"], "e-disconfirm": ["disconfirmation"]},
        decisive_oracle_literals=["DISC-xYzA9-777"],
        metadata_extra={
            "insufficiency_mechanism": "C9 rejects a disconfirmed false lesson",
            "bad_memory_s3_failed": True,
            "diagnostic_policy_failures": ["apply the false archive order lesson"],
        },
    )
    _write_oracle(tmp_path, record["seq_id"], ["DISC-xYzA9-777"])

    verdict = construct_validity.validate_record(record, b0_s3_failed=False, oracles_root=tmp_path)

    assert verdict["valid"] is False
    assert verdict["checks"]["b0_expectation_met"] is True
    assert verdict["checks"]["bad_memory_condition_failed"] is True
    assert verdict["checks"]["stored_token_required_by_s3_oracle"] is True
    assert verdict["checks"]["stored_tokens_required_by_s3_oracle"] == ["DISC-xYzA9-777"]
    assert any("S3 oracle requires stored token(s)" in reason for reason in verdict["reasons"])


def test_construct_validity_passes_valid_c10_b0_pass_bad_memory_fail(tmp_path: Path) -> None:
    record = _fixture_record(
        "v2-c10-fixture-abstention-01",
        construct_label="C10",
        events=[
            {
                "event_id": "e-forbid",
                "after_session": 1,
                "content": "Irrelevant marker FORBID-aBcD1-123 belongs to another surface.",
            },
            {
                "event_id": "e-canary",
                "after_session": 2,
                "content": "Irrelevant canary CANARY-zYxW2-456 must not be imported.",
            },
        ],
        required_fact_ids=["irrelevant_marker", "irrelevant_canary"],
        event_fact_map={"e-forbid": ["irrelevant_marker"], "e-canary": ["irrelevant_canary"]},
        decisive_oracle_literals=["COUNT|open=1"],
        metadata_extra={
            "insufficiency_mechanism": "C10 abstains from irrelevant memories while taking visible action",
            "bad_memory_s3_failed": True,
            "diagnostic_policy_failures": ["import irrelevant C10 marker facts"],
        },
    )
    _write_oracle(
        tmp_path,
        record["seq_id"],
        ["COUNT|open=1"],
        extra_text='source = ""\nassert "FORBID-aBcD1-123" not in source\nassert "CANARY-zYxW2-456" not in source',
    )

    verdict = construct_validity.validate_record(record, b0_s3_failed=False, oracles_root=tmp_path)

    assert verdict["valid"] is True
    assert verdict["signature"]["b0"] == "PASS"
    assert verdict["checks"]["bad_memory_condition_failed"] is True
    assert verdict["checks"]["stored_token_required_by_s3_oracle"] is False
    assert verdict["checks"]["single_event_copy_required"] is False
    assert verdict["reasons"] == []


def test_construct_validity_summary_is_keyed_by_construct(tmp_path: Path) -> None:
    valid_c3 = _fixture_record("v2-c3-summary-valid", construct_label="C3")
    invalid_c9 = _fixture_record("v2-c9-summary-invalid", construct_label="C9")
    valid_c10 = _fixture_record("v2-c10-summary-valid", construct_label="C10")
    _write_oracle(tmp_path, valid_c3["seq_id"], ["ALPHA_BETA_OK"])
    _write_oracle(tmp_path, invalid_c9["seq_id"], ["ALPHA_BETA_OK"])
    _write_oracle(tmp_path, valid_c10["seq_id"], ["ALPHA_BETA_OK"])

    verdicts = [
        construct_validity.validate_record(
            valid_c3,
            b0_s3_failed=True,
            oracles_root=tmp_path,
            single_event_scorer=_rejecting_single_event_scorer,
        ),
        construct_validity.validate_record(invalid_c9, b0_s3_failed=True, oracles_root=tmp_path),
        construct_validity.validate_record(
            valid_c10,
            b0_s3_failed=False,
            bad_memory_s3_failed=True,
            oracles_root=tmp_path,
        ),
    ]

    summary = construct_validity.summarize(verdicts)

    assert summary["checked_count"] == 3
    assert summary["by_construct"]["C3"]["valid_count"] == 1
    assert summary["by_construct"]["C9"]["invalid_count"] == 1
    assert summary["by_construct"]["C10"]["valid_count"] == 1


def test_construct_validity_passes_necessary_but_insufficient_fixture(tmp_path: Path) -> None:
    record = _fixture_record()
    _write_oracle(tmp_path, record["seq_id"], ["ALPHA_BETA_OK"])

    verdict = construct_validity.validate_record(
        record,
        b0_s3_failed=True,
        oracles_root=tmp_path,
        single_event_scorer=_rejecting_single_event_scorer,
    )

    assert verdict["valid"] is True
    assert verdict["checks"]["b0_s3_failed"] is True
    assert verdict["checks"]["metadata_present"] is True
    assert verdict["checks"]["metadata_consistent"] is True
    assert verdict["checks"]["decisive_literals_in_oracle"] is True
    assert verdict["checks"]["required_facts_span_events"] is True
    assert verdict["checks"]["single_event_copy_rejected"] is True
    assert verdict["reasons"] == []


def test_construct_validity_fails_single_bit_trap(tmp_path: Path) -> None:
    record = _fixture_record("v2-c7-fixture-single-bit-01")
    _write_oracle(tmp_path, record["seq_id"], ["ALPHA_BETA_OK"])

    def single_bit_scorer(
        checked_record: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        assert checked_record["seq_id"] == record["seq_id"]
        return [
            {
                "event_id": event["event_id"],
                "passed": event["event_id"] == "e2",
                "error_type": None if event["event_id"] == "e2" else "oracle_failed",
                "empty_production_diff": False,
                "production_files": ["fixture.py"],
            }
            for event in events
        ]

    verdict = construct_validity.validate_record(
        record,
        b0_s3_failed=True,
        oracles_root=tmp_path,
        single_event_scorer=single_bit_scorer,
    )

    assert verdict["valid"] is False
    assert verdict["checks"]["single_event_copy_rejected"] is False
    assert verdict["single_event_results"][1]["passed"] is True
    assert "single-event verbatim copy passed S3 oracle: e2" in verdict["reasons"]


def test_construct_validity_fails_missing_metadata_fixture(tmp_path: Path) -> None:
    record = _fixture_record("v2-c7-fixture-missing-metadata-01")
    record.pop("validation_metadata")
    _write_oracle(tmp_path, record["seq_id"], ["ALPHA_BETA_OK"])

    verdict = construct_validity.validate_record(
        record,
        b0_s3_failed=True,
        oracles_root=tmp_path,
        single_event_scorer=_rejecting_single_event_scorer,
    )

    assert verdict["valid"] is False
    assert verdict["checks"]["metadata_present"] is False
    assert any("missing construct metadata key(s)" in reason for reason in verdict["reasons"])


def test_batch_validate_construct_check_folds_into_funnel(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    record = _fixture_record("v2-c7-fixture-batch-01")
    source.write_text(json.dumps(record) + "\n", encoding="utf-8")

    def passing_validator(checked_record: Mapping[str, Any], *, live: bool) -> dict[str, Any]:
        assert live is True
        return {
            "seq_id": checked_record["seq_id"],
            "valid": True,
            "b0_s3_failed": True,
            "refsol_passes": True,
            "oracle_hidden": True,
            "reasons": [],
        }

    def failing_construct_validator(
        checked_record: Mapping[str, Any],
        b0_s3_failed: Any,
        bad_memory_s3_failed: Any,
    ) -> dict[str, Any]:
        assert checked_record["seq_id"] == record["seq_id"]
        assert checked_record["construct_label"] == "C7"
        assert checked_record["validation_metadata"]["required_fact_ids"] == ["alpha_fact", "beta_fact"]
        assert b0_s3_failed is True
        assert bad_memory_s3_failed is None
        return {
            "seq_id": checked_record["seq_id"],
            "valid": False,
            "checked": True,
            "skipped": False,
            "checks": {"b0_s3_failed": True},
            "single_event_results": [],
            "reasons": ["fixture construct gate failed"],
        }

    summary = batch_validate.run_batch(
        source,
        dry=False,
        workers=1,
        validation_root=tmp_path / "validation",
        validate_sequence=passing_validator,
        construct_check=True,
        construct_validator=failing_construct_validator,
    )

    verdict = summary["verdicts"][0]
    assert summary["construct_check"] is True
    assert summary["construct_validity"]["checked_count"] == 1
    assert summary["construct_validity"]["invalid_count"] == 1
    assert summary["valid_count"] == 0
    assert summary["invalid_count"] == 1
    assert verdict["valid"] is False
    assert verdict["construct_validity_valid"] is False
    assert "construct-validity: fixture construct gate failed" in verdict["reasons"]
