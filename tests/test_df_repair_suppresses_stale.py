"""Regression for DF local repair scope.

The DreamForgePolicy.write post-write override is intentionally not used here:
the bug happens inside sleep_pipeline before that override can run.  The test
uses a DreamForgePolicy instance for the real injected judge callables, then
runs the sleep pipeline directly to assert memory creation already uses the
sequence namespace required by local contradiction repair.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from benchmarks.baselines import DreamForgePolicy  # noqa: E402
from dream_memory.consolidation import sleep_pipeline  # noqa: E402
from dream_memory.llm_judge import (  # noqa: E402
    LLMJudge,
    _CONSOLIDATION_TAG,
    _CONTRADICTION_TAG,
    _REPLAY_TAG,
)
from dream_memory.schemas import MemoryStatus  # noqa: E402


SEQ_ID = "repair-scope-seq"
FILE_SCOPE = ["configly/flags.py"]
SYMBOL_SCOPE = ["merge_flags"]


class ScopeMismatchStub:
    """LLM stub that returns the real repo name in scope.repo."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if _CONSOLIDATION_TAG in prompt:
            episode = _prompt_json(prompt, "RAW EPISODE:")
            content = (
                "merge_flags may return a plain dict because Config is dict-compatible."
                if str(episode.get("task_id", "")).endswith("-s1")
                else "merge_flags must normalize inputs and return Config."
            )
            return json.dumps([
                {
                    "memory_type": "semantic_project",
                    "content": content,
                    "scope": {
                        "repo": "configly",
                        "files": FILE_SCOPE,
                        "symbols": SYMBOL_SCOPE,
                        "session_validity": "future",
                    },
                    "provenance": {
                        "trajectory_id": episode.get("trajectory_id"),
                        "event_id": f"{episode.get('task_id')}-event",
                        "quote": content,
                    },
                    "confidence": 0.94,
                    "risk_score": 0.05,
                    "staleness_score": 0.0,
                    "retrieval_tags": ["consolidation", "merge_flags", SEQ_ID],
                }
            ])
        if _CONTRADICTION_TAG in prompt:
            return json.dumps({
                "relation": "supersedes",
                "old_status": "superseded",
                "new_status": "active",
                "scope_delta": "S2 replaces the earlier return-type contract.",
                "rationale": "The newer Config return contract supersedes the plain dict contract.",
            })
        if _REPLAY_TAG in prompt:
            return "{}"
        return "[]"


def _prompt_json(prompt: str, marker: str) -> Dict[str, Any]:
    tail = prompt.split(marker, 1)[1].strip()
    value, _ = json.JSONDecoder().raw_decode(tail)
    return dict(value)


def _episode(session_index: int, prompt: str) -> Dict[str, Any]:
    return {
        "trajectory_id": f"traj-{SEQ_ID}-s{session_index}",
        "task_id": f"{SEQ_ID}-s{session_index}",
        "sequence_id": SEQ_ID,
        "repo_scope": SEQ_ID,
        "file_paths": FILE_SCOPE,
        "symbols": SYMBOL_SCOPE,
        "prompt": prompt,
        "observations": [prompt],
        "outcome": "success",
    }


def _run_df_sleep(policy: DreamForgePolicy, episode: Dict[str, Any]) -> None:
    sleep_pipeline(
        [episode],
        policy.store,
        extractor=policy.extractor,
        replay_judge=policy.replay_judge,
        repair_judge=policy.repair_judge,
        write=True,
        maintenance_scope=policy.maintenance_scope,
    )


def test_df_repair_suppresses_stale_old_fact_before_s3_retrieval() -> None:
    stub = ScopeMismatchStub()
    policy = DreamForgePolicy(
        name="DFtest",
        label="DreamForge",
        description="repair scope regression",
        llm_judge=LLMJudge(stub),
        maintenance_scope="local",
    )

    _run_df_sleep(
        policy,
        _episode(1, "S1 establishes that merge_flags may return a plain dict."),
    )
    _run_df_sleep(
        policy,
        _episode(2, "S2 supersedes that contract: merge_flags must return Config."),
    )

    old = next(
        item for item in policy.memory_items()
        if "plain dict" in item.content
    )
    new = next(
        item for item in policy.memory_items()
        if "must normalize inputs and return Config" in item.content
    )

    assert old.repo_scope == SEQ_ID
    assert new.repo_scope == SEQ_ID
    assert old.status != MemoryStatus.ACTIVE
    assert old.status in {MemoryStatus.STALE, MemoryStatus.SUPERSEDED}
    assert new.status == MemoryStatus.ACTIVE
    assert new.id in old.superseded_by
    assert old.id in new.supersedes
    assert any(_CONTRADICTION_TAG in call for call in stub.calls)

    context = policy.read({
        "id": f"{SEQ_ID}-s3",
        "sequence_id": SEQ_ID,
        "prompt": "Implement merge_flags with the Config return contract.",
        "files": FILE_SCOPE,
    })
    context_ids = {item["id"] for item in context}
    decisions = {
        decision["memory_id"]: decision
        for decision in policy.last_retrieval_decisions
    }

    assert new.id in context_ids
    assert old.id not in context_ids
    assert decisions[old.id]["admitted"] is False
    assert decisions[old.id]["reason"] in {"status_stale", "status_superseded", "active_superseder"}
    assert decisions[new.id]["admitted"] is True
