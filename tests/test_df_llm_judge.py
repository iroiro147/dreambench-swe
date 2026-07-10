"""Acceptance tests for LLMJudge wiring — stdlib only, no live LLM, no network.

Tests:
  (a) DreamForge consolidation/repair/replay invoke the injected judge (not the
      regex default) when an LLMJudge is provided.
  (b) With maintenance_scope='local', an out-of-scope existing memory is NOT
      considered for contradiction repair while an in-scope one IS.
  (c) The deterministic fallback (no judge injected) still produces MemoryItems.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dream_memory.llm_judge import LLMJudge, _CONSOLIDATION_TAG, _CONTRADICTION_TAG, _REPLAY_TAG  # noqa: E402
from dream_memory.consolidation import sleep_pipeline  # noqa: E402
from dream_memory.contradiction_repair import ContradictionRepair, _memory_in_episode_scope  # noqa: E402
from dream_memory.counterfactual_replay import CounterfactualReplay  # noqa: E402
from dream_memory.memory_store import MemoryStore  # noqa: E402
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType, Provenance, new_memory  # noqa: E402
from benchmarks.baselines import DreamForgePolicy  # noqa: E402


# ---------------------------------------------------------------------------
# Shared stub complete() function
# ---------------------------------------------------------------------------

def _make_stub_complete() -> tuple:
    """Return (complete_fn, call_log).

    complete_fn inspects the prompt tag and returns appropriate canned JSON.
    call_log collects every prompt received so tests can assert invocations.
    """
    call_log: list = []

    CONSOLIDATION_RESPONSE = json.dumps([
        {
            "memory_type": "human_feedback",
            "content": "Stub LLM: Config is the active public architecture for new helpers.",
            "scope": {
                "repo": "configly",
                "files": ["configly/parser.py"],
                "symbols": ["Config"],
                "session_validity": "all",
            },
            "provenance": {
                "trajectory_id": "traj-test-001",
                "event_id": "config-stale-merge-e1",
                "quote": "new public helpers should preserve Config helpers",
            },
            "confidence": 0.88,
            "risk_score": 0.10,
            "staleness_score": 0.0,
            "retrieval_tags": ["human-feedback", "config-architecture"],
        }
    ])

    CONTRADICTION_RESPONSE = json.dumps({
        "relation": "supersedes",
        "old_status": "superseded",
        "new_status": "active",
        "scope_delta": "narrowed from all helpers to composition APIs only",
        "rationale": "New memory explicitly supersedes the overbroad compatibility reading.",
    })

    REPLAY_RESPONSE = json.dumps({
        "bad_action": "implement merge_configs as shallow dict.update returning dict",
        "evidence": "Maintainer feedback: new composition APIs must return Config",
        "correct_alternative": "normalize dict inputs to Config and return Config",
        "future_retrieval_condition": "any task adding a public config composition helper",
        "scope": {
            "repo": "configly",
            "files": ["configly/parser.py"],
            "symbols": ["merge_configs"],
        },
    })

    def complete(prompt: str) -> str:
        call_log.append(prompt)
        if _CONSOLIDATION_TAG in prompt:
            return CONSOLIDATION_RESPONSE
        if _CONTRADICTION_TAG in prompt:
            return CONTRADICTION_RESPONSE
        if _REPLAY_TAG in prompt:
            return REPLAY_RESPONSE
        return "[]"

    return complete, call_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_episode(
    trajectory_id: str = "traj-test-001",
    task_id: str = "config-stale-merge-s1",
    repo_scope: str = "configly",
    files: list = None,
    outcome: str = "failure",
) -> dict:
    return {
        "trajectory_id": trajectory_id,
        "task_id": task_id,
        "repo_scope": repo_scope,
        "file_paths": files or ["configly/parser.py"],
        "prompt": "Add merge_configs helper.",
        "actions": ["implement merge_configs as dict.update(override)"],
        "observations": ["tests passed but reviewer flagged wrong return type"],
        "failure_observations": ["merge_configs returned dict instead of Config"],
        "outcome": outcome,
        "successful_recovery": None,
    }


def _make_memory(
    content: str,
    repo_scope: str = "configly",
    files: list = None,
    memory_type: MemoryType = MemoryType.HUMAN_FEEDBACK,
) -> MemoryItem:
    return new_memory(
        content=content,
        type=memory_type,
        write_reason="test fixture",
        provenance=Provenance(
            trajectory_ids=["traj-old"],
            file_paths=files or ["configly/parser.py"],
        ),
        repo_scope=repo_scope,
        file_scope=files or ["configly/parser.py"],
        confidence=0.7,
        utility_score=0.7,
        risk_score=0.1,
        retrieval_tags=["test"],
    )


# ---------------------------------------------------------------------------
# (a) LLMJudge callables are invoked — not the regex default
# ---------------------------------------------------------------------------

class TestLLMJudgeInvocation:
    """Assert that the injected judge is called, not the regex default."""

    def test_consolidation_judge_called_not_default_extractor(self) -> None:
        complete, call_log = _make_stub_complete()
        judge = LLMJudge(complete)

        store = MemoryStore()
        episode = _raw_episode()
        results = sleep_pipeline(
            [episode],
            store,
            extractor=judge.consolidation_judge,
            write=False,
        )

        consolidation_calls = [p for p in call_log if _CONSOLIDATION_TAG in p]
        assert len(consolidation_calls) >= 1, "consolidation_judge was never called"

        contents = [m.content for m in results]
        assert any("Stub LLM" in c for c in contents), (
            f"Expected stub LLM output in results; got: {contents}"
        )

    def test_contradiction_judge_called_not_default_judge(self) -> None:
        complete, call_log = _make_stub_complete()
        judge = LLMJudge(complete)

        store = MemoryStore()
        old_mem = _make_memory("New helpers may return plain dicts.")
        store.add(old_mem)

        candidate = _make_memory("New composition APIs must return Config.")
        repair = ContradictionRepair(judge.contradiction_judge)
        results = repair.apply([candidate], store)

        contradiction_calls = [p for p in call_log if _CONTRADICTION_TAG in p]
        assert len(contradiction_calls) >= 1, "contradiction_judge was never called"

        # stub returns relation=supersedes → old memory should be superseded
        assert old_mem.status == MemoryStatus.SUPERSEDED, (
            f"Expected old memory to be superseded; status={old_mem.status}"
        )

    def test_replay_judge_called_not_default_judge(self) -> None:
        complete, call_log = _make_stub_complete()
        judge = LLMJudge(complete)

        store = MemoryStore()
        episode = _raw_episode(outcome="failure")
        replay = CounterfactualReplay(judge.replay_judge)
        results = replay.run([episode], store)

        replay_calls = [p for p in call_log if _REPLAY_TAG in p]
        assert len(replay_calls) >= 1, "replay_judge was never called"

        assert len(results) >= 1, "Expected at least one replay memory"
        contents = " ".join(m.content for m in results)
        assert "dict.update" in contents or "merge_configs" in contents or "Config" in contents, (
            f"Expected stub replay content; got: {contents}"
        )

    def test_dreamforge_policy_uses_llm_judge_when_provided(self) -> None:
        complete, call_log = _make_stub_complete()
        judge = LLMJudge(complete)

        policy = DreamForgePolicy(
            name="DF",
            label="DreamForge",
            description="Full DF pipeline",
            llm_judge=judge,
            maintenance_scope="local",
        )
        episode = _raw_episode(outcome="failure")
        policy.write(episode)

        consolidation_calls = [p for p in call_log if _CONSOLIDATION_TAG in p]
        assert len(consolidation_calls) >= 1, (
            "DreamForgePolicy.write did not invoke consolidation_judge"
        )


# ---------------------------------------------------------------------------
# LLMJudge contradiction parser robustness
# ---------------------------------------------------------------------------

class TestLLMJudgeRobustness:
    """Regression coverage for malformed contradiction-judge metadata."""

    def test_contradiction_judge_accepts_rationation_key(self) -> None:
        response = json.dumps({
            "relation": "supersedes",
            "old_status": "superseded",
            "new_status": "active",
            "scope_delta": "newer evidence replaces stale fact",
            "rationation": "Misspelled rationale from the live model.",
        })
        judge = LLMJudge(complete=lambda prompt: response)

        decision = judge.contradiction_judge(
            _make_memory("This repo uses Jest.", memory_type=MemoryType.SEMANTIC_PROJECT),
            _make_memory("This repo uses Vitest.", memory_type=MemoryType.SEMANTIC_PROJECT),
        )

        assert decision["contradicts"] is True
        assert decision["newer_supersedes"] is True
        assert decision["rationale"] == "Misspelled rationale from the live model."
        assert decision["reason"] == "Misspelled rationale from the live model."

    def test_contradiction_judge_allows_missing_rationale(self) -> None:
        response = json.dumps({
            "relation": "no_conflict",
            "old_status": "active",
            "new_status": "active",
            "scope_delta": "",
        })
        judge = LLMJudge(complete=lambda prompt: response)

        decision = judge.contradiction_judge(
            _make_memory("Config helpers return Config."),
            _make_memory("Todo commands print STATS."),
        )

        assert decision["contradicts"] is False
        assert decision["old_status"] == "active"
        assert decision["new_status"] == "active"
        assert decision["rationale"] == ""
        assert decision["reason"] == ""

    def test_sleep_pipeline_falls_back_for_one_malformed_repair_call(self) -> None:
        store = MemoryStore()
        old = store.add(new_memory(
            "This repo uses Jest.",
            MemoryType.SEMANTIC_PROJECT,
            write_reason="old package.json observation",
            provenance=Provenance(trajectory_ids=["traj-old"], file_paths=["package.json"]),
            repo_scope="repo-a",
            file_scope=["package.json"],
            confidence=0.6,
            utility_score=0.6,
            retrieval_tags=["testing"],
        ))
        candidate = new_memory(
            "This repo uses Vitest.",
            MemoryType.SEMANTIC_PROJECT,
            write_reason="new package.json observation",
            provenance=Provenance(trajectory_ids=["traj-new"], file_paths=["package.json"]),
            repo_scope="repo-a",
            file_scope=["package.json"],
            confidence=0.9,
            utility_score=0.7,
            retrieval_tags=["testing"],
        )
        other_write = new_memory(
            "Keep package scripts documented after test-runner changes.",
            MemoryType.PROCEDURAL,
            write_reason="new workflow observation",
            provenance=Provenance(trajectory_ids=["traj-new"], file_paths=["package.json"]),
            repo_scope="repo-a",
            file_scope=["package.json"],
            confidence=0.8,
            utility_score=0.7,
            retrieval_tags=["workflow"],
        )

        def extractor(payload):
            assert payload["raw_episodes"][0]["trajectory_id"] == "traj-new"
            return [candidate, other_write]

        calls = []
        malformed_returned = False

        def complete(prompt: str) -> str:
            nonlocal malformed_returned
            calls.append(prompt)
            if (
                not malformed_returned
                and "This repo uses Vitest." in prompt
                and "This repo uses Jest." in prompt
            ):
                malformed_returned = True
                return "not json at all"
            return json.dumps({
                "relation": "no_conflict",
                "old_status": "active",
                "new_status": "active",
                "scope_delta": "",
                "rationale": "Subsequent pair is clean.",
            })

        judge = LLMJudge(complete=complete)
        raw_episode = {
            "trajectory_id": "traj-new",
            "task_id": "task-new",
            "repo_scope": "repo-a",
            "file_paths": ["package.json"],
            "observations": ["package.json now shows test runner is vitest"],
            "outcome": "success",
        }

        written = sleep_pipeline(
            [raw_episode],
            store,
            extractor=extractor,
            repair_judge=judge.contradiction_judge,
            write=True,
        )

        written_contents = [memory.content for memory in written]
        stored_contents = [memory.content for memory in store.get_all(include_inactive=True)]
        assert old.status == MemoryStatus.SUPERSEDED
        assert candidate.id in old.superseded_by
        assert any("fallback after repair judge error" in content for content in written_contents)
        assert any("Keep package scripts documented" in content for content in stored_contents)


# ---------------------------------------------------------------------------
# (b) maintenance_scope='local' filters out-of-scope memories
# ---------------------------------------------------------------------------

class TestMaintenanceScopeLocal:
    """Assert that local scope filtering works correctly in repair."""

    def _make_store_with_two_memories(self):
        store = MemoryStore()
        in_scope = store.add(_make_memory(
            "Config new helpers must return Config objects.",
            repo_scope="configly",
            files=["configly/parser.py"],
        ))
        out_of_scope = store.add(_make_memory(
            "Config new helpers must return dict objects.",
            repo_scope="configly",
            files=["configly/csv_parser.py"],  # different file — out of scope
        ))
        return store, in_scope, out_of_scope

    def test_local_scope_excludes_out_of_scope_existing_memory(self) -> None:
        complete, call_log = _make_stub_complete()
        judge = LLMJudge(complete)

        store, in_scope_mem, out_of_scope_mem = self._make_store_with_two_memories()

        candidate = _make_memory(
            "New composition APIs must normalize inputs and return Config.",
            repo_scope="configly",
            files=["configly/parser.py"],
        )
        episode_scope = {
            "repo": "configly",
            "files": ["configly/parser.py"],
            "symbols": ["Config", "merge_configs"],
        }
        repair = ContradictionRepair(judge.contradiction_judge)
        repair.apply(
            [candidate],
            store,
            episode_scope=episode_scope,
            maintenance_scope="local",
        )

        # Only the in-scope memory (parser.py) should have been considered.
        # The stub always returns supersedes, so in_scope_mem should be superseded.
        assert in_scope_mem.status == MemoryStatus.SUPERSEDED, (
            "In-scope memory was not superseded by local repair"
        )
        # out_of_scope_mem should NOT have been touched
        assert out_of_scope_mem.status == MemoryStatus.ACTIVE, (
            "Out-of-scope memory was incorrectly modified by local repair"
        )

    def test_global_scope_considers_all_existing_memories(self) -> None:
        complete, call_log = _make_stub_complete()
        judge = LLMJudge(complete)

        store, in_scope_mem, out_of_scope_mem = self._make_store_with_two_memories()

        candidate = _make_memory(
            "New composition APIs must normalize inputs and return Config.",
            repo_scope="configly",
            files=["configly/parser.py"],
        )
        episode_scope = {
            "repo": "configly",
            "files": ["configly/parser.py"],
            "symbols": [],
        }
        repair = ContradictionRepair(judge.contradiction_judge)
        repair.apply(
            [candidate],
            store,
            episode_scope=episode_scope,
            maintenance_scope="global",
        )

        # In global mode, BOTH memories should be considered (and superseded by stub)
        assert in_scope_mem.status == MemoryStatus.SUPERSEDED
        assert out_of_scope_mem.status == MemoryStatus.SUPERSEDED

    def test_scope_helper_correctly_classifies_memories(self) -> None:
        episode_scope = {
            "repo": "configly",
            "files": ["configly/parser.py"],
            "symbols": ["Config"],
        }
        in_scope = _make_memory("x", repo_scope="configly", files=["configly/parser.py"])
        out_of_scope = _make_memory("x", repo_scope="configly", files=["configly/csv_parser.py"])
        no_scope = new_memory(
            "no scope",
            type=MemoryType.PROCEDURAL,
            write_reason="test",
            provenance=Provenance(trajectory_ids=["t"]),
        )

        assert _memory_in_episode_scope(in_scope, episode_scope), "in-scope memory should match"
        assert not _memory_in_episode_scope(out_of_scope, episode_scope), (
            "out-of-scope memory should not match"
        )
        assert _memory_in_episode_scope(no_scope, episode_scope), (
            "unscoped memory should be included conservatively"
        )

    def test_dreamforge_policy_local_scope_does_not_touch_out_of_scope_memory(self) -> None:
        complete, _ = _make_stub_complete()
        judge = LLMJudge(complete)

        policy = DreamForgePolicy(
            name="DF",
            label="DreamForge",
            description="Full DF pipeline",
            llm_judge=judge,
            maintenance_scope="local",
        )
        # Pre-seed store with an out-of-scope memory
        out_of_scope_mem = policy.store.add(_make_memory(
            "Config new helpers should return plain dict.",
            repo_scope="configly",
            files=["configly/csv_parser.py"],  # different file
        ))

        # Write episode scoped to parser.py only
        episode = _raw_episode(
            files=["configly/parser.py"],
            outcome="failure",
        )
        policy.write(episode)

        # out_of_scope_mem should still be ACTIVE — local scope did not touch it
        assert out_of_scope_mem.status == MemoryStatus.ACTIVE, (
            "Local-scope maintenance incorrectly modified an out-of-scope memory"
        )


# ---------------------------------------------------------------------------
# (c) Deterministic fallback works with no judge injected
# ---------------------------------------------------------------------------

class TestDeterministicFallback:
    """Assert the existing regex/template path still works when no judge is given."""

    def test_default_consolidation_extractor_produces_memories(self) -> None:
        store = MemoryStore()
        episode = {
            "trajectory_id": "traj-fallback",
            "task_id": "fallback-task",
            "repo_scope": "myrepo",
            "file_paths": ["myrepo/main.py"],
            "prompt": "Add a feature.",
            "actions": ["python3 -m pytest"],
            "observations": ["All tests passed."],
            "outcome": "success",
        }
        results = sleep_pipeline([episode], store, write=False)
        assert len(results) >= 1, "Default extractor produced no memories"
        types = {m.type for m in results}
        assert MemoryType.EPISODIC in types, "Expected at least an EPISODIC memory from fallback"

    def test_default_contradiction_judge_produces_no_false_positives(self) -> None:
        store = MemoryStore()
        mem_a = _make_memory("The project uses pytest.")
        store.add(mem_a)
        candidate = _make_memory("The project uses pytest for testing.")
        repair = ContradictionRepair()  # no judge — uses default_judge
        results = repair.apply([candidate], store)
        # Default regex: no clear conflict between these two
        assert all(
            m.type != MemoryType.CONTRADICTION for m in results
        ) or len(results) == 1, (
            "Default judge produced a false-positive contradiction"
        )

    def test_default_replay_judge_produces_memories_for_failed_episode(self) -> None:
        store = MemoryStore()
        episode = {
            "trajectory_id": "traj-fallback-replay",
            "task_id": "fallback-replay-task",
            "repo_scope": "myrepo",
            "file_paths": ["myrepo/main.py"],
            "failure_observations": ["AssertionError: expected Config got dict"],
            "outcome": "failure",
        }
        replay = CounterfactualReplay()  # no judge — uses default_judge
        results = replay.run([episode], store)
        assert len(results) >= 1, "Default replay judge produced no memories for failed episode"

    def test_dreamforge_policy_without_judge_runs_full_pipeline(self) -> None:
        policy = DreamForgePolicy(
            name="DF",
            label="DreamForge",
            description="Full DF pipeline (no judge)",
        )
        episode = _raw_episode(outcome="failure")
        policy.write(episode)
        # Should produce at least one memory from the default pipeline
        assert policy.last_write_count >= 1, (
            "DreamForgePolicy without judge wrote zero memories"
        )
        items = policy.memory_items()
        assert len(items) >= 1

    def test_fallback_and_llm_judge_produce_same_schema_structure(self) -> None:
        """Both paths should produce valid MemoryItem objects with provenance."""
        # Fallback path
        store_fb = MemoryStore()
        episode = _raw_episode(outcome="failure")
        fallback_results = sleep_pipeline([episode], store_fb, write=False)

        # LLM judge path
        complete, _ = _make_stub_complete()
        judge = LLMJudge(complete)
        store_llm = MemoryStore()
        llm_results = sleep_pipeline(
            [episode],
            store_llm,
            extractor=judge.consolidation_judge,
            write=False,
        )

        for mem in fallback_results + llm_results:
            assert isinstance(mem, MemoryItem)
            assert mem.content.strip()
            assert mem.write_reason.strip()
            assert isinstance(mem.provenance, Provenance)
            assert 0.0 <= mem.confidence <= 1.0
            assert 0.0 <= mem.risk_score <= 1.0
