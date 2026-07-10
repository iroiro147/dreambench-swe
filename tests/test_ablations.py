"""Focused tests for the DreamForge Phase-0 ablation conditions."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benchmarks.baselines import DF_TOKEN_BUDGET_READ, DreamForgePolicy  # noqa: E402
from dream_memory.llm_judge import (  # noqa: E402
    LLMJudge,
    _CONSOLIDATION_TAG,
    _CONTRADICTION_TAG,
    _REPLAY_TAG,
)
from dream_memory.schemas import MemoryStatus, MemoryType, Provenance, new_memory  # noqa: E402
from experiments import run_bench  # noqa: E402
from scripts import run_grid  # noqa: E402


SEQ = "ablation-seq"
FILES = ["src/config.py"]


class AblationJudgeStub:
    """Deterministic complete(prompt)->JSON stub for all DF sleep judges."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.calls_by_tag = {"consolidation": 0, "contradiction": 0, "replay": 0}

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if _CONSOLIDATION_TAG in prompt:
            self.calls_by_tag["consolidation"] += 1
            episode = _prompt_json(prompt, "RAW EPISODE:")
            content = str(episode.get("memory_content") or episode.get("prompt") or "Ablation memory.")
            memory_type = str(episode.get("memory_type") or "semantic_project")
            return json.dumps([
                {
                    "memory_type": memory_type,
                    "content": content,
                    "scope": {
                        "repo": "real-repo-name",
                        "files": episode.get("file_paths") or FILES,
                        "symbols": episode.get("symbols") or [],
                        "session_validity": "future",
                    },
                    "provenance": {
                        "trajectory_id": episode.get("trajectory_id"),
                        "event_id": f"{episode.get('task_id')}-event",
                        "quote": content,
                    },
                    "confidence": 0.95,
                    "risk_score": 0.05,
                    "staleness_score": 0.0,
                    "retrieval_tags": ["consolidation", SEQ, memory_type],
                }
            ])
        if _CONTRADICTION_TAG in prompt:
            self.calls_by_tag["contradiction"] += 1
            return json.dumps({
                "relation": "supersedes",
                "old_status": "superseded",
                "new_status": "active",
                "scope_delta": "newer ablation fixture supersedes older memory",
                "rationale": "The newer fixture is authoritative for this test.",
            })
        if _REPLAY_TAG in prompt:
            self.calls_by_tag["replay"] += 1
            episode = _prompt_json(prompt, "EPISODE:")
            return json.dumps({
                "bad_action": str(episode.get("bad_action") or "continued after failure evidence"),
                "evidence": str(episode.get("replay_evidence") or "Traceback: ablation failure"),
                "correct_alternative": "stop and revise the plan before retrying",
                "future_retrieval_condition": SEQ,
                "scope": {
                    "repo": "real-repo-name",
                    "files": episode.get("file_paths") or FILES,
                    "symbols": episode.get("symbols") or [],
                },
            })
        return "[]"


def _prompt_json(prompt: str, marker: str) -> Dict[str, Any]:
    tail = prompt.split(marker, 1)[1].strip()
    value, _ = json.JSONDecoder().raw_decode(tail)
    return dict(value)


def _policy(*, name: str = "DF", **flags: Any) -> tuple[DreamForgePolicy, AblationJudgeStub]:
    stub = AblationJudgeStub()
    policy = DreamForgePolicy(
        name=name,
        label=name,
        description=f"{name} test policy",
        llm_judge=LLMJudge(stub),
        maintenance_scope="local",
        **flags,
    )
    return policy, stub


def _episode(
    session: int,
    content: str,
    *,
    outcome: str = "success",
    files: List[str] | None = None,
    memory_type: str = "semantic_project",
) -> Dict[str, Any]:
    return {
        "trajectory_id": f"traj-{SEQ}-s{session}",
        "task_id": f"{SEQ}-s{session}",
        "sequence_id": SEQ,
        "repo_scope": SEQ,
        "file_paths": files or FILES,
        "symbols": ["Config"],
        "prompt": content,
        "observations": [content],
        "failure_observations": ["Traceback: ablation failure"] if outcome != "success" else [],
        "outcome": outcome,
        "memory_content": content,
        "memory_type": memory_type,
    }


def _scored_memory(
    *,
    content: str = "pytest parser config memory for ablation retrieval",
    repo_scope: str = SEQ,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    staleness_score: float = 0.0,
) -> Any:
    return new_memory(
        content=content,
        type=MemoryType.SEMANTIC_PROJECT,
        write_reason="ablation retrieval fixture",
        provenance=Provenance(
            trajectory_ids=["traj-retrieval-fixture"],
            task_ids=[f"{SEQ}-s1"],
            file_paths=FILES,
        ),
        status=status,
        repo_scope=repo_scope,
        file_scope=FILES,
        task_scope=[SEQ],
        confidence=1.0,
        utility_score=1.0,
        risk_score=0.0,
        staleness_score=staleness_score,
        retrieval_tags=[SEQ, "consolidation", "pytest", "parser", "config"],
    )


def _read_task(prompt: str = "Use the pytest parser config memory.") -> Dict[str, Any]:
    return {
        "id": f"{SEQ}-s3",
        "sequence_id": SEQ,
        "seq_type": "ablation",
        "prompt": prompt,
        "files": FILES,
    }


def test_a0_episodic_only_writes_raw_episode_and_runs_no_sleep_operators() -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A0 must not call consolidation, repair, or replay")

    policy = DreamForgePolicy(
        name="A0",
        label="DF-A0",
        description="A0 test policy",
        extractor=explode,
        replay_judge=explode,
        repair_judge=explode,
        enable_consolidation=False,
    )

    policy.write(_episode(1, "Project fact: this repo uses pytest."))

    items = policy.memory_items()
    assert len(items) == 1
    assert items[0].type == MemoryType.EPISODIC
    assert items[0].status == MemoryStatus.ACTIVE
    assert "Project fact: this repo uses pytest" in items[0].content
    assert policy.last_write_items == items
    assert policy.last_write_count == 1


def test_a2_repair_disabled_leaves_contradicting_old_memory_active() -> None:
    full, _ = _policy(name="DF")
    full.write(_episode(1, "This repo uses Jest."))
    full.write(_episode(2, "This repo uses Vitest."))
    full_old = next(item for item in full.memory_items() if "Jest" in item.content)
    assert full_old.status == MemoryStatus.SUPERSEDED

    a2, stub = _policy(name="A2", enable_repair=False)
    a2.write(_episode(1, "This repo uses Jest."))
    a2.write(_episode(2, "This repo uses Vitest."))
    old = next(item for item in a2.memory_items() if "Jest" in item.content)
    new = next(item for item in a2.memory_items() if "Vitest" in item.content)

    assert old.status == MemoryStatus.ACTIVE
    assert old.superseded_by == []
    assert new.supersedes == []
    assert stub.calls_by_tag["contradiction"] == 0
    assert not any(item.type == MemoryType.CONTRADICTION for item in a2.memory_items())


def test_a4_replay_disabled_writes_no_dream_artifact_for_failure_trajectory() -> None:
    full, full_stub = _policy(name="DF")
    full.write(_episode(1, "Failure fixture should produce replay.", outcome="failure", memory_type="failure"))
    assert full_stub.calls_by_tag["replay"] >= 1
    assert any(item.type == MemoryType.DREAM_ARTIFACT for item in full.memory_items())

    a4, stub = _policy(name="A4", enable_replay=False)
    a4.write(_episode(1, "Failure fixture should not replay.", outcome="failure", memory_type="failure"))

    assert stub.calls_by_tag["replay"] == 0
    assert not any(item.type == MemoryType.DREAM_ARTIFACT for item in a4.memory_items())


def test_a5_stale_suppression_disabled_admits_superseded_memory_at_read() -> None:
    superseded = _scored_memory(status=MemoryStatus.SUPERSEDED)

    full = DreamForgePolicy(name="DF", label="DF", description="full")
    full.store.add(_scored_memory(status=MemoryStatus.SUPERSEDED))
    full_context = full.read(_read_task())
    full_decisions = {item["memory_id"]: item for item in full.last_retrieval_decisions}
    full_memory_id = full.memory_items()[0].id
    assert full_context == []
    assert full_decisions[full_memory_id]["reason"] == "status_superseded"

    a5 = DreamForgePolicy(
        name="A5",
        label="DF-A5",
        description="A5",
        enable_stale_suppression=False,
    )
    a5.store.add(superseded)
    context = a5.read(_read_task())

    assert [item["id"] for item in context] == [superseded.id]
    decisions = {item["memory_id"]: item for item in a5.last_retrieval_decisions}
    assert decisions[superseded.id]["admitted"] is True
    assert decisions[superseded.id]["reason"].startswith("stale_suppression_disabled:")


def test_a6_retrieval_gate_disabled_admits_wrong_scope_memory() -> None:
    wrong_scope = _scored_memory(repo_scope="other-sequence")

    full = DreamForgePolicy(name="DF", label="DF", description="full")
    full.store.add(_scored_memory(repo_scope="other-sequence"))
    full_context = full.read(_read_task())
    full_decisions = {item["memory_id"]: item for item in full.last_retrieval_decisions}
    full_memory_id = full.memory_items()[0].id
    assert full_context == []
    assert full_decisions[full_memory_id]["reason"] == "repo_scope_conflict"

    a6 = DreamForgePolicy(
        name="A6",
        label="DF-A6",
        description="A6",
        enable_retrieval_gate=False,
    )
    a6.store.add(wrong_scope)
    context = a6.read(_read_task())

    assert [item["id"] for item in context] == [wrong_scope.id]
    decisions = {item["memory_id"]: item for item in a6.last_retrieval_decisions}
    assert decisions[wrong_scope.id]["admitted"] is True
    assert decisions[wrong_scope.id]["reason"] == "retrieval_gate_disabled"


def test_a11_forced_consolidation_runs_repair_with_global_scope() -> None:
    old_local = _scored_memory(
        content="Old out-of-scope local fact.",
        repo_scope=SEQ,
    )
    old_local.file_scope = ["src/old.py"]

    full, _ = _policy(name="DF")
    full.store.add(old_local)
    full.write(_episode(2, "New in-scope fact.", files=["src/new.py"]))
    full_old = next(item for item in full.memory_items() if "Old out-of-scope" in item.content)
    assert full_old.status == MemoryStatus.ACTIVE

    old_global = _scored_memory(
        content="Old out-of-scope global fact.",
        repo_scope=SEQ,
    )
    old_global.file_scope = ["src/old.py"]

    a11, stub = _policy(name="A11", forced_consolidation=True)
    a11.store.add(old_global)
    a11.write(_episode(2, "New in-scope fact.", files=["src/new.py"]))
    a11_old = next(item for item in a11.memory_items() if "Old out-of-scope" in item.content)

    assert a11_old.status == MemoryStatus.SUPERSEDED
    assert stub.calls_by_tag["contradiction"] >= 1


def test_full_df_defaults_still_run_replay_and_filter_inactive_memories() -> None:
    policy = DreamForgePolicy(name="DF", label="DF", description="full defaults")
    policy.write(_episode(1, "Default DF failure replay fixture.", outcome="failure", memory_type="failure"))

    assert any(item.type == MemoryType.DREAM_ARTIFACT for item in policy.memory_items())

    stale = _scored_memory(status=MemoryStatus.SUPERSEDED)
    policy.store.add(stale)
    context = policy.read(_read_task("Use pytest parser config memory without stale cues."))
    context_ids = {item["id"] for item in context}
    decisions = {item["memory_id"]: item for item in policy.last_retrieval_decisions}

    assert stale.id not in context_ids
    assert decisions[stale.id]["admitted"] is False
    assert decisions[stale.id]["reason"] == "status_superseded"


def test_run_bench_constructs_ablation_conditions_as_df_variants() -> None:
    choices = run_bench._condition_choices(include_ablations=True)
    for name, flags in run_bench.DF_ABLATIONS.items():
        assert name in choices
        assert run_bench._normalize_condition(name.lower()) == name
        judge_client = run_bench._judge_client_for_condition(
            name,
            judge_model=run_bench.DEFAULT_JUDGE_MODEL,
            dry_run=True,
        )
        assert isinstance(judge_client, run_bench.StubJudgeClient)
        policy = run_bench._policy_for_condition(name, judge_client=judge_client)
        assert isinstance(policy, DreamForgePolicy)
        assert policy.name == name
        assert policy.label == f"DF-{name}"
        for flag, value in flags.items():
            assert getattr(policy, flag) == value


def test_run_bench_constructs_hybrid_conditions_as_df_variants() -> None:
    choices = run_bench._condition_choices(include_ablations=True)
    expected = {
        run_bench.DF_HYBRID_CONDITION,
        run_bench.DF_RAW_ONLY_CONDITION,
        run_bench.DF_STRICT_HYBRID_CONDITION,
    }
    assert expected.issubset(set(choices))

    for name in expected:
        assert run_bench._normalize_condition(name.lower()) == name
        judge_client = run_bench._judge_client_for_condition(
            name,
            judge_model=run_bench.DEFAULT_JUDGE_MODEL,
            dry_run=True,
        )
        assert isinstance(judge_client, run_bench.StubJudgeClient)
        policy = run_bench._policy_for_condition(name, judge_client=judge_client)
        assert isinstance(policy, DreamForgePolicy)
        assert policy.name == name
        assert policy.enable_raw_evidence is True
        assert policy.exclude_contradiction_from_read is True

    hybrid = run_bench._policy_for_condition(
        run_bench.DF_HYBRID_CONDITION,
        judge_client=run_bench.StubJudgeClient(),
    )
    assert hybrid.enable_consolidation is True
    assert hybrid.gate.theta_admit == 1.0
    assert hybrid.read_limit == 6
    assert hybrid.token_budget_read == DF_TOKEN_BUDGET_READ

    raw_only = run_bench._policy_for_condition(
        run_bench.DF_RAW_ONLY_CONDITION,
        judge_client=run_bench.StubJudgeClient(),
    )
    assert raw_only.enable_consolidation is False

    strict_hybrid = run_bench._policy_for_condition(
        run_bench.DF_STRICT_HYBRID_CONDITION,
        judge_client=run_bench.StubJudgeClient(),
    )
    assert strict_hybrid.enable_consolidation is True
    assert strict_hybrid.gate.theta_admit == run_bench.DF_STRICT_RETRIEVAL_CONFIG["theta_admit"]
    assert strict_hybrid.read_limit == run_bench.DF_STRICT_RETRIEVAL_CONFIG["read_limit"]
    assert strict_hybrid.token_budget_read == run_bench.DF_STRICT_RETRIEVAL_CONFIG["token_budget_read"]


def test_df_strict_is_registered_and_only_tightens_retrieval_strictness() -> None:
    choices = run_bench._condition_choices(include_ablations=True)
    assert run_bench.DF_STRICT_CONDITION in choices
    assert run_bench._normalize_condition("df-strict") == run_bench.DF_STRICT_CONDITION

    full_judge = run_bench._judge_client_for_condition(
        run_bench.DF_CONDITION,
        judge_model=run_bench.DEFAULT_JUDGE_MODEL,
        dry_run=True,
    )
    strict_judge = run_bench._judge_client_for_condition(
        run_bench.DF_STRICT_CONDITION,
        judge_model=run_bench.DEFAULT_JUDGE_MODEL,
        dry_run=True,
    )
    assert isinstance(full_judge, run_bench.StubJudgeClient)
    assert isinstance(strict_judge, run_bench.StubJudgeClient)

    full = run_bench._policy_for_condition(run_bench.DF_CONDITION, judge_client=full_judge)
    strict = run_bench._policy_for_condition(run_bench.DF_STRICT_CONDITION, judge_client=strict_judge)

    assert isinstance(full, DreamForgePolicy)
    assert isinstance(strict, DreamForgePolicy)
    assert strict.name == run_bench.DF_STRICT_CONDITION
    assert strict.label == "DreamForgeStrict"

    unchanged_flags = (
        "enable_consolidation",
        "enable_repair",
        "enable_replay",
        "enable_stale_suppression",
        "enable_retrieval_gate",
        "forced_consolidation",
        "enable_raw_evidence",
        "exclude_contradiction_from_read",
        "maintenance_scope",
    )
    for flag in unchanged_flags:
        assert getattr(strict, flag) == getattr(full, flag)

    assert strict.gate.theta_admit == run_bench.DF_STRICT_RETRIEVAL_CONFIG["theta_admit"]
    assert strict.gate.theta_admit > full.gate.theta_admit
    assert strict.read_limit == run_bench.DF_STRICT_RETRIEVAL_CONFIG["read_limit"]
    assert strict.read_limit < full.read_limit
    assert strict.token_budget_read == run_bench.DF_STRICT_RETRIEVAL_CONFIG["token_budget_read"]
    assert strict.token_budget_read < full.token_budget_read

    assert run_grid.parse_conditions("DF-strict") == [run_bench.DF_STRICT_CONDITION]
    command = run_grid.bench_command(
        unit=run_grid.WorkUnit(
            condition=run_bench.DF_STRICT_CONDITION,
            seed=7,
            group_index=1,
            group_total=1,
            seq_ids=("ablation-seq",),
        ),
        judge_model=run_bench.DEFAULT_JUDGE_MODEL,
        sequence_records=run_grid.REPO_ROOT / run_grid.DEFAULT_SEQUENCE_RECORDS,
        results_root=run_grid.REPO_ROOT / "experiments" / "results",
    )
    assert run_bench.DF_STRICT_CONDITION in command
    assert "--include-live-baselines" not in command
