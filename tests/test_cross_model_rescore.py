"""Synthetic tests for the Phase C cross-model hygiene rescore harness."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cross_model_rescore as cmr  # noqa: E402


def _sequence() -> dict:
    return {
        "seq_id": "mock-seq",
        "sessions": [
            {"session_index": 2, "visible_files": ["app.py"]},
            {"session_index": 3, "visible_files": ["app.py"]},
        ],
        "events": [
            {
                "content": "Reviewer requires exact marker TOKEN-GOOD.",
                "scope": {
                    "repo": "mock",
                    "sequence_id": "mock-seq",
                    "files": ["app.py"],
                    "symbols": ["render"],
                },
            }
        ],
        "oracle_labels": [
            {
                "label_id": "mock-stale",
                "type": "stale",
                "active_session": 3,
                "stale_fact": "Use TOKEN-OLD.",
                "active_fact": "Use TOKEN-GOOD.",
            },
            {
                "label_id": "mock-harmful",
                "type": "harmful-retrieval",
                "active_session": 3,
                "harmful_memory": "Use TOKEN-OLD.",
            },
            {
                "label_id": "mock-contradiction",
                "type": "contradiction",
                "active_session": 3,
                "old_memory": "Use TOKEN-OLD.",
                "new_memory": "Use TOKEN-GOOD.",
            },
            {
                "label_id": "mock-repeated",
                "type": "repeated-error-opportunity",
                "active_session": 3,
                "bad_action": "Missing TOKEN-GOOD repeats the previous failure.",
            },
            {
                "label_id": "mock-human",
                "type": "human-feedback",
                "active_session": 3,
                "required_memory": "Reviewer requires exact marker TOKEN-GOOD.",
            },
        ],
    }


def _patch(token: str | None) -> str:
    token_line = f'    return "{token}"' if token else '    return "TOKEN-BAD"'
    return (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1,3 @@\n"
        "+def render():\n"
        f"+{token_line}\n"
    )


def _record(condition: str, session_index: int, *, good: bool, useful_memory: bool, harmful_memory: bool) -> dict:
    memory_context = []
    if useful_memory:
        memory_context.append(
            {
                "id": f"{condition}-useful",
                "content": "Reviewer requires exact marker TOKEN-GOOD.",
                "status": "active",
                "repo_scope": "mock-seq",
                "file_scope": ["app.py"],
                "provenance": {"trajectory_ids": [f"mock-seq-s02-{condition}-gpt-5.5"]},
            }
        )
    if harmful_memory:
        memory_context.append(
            {
                "id": f"{condition}-harmful",
                "content": "Use TOKEN-OLD.",
                "status": "active",
                "repo_scope": "mock-seq",
                "file_scope": ["app.py"],
            }
        )
    return {
        "task": {
            "id": f"mock-seq-s{session_index:02d}",
            "seq_id": "mock-seq",
            "sequence_id": "mock-seq",
            "session_index": session_index,
            "files": ["app.py"],
        },
        "condition": condition,
        "isolation_mode": "container",
        "memory_context": memory_context,
        "memory_writes": [],
        "score": {"production_diff": _patch("TOKEN-GOOD" if good else None)},
        "oracle": {"passed": good, "stdout": "", "stderr": "" if good else "regress prior behavior"},
        "final_passed": good,
        "pass_at_1": good,
        "started_from_previous_session": session_index > 1,
        "previous_session_end_commit": "s2" if session_index > 1 else None,
        "forbidden_fresh_base_ref_used": False,
    }


def _write_synthetic_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    sequence_path = tmp_path / "sequences.jsonl"
    sequence_path.write_text(json.dumps(_sequence()) + "\n", encoding="utf-8")
    results_root = tmp_path / "results"
    for condition, good, useful_memory, harmful_memory in [
        ("B0", False, False, False),
        ("B5", False, False, True),
        ("DF", True, True, False),
    ]:
        condition_dir = results_root / condition
        condition_dir.mkdir(parents=True)
        records = [
            _record(condition, 2, good=False, useful_memory=False, harmful_memory=harmful_memory),
            _record(condition, 3, good=good, useful_memory=useful_memory, harmful_memory=harmful_memory),
        ]
        (condition_dir / "results.json").write_text(
            json.dumps({"condition": condition, "records": records}),
            encoding="utf-8",
        )
    mock_responses = tmp_path / "mock_responses.json"
    mock_responses.write_text(
        json.dumps(
            [
                {"answer": False},
                {"answer": True},
                {"answer": False},
                {"answer": False},
                "not valid json",
                {"answer": False},
                {"answer": True},
                {"answer": False},
                {"answer": True},
            ]
        ),
        encoding="utf-8",
    )
    return sequence_path, results_root, mock_responses


def test_label_judge_adapter_abstains_on_malformed_response() -> None:
    adapter = cmr.LabelJudgeAdapter(
        client=cmr.MockTextJudgeClient(["not valid json"]),
        judge_selector="glm-latest",
        max_retries=0,
    )

    result = adapter(
        {
            "question": "contradiction_repair",
            "payload": {
                "condition": "DF",
                "sequence_id": "mock-seq",
                "label": {"label_id": "mock-contradiction"},
            },
            "fallback_answer": True,
        }
    )

    assert result["answer"] is None
    assert result["status"] == "abstain"
    assert adapter.calls[0].status == "abstain"


def test_cross_model_rescore_cli_mock_end_to_end(tmp_path: Path) -> None:
    sequence_path, results_root, mock_responses = _write_synthetic_inputs(tmp_path)
    out_json = tmp_path / "cross_model_rescore.json"
    out_md = tmp_path / "cross_model_rescore.md"
    env = {
        **os.environ,
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
    }

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "cross_model_rescore.py"),
            str(results_root),
            "--judge",
            "glm-latest",
            "--sequence-records",
            str(sequence_path),
            "--mock-judge-responses",
            str(mock_responses),
            "--max-retries",
            "0",
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ],
        text=True,
        capture_output=True,
        env=env,
        cwd=ROOT,
        check=True,
    )

    assert "CROSS_MODEL_RESCORE complete" in completed.stdout
    assert "Pass@1 untouched: true" in completed.stdout
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["metadata"]["executable_pass_at_1_untouched"] is True
    assert payload["judge_calls"]["abstentions"] == 1
    b5_repeated = payload["conditions"]["B5"]["cross_model_metrics"]["RepeatedErrorRate"]
    assert b5_repeated["denominator"] == 0
    assert payload["survival"]["task_coupled_family_win_survives"] is True
    assert payload["survival"]["independent_family_4_of_5_survives"] is True
    assert out_md.exists()
