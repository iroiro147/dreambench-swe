"""Contradiction repair for DreamForge memories.

The repair operator links contradictory memories and supersedes stale derived
items instead of deleting them. A judge callable can be injected for model-based
repair; the default judge is deterministic and deliberately conservative.

LOCAL SCOPE (ANM Finding 5): by default (maintenance_scope='local') repair only
considers existing memories whose file/repo scope intersects the current
episode's changed scope.  Pass maintenance_scope='global' for whole-store repair.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from dream_memory.memory_store import MemoryStore
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType, Provenance, new_memory


Judge = Callable[[MemoryItem, MemoryItem], Dict[str, Any]]


def apply(
    proposed: Iterable[MemoryItem],
    memory_store: MemoryStore,
    *,
    judge: Optional[Judge] = None,
    episode_scope: Optional[Dict[str, Any]] = None,
    maintenance_scope: str = "local",
) -> List[MemoryItem]:
    return ContradictionRepair(judge).apply(
        proposed,
        memory_store,
        episode_scope=episode_scope,
        maintenance_scope=maintenance_scope,
    )


class ContradictionRepair:
    def __init__(self, judge: Optional[Judge] = None) -> None:
        self.judge = judge or default_judge

    def apply(
        self,
        proposed: Iterable[MemoryItem],
        memory_store: MemoryStore,
        *,
        episode_scope: Optional[Dict[str, Any]] = None,
        maintenance_scope: str = "local",
    ) -> List[MemoryItem]:
        repaired: List[MemoryItem] = []
        contradiction_records: List[MemoryItem] = []
        proposed_items = sorted(list(proposed), key=_repair_sort_key)
        all_active_existing = [
            memory for memory in memory_store.get_all(include_inactive=False)
            if memory.type != MemoryType.EPISODIC
        ]
        if maintenance_scope == "local" and episode_scope is not None:
            active_existing = [
                m for m in all_active_existing
                if _memory_in_episode_scope(m, episode_scope)
            ]
        else:
            active_existing = all_active_existing
        active_existing = sorted(active_existing, key=_repair_sort_key)

        for candidate in proposed_items:
            if candidate.type == MemoryType.EPISODIC:
                repaired.append(candidate)
                continue
            candidate_conflicts: List[Tuple[MemoryItem, Dict[str, Any]]] = []
            in_session_candidates = [
                item for item in repaired
                if item.status == MemoryStatus.ACTIVE
                and (
                    maintenance_scope != "local"
                    or episode_scope is None
                    or _memory_in_episode_scope(item, episode_scope)
                )
            ]
            for existing in active_existing + in_session_candidates:
                if existing.id == candidate.id:
                    continue
                decision = self.judge(existing, candidate)
                if decision.get("contradicts"):
                    candidate_conflicts.append((existing, decision))

            for existing, decision in candidate_conflicts:
                _link_contradiction(existing, candidate)
                if decision.get("newer_supersedes"):
                    existing.supersede_with(candidate.id)
                    if existing.id not in candidate.supersedes:
                        candidate.supersedes.append(existing.id)
                elif decision.get("requires_review", True):
                    candidate.status = MemoryStatus.REQUIRES_REVIEW
                    candidate.confidence = min(candidate.confidence, 0.5)
                    candidate.risk_score = max(candidate.risk_score, 0.45)

                contradiction_records.append(_contradiction_record(existing, candidate, decision))

            repaired.append(candidate)
        return repaired + contradiction_records


def _repair_sort_key(memory: MemoryItem) -> Tuple[str, str, str]:
    return (memory.type.value, memory.created_at, memory.id)


def default_judge(existing: MemoryItem, candidate: MemoryItem) -> Dict[str, Any]:
    """Detect obvious conflicts in the same scope without external knowledge."""
    if not _scope_compatible(existing, candidate):
        return {"contradicts": False, "reason": "scope differs"}
    if existing.type != candidate.type and MemoryType.CONTRADICTION not in {existing.type, candidate.type}:
        comparable = {
            MemoryType.SEMANTIC_PROJECT,
            MemoryType.PROCEDURAL,
            MemoryType.CONSTRAINT,
            MemoryType.HUMAN_FEEDBACK,
            MemoryType.FAILURE,
        }
        if existing.type not in comparable or candidate.type not in comparable:
            return {"contradicts": False, "reason": "type differs"}

    reason = _conflict_reason(existing.content, candidate.content)
    if not reason:
        return {"contradicts": False, "reason": "no deterministic conflict"}

    candidate_is_grounded = candidate.provenance.is_grounded()
    candidate_is_at_least_as_strong = candidate.confidence >= existing.confidence
    newer_supersedes = candidate_is_grounded and candidate_is_at_least_as_strong
    return {
        "contradicts": True,
        "reason": reason,
        "newer_supersedes": newer_supersedes,
        "requires_review": not newer_supersedes,
    }


def _scope_compatible(left: MemoryItem, right: MemoryItem) -> bool:
    if left.repo_scope and right.repo_scope and left.repo_scope != right.repo_scope:
        return False
    for attr in ("file_scope", "symbol_scope", "task_scope"):
        left_values = set(getattr(left, attr))
        right_values = set(getattr(right, attr))
        if left_values and right_values and not (left_values & right_values):
            return False
    return True


def _conflict_reason(left: str, right: str) -> Optional[str]:
    left_l = left.lower()
    right_l = right.lower()
    if left_l == right_l:
        return None

    left_slots = _fact_slots(left_l)
    right_slots = _fact_slots(right_l)
    for key, left_value in left_slots.items():
        right_value = right_slots.get(key)
        if right_value and right_value != left_value:
            return f"conflicting {key}: {left_value} vs {right_value}"

    left_polarity = _action_polarity(left_l)
    right_polarity = _action_polarity(right_l)
    if left_polarity and right_polarity and left_polarity[0] == right_polarity[0] and left_polarity[1] != right_polarity[1]:
        return f"opposite instruction about {left_polarity[0]}"
    return None


def _fact_slots(text: str) -> Dict[str, str]:
    slots: Dict[str, str] = {}
    patterns = [
        ("uses", r"\b(?:uses|use|using|framework is|test runner is)\s+([a-z0-9_.-]+)"),
        ("located", r"\b(?:lives in|located in)\s+([a-z0-9_./-]+)"),
        ("requires", r"\b(?:requires|needs)\s+([a-z0-9_.-]+)"),
    ]
    for key, pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip("`'\".,:;")
            slots[key] = value
    return slots


def _action_polarity(text: str) -> Optional[Tuple[str, str]]:
    negative = re.search(r"\b(?:do not|don't|never|avoid)\s+([a-z0-9_.-]+)", text)
    if negative:
        return negative.group(1), "negative"
    positive = re.search(r"\b(?:must|always|prefer|use)\s+([a-z0-9_.-]+)", text)
    if positive:
        return positive.group(1), "positive"
    return None


def _link_contradiction(left: MemoryItem, right: MemoryItem) -> None:
    if right.id not in left.contradicts:
        left.contradicts.append(right.id)
    if left.id not in right.contradicts:
        right.contradicts.append(left.id)


def _contradiction_record(existing: MemoryItem, candidate: MemoryItem, decision: Dict[str, Any]) -> MemoryItem:
    provenance = _merge_provenance(existing.provenance, candidate.provenance)
    status = MemoryStatus.ACTIVE if decision.get("newer_supersedes") else MemoryStatus.REQUIRES_REVIEW
    return new_memory(
        content=f"Contradiction repair linked {existing.id} and {candidate.id}: {decision.get('reason', 'conflict')}.",
        type=MemoryType.CONTRADICTION,
        write_reason="contradiction repair detected incompatible active memories",
        provenance=provenance,
        status=status,
        confidence=min(existing.confidence, candidate.confidence, 0.8),
        utility_score=0.55,
        risk_score=0.35,
        contradicts=[existing.id, candidate.id],
        retrieval_tags=["contradiction-repair", "contradiction"],
        repo_scope=candidate.repo_scope or existing.repo_scope,
        file_scope=_dedupe(existing.file_scope + candidate.file_scope),
        symbol_scope=_dedupe(existing.symbol_scope + candidate.symbol_scope),
        task_scope=_dedupe(existing.task_scope + candidate.task_scope),
    )


def _merge_provenance(left: Provenance, right: Provenance) -> Provenance:
    return Provenance(
        trajectory_ids=_dedupe(left.trajectory_ids + right.trajectory_ids),
        task_ids=_dedupe(left.task_ids + right.task_ids),
        repo_commits=_dedupe(left.repo_commits + right.repo_commits),
        file_paths=_dedupe(left.file_paths + right.file_paths),
        command_outputs=_dedupe(left.command_outputs + right.command_outputs),
        human_feedback_ids=_dedupe(left.human_feedback_ids + right.human_feedback_ids),
    )


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _memory_in_episode_scope(memory: MemoryItem, episode_scope: Dict[str, Any]) -> bool:
    """Return True if the memory's scope intersects the episode's changed scope.

    Used by local-maintenance mode to avoid O(n^2) whole-store repair.
    Matches on repo, files, or symbols; a memory with no scope metadata is
    always included (conservative: could be globally relevant).
    """
    ep_repo = str(episode_scope.get("repo") or "")
    ep_files: Set[str] = set(episode_scope.get("files") or [])
    ep_symbols: Set[str] = set(episode_scope.get("symbols") or [])

    # memory with no scope info is always a candidate (conservative)
    if not memory.repo_scope and not memory.file_scope and not memory.symbol_scope:
        return True

    if ep_repo and memory.repo_scope and memory.repo_scope != ep_repo:
        return False  # different repo — definitely out of scope

    if memory.file_scope and ep_files:
        mem_files: Set[str] = set(memory.file_scope)
        if mem_files & ep_files:
            return True

    if memory.symbol_scope and ep_symbols:
        mem_symbols: Set[str] = set(memory.symbol_scope)
        if mem_symbols & ep_symbols:
            return True

    # has scope metadata but none of the specific files/symbols overlap —
    # but if it only has repo_scope (and repo matches), still include it
    if memory.repo_scope and ep_repo and memory.repo_scope == ep_repo:
        if not memory.file_scope and not memory.symbol_scope:
            return True

    # file_scope exists but no overlap with episode files — exclude
    if memory.file_scope and ep_files and not (set(memory.file_scope) & ep_files):
        return False

    return True


__all__ = ["ContradictionRepair", "apply", "default_judge", "_memory_in_episode_scope"]
