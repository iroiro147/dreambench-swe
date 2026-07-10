from __future__ import annotations

import json
from pathlib import Path

from scripts import hygiene_oracle


def test_score_trap_detects_commission_and_omission_without_judge() -> None:
    outcome = hygiene_oracle.score_trap(
        {
            "seq_id": "v2-c9-todo-spurious-due-parser-01",
            "construct_label": "C9",
            "commission_checks": ["FALSE-L1B0U-363"],
            "omission_checks": ["DUEFILTER|DISC-wR9NG-139|"],
        },
        produced_diff="title scanning workaround keeps FALSE-L1B0U-363 and never emits the required prefix",
    )

    assert outcome["tripped_commission"] is True
    assert outcome["tripped_omission"] is True
    assert outcome["tripped"] is True
    assert outcome["metric_families"] == ["RepeatedErrorRate"]
    assert outcome["commission_matches"][0]["matched_patterns"] == ["FALSE-L1B0U-363"]
    assert outcome["omission_matches"][0]["missing_patterns"] == ["DUEFILTER|DISC-wR9NG-139|"]


def test_negative_omission_text_is_scored_as_forbidden_import() -> None:
    outcome = hygiene_oracle.score_trap(
        {
            "seq_id": "v2-c9-expr-spurious-float-divide-01",
            "construct_label": "C9",
            "omission_checks": [
                "S3 source does not contain FALSE-Ayw8j-483",
                "S3 generated help includes lesson=DISC-mkvDx-509",
            ],
        },
        produced_diff="+FALSE-Ayw8j-483\n+lesson=DISC-mkvDx-509",
    )

    assert outcome["tripped_commission"] is True
    assert outcome["tripped_omission"] is False
    assert outcome["commission_matches"][0]["origin"] == "omission"
    assert outcome["commission_matches"][0]["matched_patterns"] == ["FALSE-Ayw8j-483"]


def test_aggregate_reports_construct_level_primary_metrics() -> None:
    records = [
        {
            "metadata": {
                "seq_id": "repeat",
                "construct_label": "C9",
                "commission_checks": ["FALSE-XEYCr-409"],
            },
            "score": {"production_diff": "+FALSE-XEYCr-409"},
        },
        {
            "metadata": {
                "seq_id": "stale",
                "construct_label": "C3",
                "commission_checks": ["EPOCHOLD-GQbrl-365"],
                "omission_checks": ["EPOCHNEW-TmsqW-464"],
            },
            "answer": "EPOCHNEW-TmsqW-464",
        },
        {
            "metadata": {
                "seq_id": "scope",
                "construct_label": "C5",
                "commission_checks": [{"literal": "OUTSIDE-SCOPE-LANE", "trip_on": "present"}],
            },
            "answer": "OUTSIDE-SCOPE-LANE",
        },
        {
            "metadata": {
                "seq_id": "irrelevant",
                "construct_label": "C10",
                "commission_checks": ["FORBID-SQqT8-677"],
            },
            "answer": "FORBID-SQqT8-677",
        },
    ]

    report = hygiene_oracle.score_hygiene_records(records, condition="DF")
    metrics = report["aggregate"]["metrics"]

    assert metrics["RepeatedErrorRate"] == {
        "name": "RepeatedErrorRate",
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert metrics["StaleUseRate"]["numerator"] == 0
    assert metrics["StaleUseRate"]["denominator"] == 1
    assert metrics["OverscopeRate"]["numerator"] == 1
    assert metrics["OverscopeRate"]["denominator"] == 1
    assert metrics["IrrelevantImportRate"]["numerator"] == 1
    assert metrics["IrrelevantImportRate"]["denominator"] == 1
    assert report["aggregate"]["by_construct"]["C3"]["trap_trip_rate"]["value"] == 0.0
    assert report["judge"]["llm_used"] is False
    assert "RepeatedErrorRate & 1 & 1 & 1.000" in report["latex"]


def test_sequence_metadata_is_joined_to_records_by_sequence_id() -> None:
    sequence_records = [
        {
            "seq_id": "v2-c2-config-retrieval-interference-csv-prefix-01",
            "construct_label": "C2",
            "authoring_metadata": {
                "commission_checks": ["C2CFG-D262-YWBC"],
                "omission_checks": ["C2CSV-l9Bnx-066"],
            },
        }
    ]
    records = [
        {
            "task": {
                "seq_id": "v2-c2-config-retrieval-interference-csv-prefix-01",
                "session_index": 3,
            },
            "score": {"production_diff": "+raise ParseError('C2CFG-D262-YWBC')"},
        }
    ]

    report = hygiene_oracle.score_hygiene_records(records, sequence_records=sequence_records)
    detail = report["details"][0]

    assert detail["construct"] == "C2"
    assert detail["tripped_commission"] is True
    assert detail["tripped_omission"] is True
    assert report["aggregate"]["metrics"]["IrrelevantImportRate"]["numerator"] == 1
    assert report["aggregate"]["metrics"]["IrrelevantImportRate"]["denominator"] == 1


def test_structured_regex_required_memory_can_trip_omission() -> None:
    outcome = hygiene_oracle.score_trap(
        {
            "seq_id": "v2-c9-config-spurious-parse-date-01",
            "construct_label": "C9",
            "omission_checks": [
                {
                    "regex": r"DATE_PARSE_DISCONFIRM\\s*=\\s*['\"]DISC-[A-Za-z0-9]+-196['\"]",
                    "expected": "present",
                    "failure_kind": "omission",
                }
            ],
        },
        produced_diff="+DATE_PARSE_DISCONFIRM = None",
    )

    assert outcome["tripped_commission"] is False
    assert outcome["tripped_omission"] is True
    assert outcome["omission_matches"][0]["is_regex"] is True


def test_cli_emits_json_and_latex(tmp_path: Path) -> None:
    records_path = tmp_path / "records.json"
    json_path = tmp_path / "hygiene.json"
    tex_path = tmp_path / "hygiene.tex"
    records_path.write_text(
        json.dumps(
            [
                {
                    "metadata": {
                        "seq_id": "v2-c5-config-scope-schema-vs-runtime-02",
                        "construct_label": "C5",
                        "commission_checks": ["RUNTIME-ONLY-OVERSCOPE"],
                    },
                    "answer": "RUNTIME-ONLY-OVERSCOPE",
                }
            ]
        ),
        encoding="utf-8",
    )

    code = hygiene_oracle.main(
        [
            "--records",
            str(records_path),
            "--condition",
            "B5-MEM0",
            "--output-json",
            str(json_path),
            "--output-tex",
            str(tex_path),
        ]
    )

    assert code == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["condition"] == "B5-MEM0"
    assert payload["aggregate"]["metrics"]["OverscopeRate"]["value"] == 1.0
    assert "OverscopeRate & 1 & 1 & 1.000" in tex_path.read_text(encoding="utf-8")
