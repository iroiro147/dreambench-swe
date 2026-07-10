"""Grounded counterfactual replay for DreamForge trajectories.

LOCAL SCOPE (ANM Finding 5): by default (maintenance_scope='local') replay only
considers episodes whose scope intersects episode_scope.  Pass
maintenance_scope='global' to run replay against all episodes.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Union

from dream_memory.memory_store import MemoryStore, memory_from_dict
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType, Provenance, new_memory


Judge = Callable[[Dict[str, Any]], Sequence[Union[MemoryItem, Dict[str, Any]]]]


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


class CounterfactualReplay:
    def __init__(self, judge: Optional[Judge] = None) -> None:
        self.judge = judge or default_judge

    def run(
        self,
        raw_episodes: Iterable[Any],
        memory_store: Optional[MemoryStore] = None,
        *,
        episode_scope: Optional[Dict[str, Any]] = None,
        maintenance_scope: str = "local",
    ) -> List[MemoryItem]:
        outputs: List[MemoryItem] = []
        existing = [
            memory.to_dict()
            for memory in (memory_store.get_all(include_inactive=True) if memory_store else [])
        ]
        for episode in raw_episodes:
            episode_data = _as_dict(episode)
            if not _selected_for_replay(episode_data):
                continue
            if (
                maintenance_scope == "local"
                and episode_scope is not None
                and not _episode_in_scope(episode_data, episode_scope)
            ):
                continue
            payload = {
                "episode": episode_data,
                "existing_memory_items": existing,
            }
            outputs.extend(_coerce_memory(item) for item in self.judge(payload))
        return outputs


def run(
    raw_episodes: Iterable[Any],
    memory_store: Optional[MemoryStore] = None,
    *,
    judge: Optional[Judge] = None,
    episode_scope: Optional[Dict[str, Any]] = None,
    maintenance_scope: str = "local",
) -> List[MemoryItem]:
    return CounterfactualReplay(judge).run(
        raw_episodes,
        memory_store,
        episode_scope=episode_scope,
        maintenance_scope=maintenance_scope,
    )


def default_judge(payload: Dict[str, Any]) -> List[MemoryItem]:
    """Rule-based replay using only the supplied trajectory evidence."""
    episode = dict(payload.get("episode") or {})
    trajectory_id = episode.get("trajectory_id", "unknown")
    task_id = episode.get("task_id", "unknown")
    evidence_items = _evidence_items(episode)
    if not evidence_items:
        return []

    evidence = _trim(evidence_items[0])
    provenance = _provenance_for(episode)
    repo_scope = episode.get("repo_scope")
    file_scope = _as_list(episode.get("file_paths") or episode.get("files"))
    task_scope = _as_list(task_id)
    tags = ["counterfactual-replay", "grounded-only", str(task_id)]

    memories: List[MemoryItem] = [
        new_memory(
            content=(
                f"Counterfactual replay for trajectory {trajectory_id}: at the decision point before the "
                f"observed evidence `{evidence}`, a safer alternative was to use that evidence to revise "
                "the next action instead of continuing the same path. This is unexecuted replay, so it "
                "should guide only similar failures with matching evidence."
            ),
            type=MemoryType.DREAM_ARTIFACT,
            write_reason="grounded counterfactual replay over a failed or ambiguous trajectory",
            provenance=provenance,
            status=MemoryStatus.REQUIRES_REVIEW,
            confidence=0.45,
            utility_score=0.65,
            risk_score=0.4,
            retrieval_tags=tags + ["uncertain"],
            repo_scope=repo_scope,
            file_scope=file_scope,
            task_scope=task_scope,
        )
    ]

    if _has_clear_failure(evidence_items):
        memories.append(new_memory(
            content=(
                f"Failure lesson from trajectory {trajectory_id}: future tasks should check for the "
                f"same observed evidence `{evidence}` before repeating the failed action path."
            ),
            type=MemoryType.FAILURE,
            write_reason="counterfactual replay found a repeated-error prevention opportunity in trajectory evidence",
            provenance=provenance,
            confidence=0.55,
            utility_score=0.7,
            risk_score=0.3,
            retrieval_tags=tags + ["failure"],
            repo_scope=repo_scope,
            file_scope=file_scope,
            task_scope=task_scope,
        ))
    return memories


def _selected_for_replay(episode: Dict[str, Any]) -> bool:
    outcome = str(episode.get("outcome") or episode.get("terminal_outcome") or "").lower()
    if outcome in {"failure", "failed", "partial", "ambiguous", "unknown"}:
        return True
    return _has_clear_failure(_evidence_items(episode))


def _evidence_items(episode: Dict[str, Any]) -> List[str]:
    items: List[str] = []
    observed_steps = episode.get("observed_steps")
    if observed_steps:
        for step in observed_steps:
            if isinstance(step, dict):
                items.extend(_as_list(step.get("observation")))
                items.extend(_as_list(step.get("action")))
    for key in ("failure_observations", "observations", "actions", "known_constraints"):
        items.extend(_as_list(episode.get(key)))
    return [item for item in items if item.strip()]


def _has_clear_failure(items: Sequence[str]) -> bool:
    text = "\n".join(items).lower()
    return any(word in text for word in ("error", "failed", "failure", "traceback", "exception", "timed out"))


def _coerce_memory(item: Union[MemoryItem, Dict[str, Any]]) -> MemoryItem:
    return item if isinstance(item, MemoryItem) else memory_from_dict(item)


def _episode_in_scope(episode: Dict[str, Any], episode_scope: Dict[str, Any]) -> bool:
    """Return True if the episode's scope intersects episode_scope.

    Used for local-maintenance to avoid replaying episodes from unrelated
    repos/files.  An episode with no scope metadata is always included.
    """
    ep_repo = str(episode_scope.get("repo") or "")
    ep_files: Set[str] = set(episode_scope.get("files") or [])

    ep_episode_repo = str(episode.get("repo_scope") or episode.get("sequence_id") or "")
    ep_episode_files: Set[str] = set(
        _as_list(episode.get("file_paths") or episode.get("files"))
    )

    if not ep_episode_repo and not ep_episode_files:
        return True  # no scope info — conservative include

    if ep_repo and ep_episode_repo and ep_episode_repo != ep_repo:
        return False

    if ep_episode_files and ep_files:
        return bool(ep_episode_files & ep_files)

    return True


__all__ = ["CounterfactualReplay", "default_judge", "run", "_episode_in_scope"]
