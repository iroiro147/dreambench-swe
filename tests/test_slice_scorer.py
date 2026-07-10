"""Label-grounded scorer tests for the 08b memory-trap slice."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dream_memory.slice_scorer import HEADLINE_SLICE_METRICS, rescore_from_records, score_memory_trap_slice  # noqa: E402
from experiments.env import load_env  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SEQUENCES_PATH = ROOT / "experiments" / "env" / "sequences.jsonl"
REFSOL_ROOT = ROOT / "experiments" / "env" / "refsol"


GOOD_PATCH_BY_SEQUENCE = {
    "config-stale-merge": """diff --git a/configly/parser.py b/configly/parser.py
--- a/configly/parser.py
+++ b/configly/parser.py
@@ -1 +1,5 @@
+def merge_configs(base, override):
+    merged = Config(base)
+    merged.deep_update(override)
+    return Config(merged)
""",
    "todo-convention-aggregate": """diff --git a/todolite/cli.py b/todolite/cli.py
--- a/todolite/cli.py
+++ b/todolite/cli.py
@@ -1 +1,3 @@
+def stats(store):
+    return f"STATS|open={open_count}|done={done_count}|total={total}\\n"
""",
    "config-reviewer-strict-csv": """diff --git a/configly/parser.py b/configly/parser.py
--- a/configly/parser.py
+++ b/configly/parser.py
@@ -1 +1,4 @@
+def parse_csv(text, strict=True):
+    if strict:
+        raise ParseError(f"CSV_WIDTH line {line_no}: expected {expected} columns, got {actual}")
""",
    "expr-generated-category": """diff --git a/exprmini/operators.py b/exprmini/operators.py
--- a/exprmini/operators.py
+++ b/exprmini/operators.py
@@ -1 +1,6 @@
+OPERATORS["%"] = {
+    "name": "modulo", "precedence": 20, "associativity": "left", "category": "remainder"
+}
diff --git a/tools/generate_operator_help.py b/tools/generate_operator_help.py
--- a/tools/generate_operator_help.py
+++ b/tools/generate_operator_help.py
@@ -1 +1,2 @@
+operator_help_lines()
""",
    "todo-flaky-monotonic-ids": """diff --git a/todolite/store.py b/todolite/store.py
--- a/todolite/store.py
+++ b/todolite/store.py
@@ -1 +1,5 @@
+def add_task(store, title):
+    task_id = store["next_id"]
+    store["next_id"] = task_id + 1
+    return task_id
""",
}


def _session(sequence: dict, session_index: int) -> dict:
    matches = [
        session
        for session in sequence["sessions"]
        if int(session["session_index"]) == session_index
    ]
    assert len(matches) == 1
    return matches[0]


def _reference_solution_diff(seq_id: str, session_index: int) -> str:
    if not REFSOL_ROOT.is_dir():
        pytest.skip("reviewer-only reference solutions are not included in the public artifact")
    path = REFSOL_ROOT / seq_id / f"s{session_index}.py-diff"
    if not path.exists():
        pytest.skip(f"reviewer-only reference solution missing for {seq_id} session {session_index}")
    assert path.exists(), path
    return path.read_text(encoding="utf-8")


def _synthetic_s3_records(sequence_records: list[dict]) -> list[dict]:
    records = []
    for sequence in sequence_records:
        seq_id = sequence["seq_id"]
        session = _session(sequence, 3)
        events_text = " ".join(event["content"] for event in sequence["events"])
        records.append(
            {
                "task": {
                    "id": f"{seq_id}-s03",
                    "sequence_id": seq_id,
                    "seq_id": seq_id,
                    "seq_type": sequence.get("seq_type"),
                    "session_index": 3,
                    "files": list(session.get("visible_files") or []),
                },
                "condition": "DF",
                "memory_context": [
                    {
                        "id": f"{seq_id}-memory",
                        "content": events_text,
                        "status": "active",
                        "repo_scope": seq_id,
                        "file_scope": list(session.get("visible_files") or []),
                        "retrieval_tags": ["human_feedback", "contradiction"],
                    }
                ],
                "retrieval_decisions": [{"memory_id": f"{seq_id}-memory", "admitted": True}],
                "memory_writes": [
                    {
                        "id": f"{seq_id}-write",
                        "content": events_text,
                        "status": "active",
                        "repo_scope": seq_id,
                        "file_scope": list(session.get("visible_files") or []),
                    }
                ],
                "score": {
                    "passed": True,
                    "production_diff": _reference_solution_diff(seq_id, 3),
                    "production_files": list(session.get("visible_files") or []),
                },
                "oracle": {"passed": True, "stdout": "", "stderr": ""},
                "final_passed": True,
                "start_commit": f"{seq_id}-s2",
                "end_commit": f"{seq_id}-s3",
                "started_from_previous_session": True,
                "previous_session_end_commit": f"{seq_id}-s2",
                "forbidden_fresh_base_ref_used": False,
                "oracle_id": f"{seq_id}-s3",
                "continuation_advanced": True,
            }
        )
    return records


def test_slice_scorer_uses_oracle_label_denominators_for_all_headline_metrics() -> None:
    sequence_records = load_env.load_sequence_records(SEQUENCES_PATH)
    report = score_memory_trap_slice(
        sequence_records=sequence_records,
        records=_synthetic_s3_records(sequence_records),
        condition="DF",
    )

    metrics = report["aggregate"]["metrics"]
    for name in HEADLINE_SLICE_METRICS:
        assert metrics[name]["denominator"] > 0, name
        assert metrics[name]["value"] is not None, name


def test_correct_active_memory_is_not_counted_stale_or_harmful() -> None:
    sequence = {
        "seq_id": "config-stale-merge",
        "sessions": [{"session_index": 3, "visible_files": ["configly/parser.py"]}],
        "events": [
            {
                "content": "Maintainer decision: Config is now the active public architecture.",
                "scope": {
                    "repo": "configly",
                    "sequence_id": "config-stale-merge",
                    "files": ["configly/parser.py"],
                    "symbols": ["Config", "merge_configs"],
                },
            }
        ],
        "oracle_labels": [
            {
                "label_id": "config-stale-merge-stale",
                "type": "stale",
                "active_session": 3,
                "stale_fact": "new helpers may return plain dicts because Config is dict-compatible",
                "active_fact": "new composition APIs normalize inputs and return Config",
            },
            {
                "label_id": "config-stale-merge-harmful",
                "type": "harmful-retrieval",
                "active_session": 3,
                "harmful_memory": "new helpers may return plain dicts because Config is dict-compatible",
            },
        ],
    }
    record = {
        "task": {"seq_id": "config-stale-merge", "session_index": 3},
        "memory_context": [
            {
                "id": "correct-config-memory",
                "content": (
                    "Maintainer decision: Config is now the active public architecture. "
                    "Direct nested-dict indexing remains compatibility-only. "
                    "New public helpers should preserve Config helpers instead of returning plain dicts."
                ),
                "status": "active",
                "repo_scope": "config-stale-merge",
                "file_scope": ["configly/parser.py"],
            }
        ],
        "score": {"production_diff": GOOD_PATCH_BY_SEQUENCE["config-stale-merge"]},
        "final_passed": True,
    }

    report = score_memory_trap_slice(
        sequence_records=[sequence],
        records=[record],
        condition="DF",
    )

    metrics = report["aggregate"]["metrics"]
    assert metrics["StaleMemoryActivationRate"]["denominator"] == 1
    assert metrics["StaleMemoryActivationRate"]["numerator"] == 0
    assert metrics["HarmfulMemoryRate"]["denominator"] == 1
    assert metrics["HarmfulMemoryRate"]["numerator"] == 0


def test_contradiction_repair_written_in_s2_is_credited_at_s3() -> None:
    sequence = {
        "seq_id": "todo-convention-aggregate",
        "sessions": [
            {"session_index": 2, "visible_files": ["todolite/cli.py"]},
            {"session_index": 3, "visible_files": ["todolite/cli.py"]},
        ],
        "events": [
            {
                "content": "Scoped exception: aggregate commands use STATS.",
                "scope": {
                    "repo": "todolite",
                    "sequence_id": "todo-convention-aggregate",
                    "files": ["todolite/cli.py"],
                    "symbols": ["stats"],
                },
            }
        ],
        "oracle_labels": [
            {
                "label_id": "todo-convention-aggregate-contradiction",
                "type": "contradiction",
                "active_session": 3,
                "old_memory": "OK|verb|id|title applies to all commands",
                "new_memory": "OK format applies only to item-level commands",
            }
        ],
    }
    s2_record = {
        "task": {"seq_id": "todo-convention-aggregate", "session_index": 2},
        "memory_context": [],
        "memory_writes": [
            {
                "id": "old-ok-all-commands",
                "content": "OK|verb|id|title applies to all commands",
                "status": "superseded",
                "repo_scope": "todo-convention-aggregate",
                "file_scope": ["todolite/cli.py"],
                "superseded_by": ["new-ok-item-scope"],
                "write_reason": "contradiction repair supersede stale scope rationale",
            },
            {
                "id": "new-ok-item-scope",
                "content": "OK format applies only to item-level commands",
                "status": "active",
                "repo_scope": "todo-convention-aggregate",
                "file_scope": ["todolite/cli.py"],
                "supersedes": ["old-ok-all-commands"],
                "write_reason": "contradiction repair supersede stale scope rationale",
            },
        ],
    }
    s3_record = {
        "task": {"seq_id": "todo-convention-aggregate", "session_index": 3},
        "memory_context": [],
        "memory_writes": [],
        "score": {"production_diff": GOOD_PATCH_BY_SEQUENCE["todo-convention-aggregate"]},
        "final_passed": True,
    }

    report = score_memory_trap_slice(
        sequence_records=[sequence],
        records=[s2_record, s3_record],
        condition="DF",
    )

    metric = report["aggregate"]["metrics"]["ContradictionRepairAccuracy"]
    assert metric["denominator"] == 1
    assert metric["numerator"] == 1


def test_contradiction_repair_written_in_active_session_is_not_credited() -> None:
    sequence = {
        "seq_id": "todo-convention-aggregate",
        "sessions": [{"session_index": 3, "visible_files": ["todolite/cli.py"]}],
        "events": [
            {
                "content": "Scoped exception: aggregate commands use STATS.",
                "scope": {
                    "repo": "todolite",
                    "sequence_id": "todo-convention-aggregate",
                    "files": ["todolite/cli.py"],
                    "symbols": ["stats"],
                },
            }
        ],
        "oracle_labels": [
            {
                "label_id": "todo-convention-aggregate-contradiction",
                "type": "contradiction",
                "active_session": 3,
                "old_memory": "OK|verb|id|title applies to all commands",
                "new_memory": "OK format applies only to item-level commands",
            }
        ],
    }
    s3_record = {
        "task": {"seq_id": "todo-convention-aggregate", "session_index": 3},
        "memory_context": [],
        "memory_writes": [
            {
                "id": "old-ok-all-commands",
                "content": "OK|verb|id|title applies to all commands",
                "status": "superseded",
                "repo_scope": "todo-convention-aggregate",
                "file_scope": ["todolite/cli.py"],
                "superseded_by": ["new-ok-item-scope"],
                "write_reason": "contradiction repair supersede stale scope rationale",
            },
            {
                "id": "new-ok-item-scope",
                "content": "OK format applies only to item-level commands",
                "status": "active",
                "repo_scope": "todo-convention-aggregate",
                "file_scope": ["todolite/cli.py"],
                "supersedes": ["old-ok-all-commands"],
                "write_reason": "contradiction repair supersede stale scope rationale",
            },
        ],
        "score": {"production_diff": ""},
        "final_passed": False,
    }

    report = score_memory_trap_slice(
        sequence_records=[sequence],
        records=[s3_record],
        condition="DF",
    )

    metric = report["aggregate"]["metrics"]["ContradictionRepairAccuracy"]
    assert metric["denominator"] == 1
    assert metric["numerator"] == 0


def test_bad_diff_counts_as_stale_and_harmful_activation() -> None:
    sequence = {
        "seq_id": "config-stale-merge",
        "sessions": [{"session_index": 3, "visible_files": ["configly/parser.py"]}],
        "events": [],
        "oracle_labels": [
            {
                "label_id": "config-stale-merge-stale",
                "type": "stale",
                "active_session": 3,
                "stale_fact": "new helpers may return plain dicts because Config is dict-compatible",
            },
            {
                "label_id": "config-stale-merge-harmful",
                "type": "harmful-retrieval",
                "active_session": 3,
                "harmful_memory": "new helpers may return plain dicts because Config is dict-compatible",
            },
        ],
    }
    bad_patch = """diff --git a/configly/parser.py b/configly/parser.py
--- a/configly/parser.py
+++ b/configly/parser.py
@@ -1 +1,5 @@
+def merge_configs(base, override):
+    merged = dict(base)
+    merged.update(override)
+    return merged
"""
    record = {
        "task": {"seq_id": "config-stale-merge", "session_index": 3},
        "memory_context": [
            {
                "id": "stale-config-memory",
                "content": "new helpers may return plain dicts because Config is dict-compatible",
                "status": "active",
                "repo_scope": "config-stale-merge",
                "file_scope": ["configly/parser.py"],
            }
        ],
        "score": {"production_diff": bad_patch},
        "final_passed": False,
    }

    report = score_memory_trap_slice(
        sequence_records=[sequence],
        records=[record],
        condition="DF",
    )

    metrics = report["aggregate"]["metrics"]
    assert metrics["StaleMemoryActivationRate"]["denominator"] == 1
    assert metrics["StaleMemoryActivationRate"]["numerator"] == 1
    assert metrics["HarmfulMemoryRate"]["denominator"] == 1
    assert metrics["HarmfulMemoryRate"]["numerator"] == 1


def test_wrong_contract_without_retrieved_stale_memory_is_not_stale_or_harmful_activation() -> None:
    sequence = {
        "seq_id": "config-stale-merge",
        "sessions": [{"session_index": 3, "visible_files": ["configly/parser.py"]}],
        "events": [],
        "oracle_labels": [
            {
                "label_id": "config-stale-merge-stale",
                "type": "stale",
                "active_session": 3,
                "stale_fact": "new helpers may return plain dicts because Config is dict-compatible",
            },
            {
                "label_id": "config-stale-merge-harmful",
                "type": "harmful-retrieval",
                "active_session": 3,
                "harmful_memory": "new helpers may return plain dicts because Config is dict-compatible",
            },
        ],
    }
    bad_patch = """diff --git a/configly/parser.py b/configly/parser.py
--- a/configly/parser.py
+++ b/configly/parser.py
@@ -1 +1,5 @@
+def merge_configs(base, override):
+    merged = dict(base)
+    merged.update(override)
+    return merged
"""
    record = {
        "task": {"seq_id": "config-stale-merge", "session_index": 3},
        "memory_context": [],
        "score": {"production_diff": bad_patch},
        "final_passed": False,
    }

    report = score_memory_trap_slice(
        sequence_records=[sequence],
        records=[record],
        condition="DF",
    )

    metrics = report["aggregate"]["metrics"]
    assert metrics["StaleMemoryActivationRate"]["numerator"] == 0
    assert metrics["HarmfulMemoryRate"]["numerator"] == 0


def test_useful_memory_precision_is_retrieval_precision_and_scope_is_marked_derived() -> None:
    sequence = {
        "seq_id": "todo-reviewer-export-dialect",
        "sessions": [{"session_index": 3, "visible_files": ["todolite/cli.py"]}],
        "events": [
            {
                "content": "Reviewer requires export marker EXPORT-vQ7M2-L9Z.",
                "scope": {
                    "repo": "todolite",
                    "sequence_id": "todo-reviewer-export-dialect",
                    "files": ["todolite/cli.py"],
                    "symbols": ["export_tasks"],
                },
            }
        ],
        "oracle_labels": [
            {
                "label_id": "todo-reviewer-export-dialect-human",
                "type": "human-feedback",
                "active_session": 3,
                "required_memory": "Reviewer requires export marker EXPORT-vQ7M2-L9Z.",
            }
        ],
    }
    patch = """diff --git a/todolite/cli.py b/todolite/cli.py
--- a/todolite/cli.py
+++ b/todolite/cli.py
@@ -1 +1,3 @@
+def export_tasks(tasks):
+    return "EXPORT-vQ7M2-L9Z"
"""
    record = {
        "task": {"seq_id": "todo-reviewer-export-dialect", "session_index": 3},
        "memory_context": [
            {
                "id": "useful-prior",
                "content": "Reviewer requires export marker EXPORT-vQ7M2-L9Z.",
                "status": "active",
                "repo_scope": "todo-reviewer-export-dialect",
                "file_scope": ["todolite/cli.py"],
                "provenance": {"trajectory_ids": ["todo-reviewer-export-dialect-s02-DF-gpt-5.5"]},
            },
            {
                "id": "irrelevant-prior",
                "content": "Unrelated parser note for another behavior.",
                "status": "active",
                "repo_scope": "todo-reviewer-export-dialect",
                "file_scope": ["todolite/cli.py"],
                "provenance": {"trajectory_ids": ["todo-reviewer-export-dialect-s01-DF-gpt-5.5"]},
            },
        ],
        "score": {"production_diff": patch},
        "final_passed": False,
    }

    report = score_memory_trap_slice(
        sequence_records=[sequence],
        records=[record],
        condition="DF",
    )

    metrics = report["aggregate"]["metrics"]
    assert metrics["HumanFeedbackUseAccuracy"]["numerator"] == 1
    assert metrics["UsefulMemoryPrecision"]["numerator"] == 1
    assert metrics["UsefulMemoryPrecision"]["denominator"] == 2
    assert metrics["TransferScore"]["numerator"] == 1
    notes = report["aggregate"]["metric_independence"]
    assert notes["UsefulMemoryPrecision"]["status"] == "independent"
    assert notes["ScopeAccuracy"]["status"] == "derived"


def test_generic_detector_flags_wrong_token_patch_for_new_sequence() -> None:
    sequence_records = load_env.load_sequence_records(SEQUENCES_PATH)
    sequence = next(
        sequence
        for sequence in sequence_records
        if sequence["seq_id"] == "todo-reviewer-export-dialect"
    )
    bad_patch = """diff --git a/todolite/cli.py b/todolite/cli.py
--- a/todolite/cli.py
+++ b/todolite/cli.py
@@ -1 +1,7 @@
+def export_tasks(tasks):
+    rows = ["id,status,title"]
+    for task in tasks:
+        rows.append(f"{task.id},{task.status},{task.title}")
+    return "\\n".join(rows)
"""
    record = {
        "task": {"seq_id": "todo-reviewer-export-dialect", "session_index": 3},
        "memory_context": [
            {
                "id": "harmful-export-memory",
                "content": next(
                    label.get("harmful_memory")
                    for label in sequence["oracle_labels"]
                    if label.get("type") == "harmful-retrieval"
                ),
                "status": "active",
                "repo_scope": "todo-reviewer-export-dialect",
                "file_scope": ["todolite/cli.py"],
            }
        ],
        "memory_writes": [],
        "score": {"production_diff": bad_patch},
        "oracle": {"passed": False, "stdout": "", "stderr": "missing EXPORT-vQ7M2-L9Z"},
        "final_passed": False,
    }
    prior_record = {
        "task": {"seq_id": "todo-reviewer-export-dialect", "session_index": 2},
        "memory_context": [],
        "memory_writes": [],
        "score": {"production_diff": bad_patch},
        "oracle": {"passed": False, "stdout": "", "stderr": "missing EXPORT-vQ7M2-L9Z"},
        "final_passed": False,
    }

    report = rescore_from_records(
        records=[prior_record, record],
        sequence_records=[sequence],
        condition="B3",
    )

    metrics = report["aggregate"]["metrics"]
    assert metrics["RepeatedErrorRate"]["denominator"] == 1
    assert metrics["RepeatedErrorRate"]["numerator"] == 1
    assert metrics["HarmfulMemoryRate"]["denominator"] == 1
    assert metrics["HarmfulMemoryRate"]["numerator"] == 1

    repeated = next(
        label_result
        for detail in report["details"]
        for label_result in detail["label_results"]
        if label_result["label_type"] == "repeated-error-opportunity"
    )
    signals = repeated["deterministic_signals"]
    assert signals["bad_action"] is True
    assert "EXPORT-vQ7M2-L9Z" in signals["generic_required_markers"]
    assert "EXPORT-vQ7M2-L9Z" in signals["generic_missing_required_markers"]
