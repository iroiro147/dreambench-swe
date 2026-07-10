"""Regression tests for the live B5-MEM0 baseline wrapper."""
from __future__ import annotations

import builtins
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benchmarks.baselines import Mem0LiteralPolicy, Mem0Policy  # noqa: E402
from experiments import run_bench  # noqa: E402
from scripts import run_grid  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_mem0_isolation_env(monkeypatch) -> None:
    monkeypatch.delenv("DREAMBENCH_NAMESPACE_PREFIX", raising=False)
    monkeypatch.delenv("DREAMBENCH_MEM0_FIXTURE_ROOT", raising=False)


class FakeV3Mem0Client:
    def __init__(self, *, add_status: str = "SUCCEEDED", event_statuses: list[str] | None = None) -> None:
        self.search_calls = []
        self.add_calls = []
        self.event_calls = []
        self.add_status = add_status
        self.event_statuses = list(event_statuses or [])

    def search(self, **kwargs):
        self.search_calls.append(dict(kwargs))
        filters = kwargs.get("filters") if isinstance(kwargs.get("filters"), Mapping) else {}
        return {
            "results": [
                {
                    "id": "mem-1",
                    "memory": "Use explicit dataclass serializers.",
                    "score": 0.87,
                    "metadata": {"sequence_id": filters.get("user_id"), "files": ["src/example.py"]},
                }
            ]
        }

    def add(self, **kwargs):
        self.add_calls.append(dict(kwargs))
        return {"status": self.add_status, "event_id": "evt-1"}

    def get_event(self, event_id: str) -> dict[str, Any]:
        self.event_calls.append(event_id)
        status = self.event_statuses.pop(0) if self.event_statuses else "SUCCEEDED"
        return {"id": event_id, "status": status}


class StoringMem0Client:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, **kwargs):
        messages = kwargs["messages"]
        self.rows.append({
            "id": f"mem-{len(self.rows) + 1}",
            "user_id": kwargs["user_id"],
            "memory": messages[0]["content"],
            "metadata": kwargs.get("metadata") or {},
            "score": 1.0,
        })
        return {"status": "PENDING", "event_id": "evt-local"}

    def search(self, **kwargs):
        filters = kwargs.get("filters") if isinstance(kwargs.get("filters"), Mapping) else {}
        user_id = filters.get("user_id")
        return {"results": [row for row in self.rows if row["user_id"] == user_id][: kwargs.get("top_k", 6)]}

    def get_all(self, **kwargs):
        filters = kwargs.get("filters") if isinstance(kwargs.get("filters"), Mapping) else {}
        user_id = filters.get("user_id")
        return {"results": [row for row in self.rows if row["user_id"] == user_id]}


class LiteralPreservingMem0Client:
    def __init__(self, exact_token: str) -> None:
        self.exact_token = exact_token
        self.rows: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []

    def add(self, **kwargs):
        self.add_calls.append(dict(kwargs))
        messages = kwargs["messages"]
        raw_content = messages[0]["content"]
        if kwargs.get("infer") is False:
            memory = raw_content
        else:
            memory = raw_content.replace(self.exact_token, self.exact_token.replace("::", " ").replace(".", " "))
        self.rows.append({
            "id": f"lit-{len(self.rows) + 1}",
            "user_id": kwargs["user_id"],
            "memory": memory,
            "metadata": kwargs.get("metadata") or {},
            "score": 1.0,
        })
        return {"status": "SUCCEEDED", "id": self.rows[-1]["id"]}

    def search(self, **kwargs):
        self.search_calls.append(dict(kwargs))
        filters = kwargs.get("filters") if isinstance(kwargs.get("filters"), Mapping) else {}
        user_id = filters.get("user_id")
        return {"results": [row for row in self.rows if row["user_id"] == user_id][: kwargs.get("top_k", 6)]}


def test_mem0_policy_is_offline_safe_without_package_or_key(monkeypatch) -> None:
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "mem0":
            raise ModuleNotFoundError("No module named 'mem0'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)

    policy = Mem0Policy(name="B5-MEM0", label="Mem0", description="x")

    assert policy.read({"prompt": "t"}) == []
    policy.write({"task_id": "offline-seq-s01", "prompt": "t"})
    assert policy.last_write_count == 0


def test_mem0_fixture_root_defaults_to_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DREAMBENCH_MEM0_FIXTURE_ROOT", str(tmp_path))

    policy = Mem0Policy(name="B5-MEM0", label="Mem0", description="x")

    assert policy.cache_root == tmp_path


def test_mem0_policy_uses_v3_search_signature_and_budget(tmp_path: Path) -> None:
    client = FakeV3Mem0Client()
    policy = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=client,
        cache_root=tmp_path,
        run_id="run-abc",
        condition_id="B5-MEM0",
        seed=7,
    )

    context = policy.read({"id": "alpha-seq-s02", "prompt": "serializer convention"})

    namespace = "df3seed__run-abc__B5-MEM0__seed-7__alpha-seq"
    assert namespace.startswith("df3seed__")
    assert client.search_calls == [
        {"query": "serializer convention", "filters": {"user_id": namespace}, "top_k": 6}
    ]

    assert context[0]["id"] == "mem-1"
    assert context[0]["kind"] == "memory"
    assert context[0]["content"] == "Use explicit dataclass serializers."
    assert context[0]["repo_scope"] == "alpha-seq"
    assert context[0]["file_scope"] == ["src/example.py"]
    assert context[0]["score"] == 0.87
    assert policy.last_retrieval_decisions == [
        {
            "memory_id": "mem-1",
            "admitted": True,
            "reason": "mem0_search",
            "score": 0.87,
            "repo_scope": "alpha-seq",
        }
    ]

    cached = sorted((tmp_path / namespace).glob("*.json"))
    assert len(cached) == 1
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in cached]
    assert {payload["op"] for payload in payloads} == {"read"}
    assert all(payload["namespace"] == namespace for payload in payloads)


def test_mem0_policy_namespace_isolates_run_condition_seed_and_sequence(tmp_path: Path) -> None:
    first_client = FakeV3Mem0Client()
    second_client = FakeV3Mem0Client()
    first = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=first_client,
        cache_root=tmp_path,
        run_id="run-1",
        condition_id="B5-MEM0",
        seed=1,
    )
    second = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=second_client,
        cache_root=tmp_path,
        run_id="run-2",
        condition_id="DF",
        seed=2,
    )

    first.read({"id": "alpha-seq-s02", "prompt": "serializer convention"})
    second.read({"id": "alpha-seq-s02", "prompt": "serializer convention"})

    first_ns = first_client.search_calls[0]["filters"]["user_id"]
    second_ns = second_client.search_calls[0]["filters"]["user_id"]
    assert first_ns == "df3seed__run-1__B5-MEM0__seed-1__alpha-seq"
    assert second_ns == "df3seed__run-2__DF__seed-2__alpha-seq"
    assert first_ns != second_ns


def test_mem0_write_sanitizes_hidden_oracle_material_from_payload(tmp_path: Path) -> None:
    client = FakeV3Mem0Client()
    policy = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=client,
        cache_root=tmp_path,
        run_id="run-clean",
        condition_id="B5-MEM0",
        seed=3,
    )

    policy.write(
        {
            "task_id": "alpha-seq",
            "benchmark_task_id": "alpha-seq-s01",
            "sequence_id": "alpha-seq",
            "condition_id": "B5-MEM0",
            "seed": 3,
            "prompt": "Implement the public serializer convention.",
            "actions": ["edited src/example.py"],
            "agent_patch": "diff --git a/src/example.py b/src/example.py\n+PUBLIC_PATCH_MARKER\n",
            "production_diff": "diff --git a/src/example.py b/src/example.py\n+PUBLIC_PRODUCTION_DIFF\n",
            "observations": [
                "HIDDEN_ORACLE_MARKER should never leave the harness",
                "<REVIEWER_ONLY_ORACLES>/alpha.py revealed stderr",
            ],
            "failure_observations": ["REFSOL_SECRET_MARKER should never leave the harness"],
            "reference_patch": "REFERENCE_PATCH_SECRET",
            "oracle_cmd": "python hidden_oracle.py",
            "outcome": "failure",
            "error_type": "oracle_failed",
            "injected_memory_event": {
                "event_type": "human_feedback",
                "content": "INTENDED_EVENT_TEXT is allowed",
                "labels": ["hidden-answer-label-not-allowed"],
            },
        }
    )

    assert len(client.add_calls) == 1
    add_call = client.add_calls[0]
    content = add_call["messages"][0]["content"]
    assert add_call["user_id"] == "df3seed__run-clean__B5-MEM0__seed-3__alpha-seq"
    assert add_call["metadata"] == {
        "benchmark": "dreambench-swe",
        "condition": "B5-MEM0",
        "seed": 3,
        "sequence_id": "alpha-seq",
        "run_id": "run-clean",
    }
    assert "Implement the public serializer convention" in content
    assert "edited src/example.py" in content
    assert "PUBLIC_PATCH_MARKER" in content
    assert "PUBLIC_PRODUCTION_DIFF" in content
    assert "The task outcome was failed." in content
    assert "The public error type was oracle_failed." in content
    assert "INTENDED_EVENT_TEXT is allowed" in content
    assert "sequence_id=" not in content
    assert "intended_memory_events:" not in content
    assert "- human_feedback:" not in content
    assert "HIDDEN_ORACLE_MARKER" not in content
    assert "REFSOL_SECRET_MARKER" not in content
    assert "REFERENCE_PATCH_SECRET" not in content
    assert "hidden-answer-label-not-allowed" not in content
    assert "oracle_cmd" not in content
    assert "experiments/env/oracles" not in content


def test_mem0_write_unwraps_episode_mapping_and_sends_verbatim_event_text(tmp_path: Path) -> None:
    client = FakeV3Mem0Client()
    policy = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=client,
        cache_root=tmp_path,
        run_id="run-wrapper",
        condition_id="B5-MEM0",
        seed=8,
    )
    contract = "Reviewer contract: return token MEM0-WRAPPER-TOKEN whenever this sequence asks for proof."

    policy.write({
        "episode": {
            "task_id": "wrapper-seq-s01",
            "benchmark_task_id": "wrapper-seq-s01",
            "sequence_id": "wrapper-seq",
            "prompt": "Implement the public reviewer-contract behavior.",
            "outcome": "success",
            "injected_memory_event": {
                "event_type": "human_feedback",
                "content": contract,
            },
        }
    })

    assert len(client.add_calls) == 1
    add_call = client.add_calls[0]
    content = add_call["messages"][0]["content"]
    assert add_call["user_id"] == "df3seed__run-wrapper__B5-MEM0__seed-8__wrapper-seq"
    assert content.splitlines()[0] == contract
    assert "For sequence wrapper-seq, task wrapper-seq-s01" in content
    assert "sequence_id=" not in content
    assert "intended_memory_events:" not in content
    assert "- human_feedback:" not in content
    assert "MEM0-WRAPPER-TOKEN" in content


def test_mem0_write_then_read_retrieves_stored_contract_token(tmp_path: Path) -> None:
    client = StoringMem0Client()
    policy = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=client,
        cache_root=tmp_path,
        run_id="run-e2e",
        condition_id="B5-MEM0",
        seed=9,
    )
    token = "MEM0-E2E-TOKEN"
    contract = f"Reviewer contract: return token {token} whenever this sequence asks for proof."

    policy.write({
        "episode": {
            "task_id": "e2e-seq-s01",
            "benchmark_task_id": "e2e-seq-s01",
            "sequence_id": "e2e-seq",
            "prompt": "Implement the public reviewer-contract behavior.",
            "outcome": "success",
            "injected_memory_event": {
                "event_type": "human_feedback",
                "content": contract,
            },
        }
    })
    context = policy.read({
        "id": "e2e-seq-s02",
        "sequence_id": "e2e-seq",
        "prompt": "What reviewer contract token must be returned for this sequence?",
    })

    assert policy.last_write_count == 1
    assert len(client.get_all(filters={"user_id": "df3seed__run-e2e__B5-MEM0__seed-9__e2e-seq"})["results"]) == 1
    assert len(context) == 1
    assert token in context[0]["content"]


def test_mem0_literal_policy_disables_inference_and_preserves_exact_token(tmp_path: Path) -> None:
    token = "MEM0-LIT_EXACT_TOKEN::Alpha-09.q7"
    contract = f"Reviewer contract: return exact token {token} without normalization."
    client = LiteralPreservingMem0Client(exact_token=token)
    policy = Mem0LiteralPolicy(
        name="B5-MEM0-LIT",
        label="Mem0Literal",
        description="x",
        client=client,
        cache_root=tmp_path,
        run_id="run-lit",
        condition_id="B5-MEM0-LIT",
        seed=11,
    )

    policy.write({
        "episode": {
            "task_id": "lit-seq-s01",
            "benchmark_task_id": "lit-seq-s01",
            "sequence_id": "lit-seq",
            "prompt": "Implement exact-token recall.",
            "outcome": "success",
            "injected_memory_event": {
                "event_type": "human_feedback",
                "content": contract,
            },
        }
    })
    context = policy.read({
        "id": "lit-seq-s02",
        "sequence_id": "lit-seq",
        "prompt": f"What exact reviewer token must be returned? {token}",
    })

    namespace = "df3seed__run-lit__B5-MEM0-LIT__seed-11__lit-seq"
    assert policy.last_write_count == 1
    assert policy.mem0_config["storage_mode"] == "literal_raw_text"
    assert policy.mem0_config["add_kwargs"]["infer"] is False
    assert len(client.add_calls) == 1
    add_call = client.add_calls[0]
    assert add_call["infer"] is False
    assert add_call["user_id"] == namespace
    assert add_call["metadata"]["mem0_storage_mode"] == "literal_raw_text"
    assert add_call["metadata"]["mem0_infer"] is False
    assert add_call["messages"][0]["content"].splitlines()[0] == contract
    assert client.search_calls == [
        {"query": f"What exact reviewer token must be returned? {token}", "filters": {"user_id": namespace}, "top_k": 6}
    ]
    assert len(context) == 1
    assert context[0]["content"].splitlines()[0] == contract
    assert token in context[0]["content"]


def test_mem0_write_accepts_pending_add_without_polling_or_blocking(tmp_path: Path) -> None:
    client = FakeV3Mem0Client(add_status="PENDING", event_statuses=["PENDING", "SUCCEEDED"])
    policy = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=client,
        cache_root=tmp_path,
        run_id="run-poll",
        condition_id="B5-MEM0",
        seed=4,
        event_poll_interval_seconds=0.0,
        event_poll_timeout_seconds=1.0,
    )

    policy.write(
        {
            "task_id": "alpha-seq",
            "benchmark_task_id": "alpha-seq-s01",
            "sequence_id": "alpha-seq",
            "prompt": "Implement the serializer convention.",
            "actions": ["edited src/example.py"],
            "outcome": "success",
        }
    )

    namespace = "df3seed__run-poll__B5-MEM0__seed-4__alpha-seq"
    assert client.event_calls == []
    assert namespace not in policy._blocked_namespaces
    assert policy.last_write_count == 1
    assert policy.last_sleep_tokens > 0


def test_mem0_pending_add_namespace_remains_readable(tmp_path: Path) -> None:
    client = FakeV3Mem0Client(add_status="PENDING")
    policy = Mem0Policy(
        name="B5-MEM0",
        label="Mem0",
        description="x",
        client=client,
        cache_root=tmp_path,
        run_id="run-readable",
        condition_id="B5-MEM0",
        seed=5,
    )

    policy.write(
        {
            "task_id": "alpha-seq",
            "benchmark_task_id": "alpha-seq-s01",
            "sequence_id": "alpha-seq",
            "prompt": "Implement the serializer convention.",
            "actions": ["edited src/example.py"],
            "outcome": "success",
        }
    )

    namespace = "df3seed__run-readable__B5-MEM0__seed-5__alpha-seq"
    assert namespace not in policy._blocked_namespaces

    context = policy.read({"id": "alpha-seq-s02", "prompt": "serializer convention"})

    assert client.search_calls == [
        {"query": "serializer convention", "filters": {"user_id": namespace}, "top_k": 6}
    ]
    assert context[0]["content"] == "Use explicit dataclass serializers."


def test_run_bench_gates_mem0_live_condition(capsys, monkeypatch) -> None:
    with pytest.raises(SystemExit) as help_exit:
        run_bench._parse_args(["--help"])
    assert help_exit.value.code == 0
    assert "--include-live-baselines" in capsys.readouterr().out

    with pytest.raises(SystemExit) as no_flag_exit:
        run_bench._parse_args(["--condition", "B5-MEM0"])
    assert no_flag_exit.value.code == 2
    assert "B5-MEM0 requires --include-live-baselines" in capsys.readouterr().err

    with pytest.raises(SystemExit) as literal_no_flag_exit:
        run_bench._parse_args(["--condition", "B5-MEM0-LIT"])
    assert literal_no_flag_exit.value.code == 2
    assert "B5-MEM0-LIT requires --include-live-baselines" in capsys.readouterr().err

    args = run_bench._parse_args(["--include-live-baselines", "--condition", "B5-MEM0"])
    assert args.condition == "B5-MEM0"
    literal_args = run_bench._parse_args(["--include-live-baselines", "--condition", "B5-MEM0-LIT"])
    assert literal_args.condition == "B5-MEM0-LIT"
    assert run_bench._condition_choices() == [
        "B0",
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "B6",
        "B7",
        "DF",
        "DF-hybrid",
        "DF-raw-only",
        "DF-strict-hybrid",
    ]
    assert "B5-MEM0" in run_bench._condition_choices(include_live_baselines=True)
    assert "B5-MEM0-LIT" in run_bench._condition_choices(include_live_baselines=True)

    with pytest.raises(ValueError, match="requires --include-live-baselines"):
        run_bench._policy_for_condition("B5-MEM0")
    with pytest.raises(ValueError, match="requires --include-live-baselines"):
        run_bench._policy_for_condition("B5-MEM0-LIT")
    monkeypatch.setattr(Mem0Policy, "_construct_client", lambda self: None)
    monkeypatch.setattr(Mem0LiteralPolicy, "_construct_client", lambda self: None)
    assert isinstance(run_bench._policy_for_condition("B5-MEM0", include_live_baselines=True), Mem0Policy)
    literal_policy = run_bench._policy_for_condition("B5-MEM0-LIT", include_live_baselines=True)
    assert isinstance(literal_policy, Mem0LiteralPolicy)
    assert "infer=False" in literal_policy.description


def test_run_grid_accepts_mem0_and_threads_live_flag(capsys) -> None:
    with pytest.raises(SystemExit) as help_exit:
        run_grid.parse_args(["--help"])
    assert help_exit.value.code == 0
    assert "B5-MEM0" in capsys.readouterr().out

    conditions = run_grid.parse_conditions("B5-MEM0")
    assert conditions == ["B5-MEM0"]
    literal_conditions = run_grid.parse_conditions("B5-MEM0-LIT")
    assert literal_conditions == ["B5-MEM0-LIT"]
    command = run_grid.bench_command(
        unit=run_grid.WorkUnit(
            condition="B5-MEM0",
            seed=5,
            group_index=1,
            group_total=1,
            seq_ids=("alpha-seq",),
        ),
        judge_model="glm-latest",
        sequence_records=Path("experiments/env/sequences.jsonl"),
        results_root=Path("custom-results"),
    )
    assert "--include-live-baselines" in command
    assert command[command.index("--results-root") + 1] == "custom-results"

    literal_command = run_grid.bench_command(
        unit=run_grid.WorkUnit(
            condition="B5-MEM0-LIT",
            seed=5,
            group_index=1,
            group_total=1,
            seq_ids=("alpha-seq",),
        ),
        judge_model="glm-latest",
        sequence_records=Path("experiments/env/sequences.jsonl"),
        results_root=Path("custom-results"),
    )
    assert "--include-live-baselines" in literal_command


def _run_all() -> bool:
    with pytest.MonkeyPatch().context() as monkeypatch:
        test_mem0_policy_is_offline_safe_without_package_or_key(monkeypatch)
    with pytest.MonkeyPatch().context():
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            test_mem0_policy_scopes_calls_maps_context_and_caches(Path(tmp))
    print("test_mem0_policy.py: focused non-capsys tests passed")
    return True


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
