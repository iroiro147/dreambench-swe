"""Sleep-time consolidation for DreamForge.

All model-like behavior is behind an injectable callable. The default extractor
is deterministic, rule-based, and uses only raw episode fields so tests run
offline with no API keys.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Union

from dream_memory.memory_store import MemoryStore, memory_from_dict
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType, Provenance, new_memory


Extractor = Callable[[Dict[str, Any]], Sequence[Union[MemoryItem, Dict[str, Any]]]]
_DEFAULT_REPLAY_JUDGE = object()
_DEFAULT_REPAIR_JUDGE = object()


def _as_dict(episode: Any) -> Dict[str, Any]:
    if isinstance(episode, dict):
        return dict(episode)
    if hasattr(episode, "raw_episode_view"):
        return dict(episode.raw_episode_view())
    if hasattr(episode, "to_dict"):
        return dict(episode.to_dict())
    raise TypeError(f"unsupported raw episode type: {type(episode)!r}")


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _trim(text: str, limit: int = 220) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _episode_text(episode: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("prompt", "initial_prompt", "task_prompt", "outcome", "successful_recovery"):
        if episode.get(key):
            parts.append(str(episode[key]))
    for key in ("actions", "observations", "failure_observations", "known_constraints"):
        parts.extend(_as_list(episode.get(key)))
    return "\n".join(parts)


def _provenance_for(episode: Dict[str, Any]) -> Provenance:
    return Provenance(
        trajectory_ids=_as_list(episode.get("trajectory_id")),
        task_ids=_as_list(episode.get("task_id")),
        repo_commits=_as_list(episode.get("repo_commit")),
        file_paths=_as_list(episode.get("file_paths") or episode.get("files")),
        command_outputs=[
            _trim(item, 180)
            for item in _as_list(episode.get("command_outputs") or episode.get("failure_observations") or episode.get("observations"))
            if item
        ][:5],
        human_feedback_ids=_as_list(episode.get("human_feedback_id") or episode.get("human_feedback_ids")),
    )


def _episode_sequence_scope(episode: Dict[str, Any]) -> Optional[str]:
    for key in ("repo_scope", "sequence_id"):
        value = episode.get(key)
        if value:
            return str(value)
    task_id = episode.get("task_id")
    if task_id:
        return re.sub(r"-s\d+$", "", str(task_id))
    return None


def _base_kwargs(episode: Dict[str, Any], tags: Sequence[str]) -> Dict[str, Any]:
    task_id = episode.get("task_id")
    return {
        "repo_scope": _episode_sequence_scope(episode),
        "file_scope": _as_list(episode.get("file_paths") or episode.get("files")),
        "task_scope": _as_list(task_id),
        "retrieval_tags": list(tags),
    }


def default_extractor(payload: Dict[str, Any]) -> List[MemoryItem]:
    """Deterministically convert raw episodes into grounded typed memories."""
    memories: List[MemoryItem] = []
    for episode in payload.get("raw_episodes", []):
        episode = _as_dict(episode)
        trajectory_id = episode.get("trajectory_id", "unknown")
        task_id = episode.get("task_id", "unknown")
        text = _episode_text(episode)
        lowered = text.lower()
        provenance = _provenance_for(episode)
        base = _base_kwargs(episode, ["consolidation", str(task_id)])

        if not provenance.is_grounded():
            memories.append(new_memory(
                content=f"Episode {trajectory_id} could not be grounded to raw evidence; review before use.",
                type=MemoryType.DREAM_ARTIFACT,
                write_reason="consolidation found an episode without usable provenance",
                provenance=provenance,
                status=MemoryStatus.REQUIRES_REVIEW,
                confidence=0.2,
                utility_score=0.2,
                risk_score=0.5,
                retrieval_tags=["consolidation", "ungrounded"],
            ))
            continue

        memories.append(new_memory(
            content=f"Raw episode pointer for task {task_id} in trajectory {trajectory_id}. Outcome: {episode.get('outcome', episode.get('terminal_outcome', 'unknown'))}.",
            type=MemoryType.EPISODIC,
            write_reason="preserve raw trajectory pointer for audit and episodic-only ablation",
            provenance=provenance,
            confidence=1.0,
            utility_score=0.35,
            risk_score=0.0,
            retrieval_tags=list(base["retrieval_tags"]) + ["episodic"],
            repo_scope=base["repo_scope"],
            file_scope=base["file_scope"],
            task_scope=base["task_scope"],
        ))

        if _looks_like_failure(episode, lowered):
            evidence = _first_evidence(episode, lowered)
            recovery = episode.get("successful_recovery")
            content = f"Failure in trajectory {trajectory_id}: observed evidence `{_trim(evidence)}`."
            if recovery:
                content += f" Recovery evidence: `{_trim(str(recovery))}`."
            else:
                content += " Cause is uncertain because no successful recovery is recorded."
            memories.append(new_memory(
                content=content,
                type=MemoryType.FAILURE,
                write_reason="failure evidence appeared in the raw episode",
                provenance=provenance,
                status=MemoryStatus.ACTIVE if recovery else MemoryStatus.REQUIRES_REVIEW,
                confidence=0.75 if recovery else 0.45,
                utility_score=0.8,
                risk_score=0.25 if recovery else 0.45,
                retrieval_tags=list(base["retrieval_tags"]) + ["failure", "causal-uncertain" if not recovery else "recovery-grounded"],
                repo_scope=base["repo_scope"],
                file_scope=base["file_scope"],
                task_scope=base["task_scope"],
            ))

        preference = _extract_human_feedback(episode, lowered)
        if preference:
            memories.append(new_memory(
                content=f"Human feedback from trajectory {trajectory_id}: {_trim(preference)}",
                type=MemoryType.HUMAN_FEEDBACK,
                write_reason="explicit user or reviewer preference appeared in the episode",
                provenance=provenance,
                confidence=0.8,
                utility_score=0.75,
                risk_score=0.35 if not base["file_scope"] else 0.15,
                retrieval_tags=list(base["retrieval_tags"]) + ["human-feedback"],
                repo_scope=base["repo_scope"],
                file_scope=base["file_scope"],
                task_scope=base["task_scope"],
            ))

        procedure = _extract_procedure(episode)
        if procedure:
            memories.append(new_memory(
                content=f"Procedural lesson from trajectory {trajectory_id}: {_trim(procedure)}",
                type=MemoryType.PROCEDURAL,
                write_reason="repeatable command or workflow succeeded in the raw episode",
                provenance=provenance,
                confidence=0.65,
                utility_score=0.7,
                risk_score=0.2,
                retrieval_tags=list(base["retrieval_tags"]) + ["procedural"],
                repo_scope=base["repo_scope"],
                file_scope=base["file_scope"],
                task_scope=base["task_scope"],
            ))

        project_fact = _extract_project_fact(episode, lowered)
        if project_fact:
            memories.append(new_memory(
                content=f"Project fact from trajectory {trajectory_id}: {_trim(project_fact)}",
                type=MemoryType.SEMANTIC_PROJECT,
                write_reason="repo fact was directly observed in raw trajectory evidence",
                provenance=provenance,
                confidence=0.7,
                utility_score=0.65,
                risk_score=0.2 if base["file_scope"] else 0.35,
                retrieval_tags=list(base["retrieval_tags"]) + ["semantic-project"],
                repo_scope=base["repo_scope"],
                file_scope=base["file_scope"],
                task_scope=base["task_scope"],
            ))

    return memories


class Consolidator:
    def __init__(self, extractor: Optional[Extractor] = None) -> None:
        self.extractor = extractor or default_extractor

    def extract(
        self,
        raw_episodes: Iterable[Any],
        *,
        existing_memories: Optional[Iterable[MemoryItem]] = None,
        retrieval_context: Optional[str] = None,
        repo_scope: Optional[str] = None,
    ) -> List[MemoryItem]:
        payload = {
            "raw_episodes": [_as_dict(episode) for episode in raw_episodes],
            "existing_memories": [
                memory.to_dict() if isinstance(memory, MemoryItem) else dict(memory)
                for memory in (existing_memories or [])
            ],
            "retrieval_context": retrieval_context,
            "repo_scope": repo_scope,
        }
        return [_coerce_memory(item) for item in self.extractor(payload)]


def extract(
    raw_episodes: Iterable[Any],
    *,
    extractor: Optional[Extractor] = None,
    existing_memories: Optional[Iterable[MemoryItem]] = None,
    retrieval_context: Optional[str] = None,
    repo_scope: Optional[str] = None,
) -> List[MemoryItem]:
    return Consolidator(extractor).extract(
        raw_episodes,
        existing_memories=existing_memories,
        retrieval_context=retrieval_context,
        repo_scope=repo_scope,
    )


def sleep_pipeline(
    raw_episodes: Iterable[Any],
    memory_store: MemoryStore,
    *,
    extractor: Optional[Extractor] = None,
    replay_judge: Any = _DEFAULT_REPLAY_JUDGE,
    repair_judge: Any = _DEFAULT_REPAIR_JUDGE,
    write: bool = True,
    episode_scope: Optional[Dict[str, Any]] = None,
    maintenance_scope: str = "local",
    enable_raw_evidence: bool = False,
) -> List[MemoryItem]:
    """Run the documented sleep pipeline and optionally write derived memories.

    Args:
        episode_scope: repo/files/symbols dict describing the current episode's
            changed scope.  Derived automatically from raw_episodes when absent.
        maintenance_scope: 'local' (default) restricts repair/replay to
            memories that intersect episode_scope; 'global' operates on the
            whole store (expensive, O(n^2) for repair).
    """
    episodes = [_as_dict(episode) for episode in raw_episodes]

    # derive episode scope from episodes if not provided
    if episode_scope is None:
        episode_scope = _derive_episode_scope(episodes)

    candidates = extract(
        episodes,
        extractor=extractor,
        existing_memories=memory_store.get_all(include_inactive=True),
    )
    if enable_raw_evidence:
        candidates.extend(_raw_evidence_memories(episodes))
    candidates.sort(key=lambda memory: (memory.type.value, memory.content[:50]))

    from dream_memory import counterfactual_replay, contradiction_repair

    if replay_judge is None:
        replay_outputs: List[MemoryItem] = []
    else:
        replay_outputs = counterfactual_replay.run(
            episodes,
            memory_store,
            judge=None if replay_judge is _DEFAULT_REPLAY_JUDGE else replay_judge,
            episode_scope=episode_scope,
            maintenance_scope=maintenance_scope,
        )
    proposed = candidates + replay_outputs
    if repair_judge is None:
        repaired = proposed
    else:
        repair_judge_for_pipeline = (
            _repair_judge_with_deterministic_fallback(
                repair_judge,
                contradiction_repair.default_judge,
            )
            if repair_judge is not _DEFAULT_REPAIR_JUDGE
            else None
        )
        repaired = contradiction_repair.apply(
            proposed,
            memory_store,
            judge=repair_judge_for_pipeline,
            episode_scope=episode_scope,
            maintenance_scope=maintenance_scope,
        )
    suppressed = stale_suppression(repaired)
    validated = provenance_gate(suppressed)

    if write:
        for memory in validated:
            memory_store.add(memory, allow_existing=True)
    return validated


def _raw_evidence_memories(episodes: Iterable[Dict[str, Any]]) -> List[MemoryItem]:
    memories: List[MemoryItem] = []
    for ep in episodes:
        event = dict(ep.get("injected_memory_event") or {})
        verbatim = str(
            event.get("content")
            or ep.get("human_feedback")
            or _first_evidence(ep, _episode_text(ep).lower())
            or ""
        ).strip()
        if not verbatim:
            continue
        sequence_scope = _episode_sequence_scope(ep)
        memories.append(new_memory(
            content=(
                f"Raw evidence for task {ep.get('task_id', 'unknown')} "
                f"(outcome {ep.get('outcome', ep.get('terminal_outcome', 'unknown'))}): "
                f"{_trim(verbatim, 600)}"
            ),
            type=MemoryType.EPISODIC,
            write_reason="pipeline-level raw-evidence retention (raw episodes are first-class evidence)",
            provenance=_provenance_for(ep),
            confidence=1.0,
            utility_score=0.6,
            risk_score=0.1,
            retrieval_tags=[
                "episodic",
                "raw-evidence",
                "consolidation",
                *([sequence_scope] if sequence_scope else []),
            ],
            repo_scope=sequence_scope,
            file_scope=_as_list(ep.get("file_paths") or ep.get("files")),
            task_scope=_as_list(ep.get("task_id")),
        ))
    return memories


def _repair_judge_with_deterministic_fallback(
    repair_judge: Callable[[MemoryItem, MemoryItem], Dict[str, Any]],
    fallback_judge: Callable[[MemoryItem, MemoryItem], Dict[str, Any]],
) -> Callable[[MemoryItem, MemoryItem], Dict[str, Any]]:
    def judge(existing: MemoryItem, candidate: MemoryItem) -> Dict[str, Any]:
        try:
            decision = repair_judge(existing, candidate)
            if not isinstance(decision, dict):
                raise ValueError(
                    f"repair judge returned {type(decision).__name__}, expected dict"
                )
            return decision
        except Exception as exc:
            fallback = dict(fallback_judge(existing, candidate))
            reason = str(fallback.get("reason") or "deterministic fallback")
            fallback["reason"] = (
                f"{reason} (fallback after repair judge error: {type(exc).__name__})"
            )
            fallback["judge_error"] = f"{type(exc).__name__}: {_trim(str(exc), 180)}"
            return fallback

    return judge


def _derive_episode_scope(episodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive the aggregate changed scope from a list of raw episodes."""
    repos: List[str] = []
    files: List[str] = []
    symbols: List[str] = []
    for ep in episodes:
        repo_scope = _episode_sequence_scope(ep)
        if repo_scope:
            repos.append(repo_scope)
        for f in _as_list(ep.get("file_paths") or ep.get("files")):
            if f and f not in files:
                files.append(f)
        for s in _as_list(ep.get("symbols") or ep.get("changed_symbols")):
            if s and s not in symbols:
                symbols.append(s)
    return {
        "repo": repos[0] if repos else None,
        "files": files,
        "symbols": symbols,
    }


def stale_suppression(memories: Iterable[MemoryItem], *, stale_threshold: float = 0.95) -> List[MemoryItem]:
    out: List[MemoryItem] = []
    for memory in memories:
        if memory.staleness_score >= stale_threshold and memory.status == MemoryStatus.ACTIVE:
            memory.mark_stale(memory.staleness_score)
        if memory.risk_score >= 0.9 and memory.status == MemoryStatus.ACTIVE:
            memory.status = MemoryStatus.REQUIRES_REVIEW
        out.append(memory)
    return out


def provenance_gate(memories: Iterable[MemoryItem]) -> List[MemoryItem]:
    out: List[MemoryItem] = []
    for memory in memories:
        if not memory.provenance.is_grounded():
            memory.status = MemoryStatus.REQUIRES_REVIEW
            memory.confidence = min(memory.confidence, 0.3)
            memory.risk_score = max(memory.risk_score, 0.5)
            if "ungrounded" not in memory.retrieval_tags:
                memory.retrieval_tags.append("ungrounded")
        out.append(memory)
    return out


def _coerce_memory(item: Union[MemoryItem, Dict[str, Any]]) -> MemoryItem:
    return item if isinstance(item, MemoryItem) else memory_from_dict(item)


def _looks_like_failure(episode: Dict[str, Any], lowered: str) -> bool:
    outcome = str(episode.get("outcome") or episode.get("terminal_outcome") or "").lower()
    if outcome in {"failure", "failed", "partial", "unknown"}:
        return outcome != "success"
    return any(word in lowered for word in ("error", "failed", "failure", "traceback", "exception", "timed out"))


def _first_evidence(episode: Dict[str, Any], lowered: str) -> str:
    for item in _as_list(episode.get("failure_observations") or episode.get("observations") or episode.get("actions")):
        if any(word in item.lower() for word in ("error", "failed", "failure", "traceback", "exception", "timed out")):
            return item
    return lowered.splitlines()[0] if lowered else "no detailed failure observation"


def _extract_human_feedback(episode: Dict[str, Any], lowered: str) -> Optional[str]:
    for key in ("human_feedback", "reviewer_comment", "user_feedback"):
        if episode.get(key):
            return str(episode[key])
    for line in lowered.splitlines():
        if any(marker in line for marker in ("reviewer:", "user asked", "user said", "prefer ", "please ", "must ")):
            return line
    return None


def _extract_procedure(episode: Dict[str, Any]) -> Optional[str]:
    outcome = str(episode.get("outcome") or episode.get("terminal_outcome") or "").lower()
    if outcome not in {"success", "passed"}:
        return None
    for action in _as_list(episode.get("actions")):
        stripped = action.strip()
        if re.search(r"\b(python3?|pytest|npm|make|cargo|go test|ruff|mypy)\b", stripped):
            return f"`{stripped}` was part of a successful verification path."
    return None


def _extract_project_fact(episode: Dict[str, Any], lowered: str) -> Optional[str]:
    patterns = [
        r"\b(?:uses|use|using|framework is|test runner is)\s+([a-z0-9_.-]+)",
        r"\b(?:lives in|located in)\s+([a-z0-9_./-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(0)
    if episode.get("repo_fact"):
        return str(episode["repo_fact"])
    return None


__all__ = [
    "Consolidator",
    "default_extractor",
    "extract",
    "sleep_pipeline",
    "stale_suppression",
    "provenance_gate",
]
