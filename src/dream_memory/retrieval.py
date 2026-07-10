"""Metadata-aware retrieval gate for DreamForge memories."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from dream_memory.memory_store import MemoryStore
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./-]+", (text or "").lower()))


def _as_list(value: Optional[Union[str, Sequence[str]]]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _parse_time(value: Optional[Union[str, datetime]]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class RetrievalTask:
    text: str = ""
    tags: List[str] = field(default_factory=list)
    repo_scope: Optional[str] = None
    files: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    task_id: Optional[str] = None
    phase: Optional[str] = None
    time: Optional[Union[str, datetime]] = None
    include_historical: bool = False
    require_provenance: bool = True
    allowed_types: Optional[List[MemoryType]] = None
    token_budget_read: Optional[int] = None


def task_from_any(task: Union[RetrievalTask, Dict[str, Any], str]) -> RetrievalTask:
    if isinstance(task, RetrievalTask):
        return task
    if isinstance(task, str):
        return RetrievalTask(text=task)
    data = dict(task)
    allowed_types = data.get("allowed_types") or data.get("types")
    if allowed_types is not None:
        allowed_types = [
            item if isinstance(item, MemoryType) else MemoryType(item)
            for item in allowed_types
        ]
    return RetrievalTask(
        text=str(data.get("text") or data.get("prompt") or data.get("query") or ""),
        tags=_as_list(data.get("tags") or data.get("retrieval_tags")),
        repo_scope=data.get("repo_scope") or data.get("repo"),
        files=_as_list(data.get("files") or data.get("file_scope")),
        symbols=_as_list(data.get("symbols") or data.get("symbol_scope")),
        task_id=data.get("task_id") or data.get("id"),
        phase=data.get("phase"),
        time=data.get("time") or data.get("timestamp"),
        include_historical=bool(data.get("include_historical", False)),
        require_provenance=bool(data.get("require_provenance", True)),
        allowed_types=allowed_types,
        token_budget_read=data.get("token_budget_read") or data.get("token_budget"),
    )


DEFAULT_WEIGHTS: Dict[str, float] = {
    "semantic": 1.4,
    "tag": 1.0,
    "file": 1.0,
    "symbol": 0.8,
    "task": 0.8,
    "type": 0.5,
    "confidence": 0.6,
    "utility": 0.8,
    "human_verified": 0.3,
    "provenance": 0.4,
    "staleness": 1.0,
    "risk": 1.2,
    "age": 0.2,
    "scope": 0.7,
    "contradiction": 0.5,
}


class RetrievalGate:
    """Hard-gated, metadata-aware retrieval over a ``MemoryStore``."""

    def __init__(
        self,
        memory_store: Optional[MemoryStore] = None,
        *,
        weights: Optional[Dict[str, float]] = None,
        theta_admit: float = 1.0,
        theta_risk_block: float = 0.95,
        theta_stale_block: float = 0.95,
    ) -> None:
        self.memory_store = memory_store
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.theta_admit = theta_admit
        self.theta_risk_block = theta_risk_block
        self.theta_stale_block = theta_stale_block
        self.last_decisions: List[Dict[str, Any]] = []

    def score(self, memory: MemoryItem, task: Union[RetrievalTask, Dict[str, Any], str]) -> float:
        """Score a candidate using the documented metadata-aware formula."""
        q = task_from_any(task)
        score = 0.0
        score += self.weights["semantic"] * self.semantic_sim(memory.content, q.text)
        score += self.weights["tag"] * self.tag_overlap(memory.retrieval_tags, q.tags)
        score += self.weights["file"] * self.file_scope_match(memory.file_scope, q.files)
        score += self.weights["symbol"] * self.symbol_scope_match(memory.symbol_scope, q.symbols)
        score += self.weights["task"] * self.task_scope_match(memory.task_scope, q.task_id)
        score += self.weights["type"] * self.type_prior(memory.type, q.phase)
        score += self.weights["confidence"] * memory.confidence
        score += self.weights["utility"] * memory.utility_score
        score += self.weights["human_verified"] * (1.0 if memory.human_verified else 0.0)
        score += self.weights["provenance"] * self.provenance_strength(memory)
        score -= self.weights["staleness"] * memory.staleness_score
        score -= self.weights["risk"] * memory.risk_score
        score -= self.weights["age"] * self.age_penalty(memory.updated_at, q.time)
        score -= self.weights["scope"] * self.overscope_penalty(memory, q)
        score -= self.weights["contradiction"] * self.contradiction_penalty(memory)
        return score

    def retrieve(
        self,
        task: Union[RetrievalTask, Dict[str, Any], str],
        k: int = 5,
        *,
        candidates: Optional[Iterable[MemoryItem]] = None,
    ) -> List[MemoryItem]:
        """Return gated top-k memories and touch only admitted results."""
        q = task_from_any(task)
        source = list(candidates) if candidates is not None else self._store_items()
        decisions: List[Dict[str, Any]] = []
        admitted: List[Tuple[float, MemoryItem]] = []

        for item in source:
            passed, reason = self.hard_gates(item, q)
            item_score = self.score(item, q)
            if not passed:
                decisions.append({
                    "memory_id": item.id,
                    "admitted": False,
                    "reason": reason,
                    "score": item_score,
                })
                continue
            if item_score < self.theta_admit:
                decisions.append({
                    "memory_id": item.id,
                    "admitted": False,
                    "reason": "below_admission_threshold",
                    "score": item_score,
                })
                continue
            admitted.append((item_score, item))
            decisions.append({
                "memory_id": item.id,
                "admitted": True,
                "reason": "admitted",
                "score": item_score,
            })

        admitted.sort(key=lambda pair: (-pair[0], pair[1].updated_at, pair[1].id))
        results: List[MemoryItem] = []
        tokens_used = 0
        budget = q.token_budget_read
        for _, item in admitted:
            if len(results) >= k:
                break
            estimated_tokens = max(1, len(item.content.split()))
            if budget is not None and tokens_used + estimated_tokens > int(budget):
                continue
            tokens_used += estimated_tokens
            item.touch()
            results.append(item)

        self.last_decisions = decisions
        return results

    def hard_gates(self, memory: MemoryItem, task: Union[RetrievalTask, Dict[str, Any], str]) -> Tuple[bool, str]:
        q = task_from_any(task)
        if not q.include_historical and memory.status != MemoryStatus.ACTIVE:
            return False, f"status_{memory.status.value}"

        until = _parse_time(memory.valid_until)
        q_time = _parse_time(q.time)
        if until is not None and q_time is not None and until < q_time:
            return False, "expired_valid_until"

        if memory.repo_scope and q.repo_scope and memory.repo_scope != q.repo_scope:
            return False, "repo_scope_conflict"

        if memory.file_scope and q.files and not (set(memory.file_scope) & set(q.files)):
            return False, "file_scope_conflict"

        if memory.symbol_scope and q.symbols and not (set(memory.symbol_scope) & set(q.symbols)):
            return False, "symbol_scope_conflict"

        if memory.task_scope and q.task_id and q.task_id not in memory.task_scope:
            return False, "task_scope_conflict"

        if self._has_active_superseder(memory):
            return False, "active_superseder"

        if memory.risk_score >= self.theta_risk_block:
            return False, "risk_block"

        if memory.staleness_score >= self.theta_stale_block:
            return False, "stale_block"

        if q.require_provenance and not memory.provenance.is_grounded():
            return False, "ungrounded_provenance"

        if q.allowed_types is not None and memory.type not in q.allowed_types:
            return False, "type_policy"

        return True, "passed"

    def semantic_sim(self, content: str, text: str) -> float:
        left = _tokens(content)
        right = _tokens(text)
        if not left or not right:
            return 0.0
        return len(left & right) / float(len(left | right))

    def tag_overlap(self, memory_tags: Sequence[str], task_tags: Sequence[str]) -> float:
        left = {tag.lower() for tag in memory_tags}
        right = {tag.lower() for tag in task_tags}
        if not left or not right:
            return 0.0
        return len(left & right) / float(len(right))

    def file_scope_match(self, memory_files: Sequence[str], task_files: Sequence[str]) -> float:
        return self._scope_match(memory_files, task_files, empty_score=0.2)

    def symbol_scope_match(self, memory_symbols: Sequence[str], task_symbols: Sequence[str]) -> float:
        return self._scope_match(memory_symbols, task_symbols, empty_score=0.2)

    def task_scope_match(self, memory_tasks: Sequence[str], task_id: Optional[str]) -> float:
        if not task_id:
            return 0.0
        if not memory_tasks:
            return 0.2
        return 1.0 if task_id in memory_tasks else 0.0

    def type_prior(self, memory_type: MemoryType, phase: Optional[str]) -> float:
        if not phase:
            return 0.5
        normalized = phase.lower()
        preferred = {
            "debug": {MemoryType.FAILURE, MemoryType.PROCEDURAL, MemoryType.CONSTRAINT},
            "implementation": {MemoryType.PROCEDURAL, MemoryType.SEMANTIC_PROJECT, MemoryType.CONSTRAINT},
            "coding": {MemoryType.PROCEDURAL, MemoryType.SEMANTIC_PROJECT, MemoryType.CONSTRAINT},
            "review": {MemoryType.HUMAN_FEEDBACK, MemoryType.CONSTRAINT, MemoryType.CONTRADICTION},
            "sleep": {MemoryType.DREAM_ARTIFACT, MemoryType.CONTRADICTION, MemoryType.FAILURE},
        }
        for key, memory_types in preferred.items():
            if key in normalized:
                return 1.0 if memory_type in memory_types else 0.45
        return 0.5

    def provenance_strength(self, memory: MemoryItem) -> float:
        provenance = memory.provenance
        fields = [
            provenance.trajectory_ids,
            provenance.task_ids,
            provenance.repo_commits,
            provenance.file_paths,
            provenance.command_outputs,
            provenance.human_feedback_ids,
        ]
        populated = sum(1 for values in fields if values)
        if populated == 0:
            return 0.0
        return min(1.0, 0.25 + 0.2 * populated)

    def age_penalty(self, updated_at: Optional[str], task_time: Optional[Union[str, datetime]]) -> float:
        if task_time is None:
            return 0.0
        updated = _parse_time(updated_at)
        current = _parse_time(task_time)
        if updated is None or current is None or current <= updated:
            return 0.0
        days = (current - updated).total_seconds() / 86400.0
        return min(1.0, math.log1p(days) / math.log1p(365.0))

    def overscope_penalty(self, memory: MemoryItem, task: RetrievalTask) -> float:
        penalty = 0.0
        task_has_scope = bool(task.repo_scope or task.files or task.symbols or task.task_id)
        if task_has_scope and not (memory.repo_scope or memory.file_scope or memory.symbol_scope or memory.task_scope):
            penalty += 0.4
        if task.files and not memory.file_scope:
            penalty += 0.15
        if task.symbols and not memory.symbol_scope:
            penalty += 0.15
        broad_terms = {"always", "never", "every", "all", "must", "do not", "dont"}
        content = memory.content.lower()
        if any(term in content for term in broad_terms) and not (memory.file_scope or memory.symbol_scope or memory.task_scope):
            penalty += 0.3
        if memory.type in {MemoryType.HUMAN_FEEDBACK, MemoryType.CONSTRAINT} and not (memory.file_scope or memory.task_scope):
            penalty += 0.2
        return min(1.0, penalty)

    def contradiction_penalty(self, memory: MemoryItem) -> float:
        if not memory.contradicts:
            return 0.1 if memory.type == MemoryType.CONTRADICTION else 0.0
        active_conflict = 0.0
        if self.memory_store is not None:
            for memory_id in memory.contradicts:
                try:
                    if self.memory_store.get(memory_id).status == MemoryStatus.ACTIVE:
                        active_conflict = 0.5
                        break
                except KeyError:
                    continue
        else:
            active_conflict = 0.3
        return min(1.0, 0.2 + active_conflict)

    def _store_items(self) -> List[MemoryItem]:
        if self.memory_store is None:
            raise ValueError("retrieve() requires a memory_store or explicit candidates")
        return self.memory_store.get_all(include_inactive=True)

    def _has_active_superseder(self, memory: MemoryItem) -> bool:
        if not memory.superseded_by:
            return False
        if self.memory_store is None:
            return True
        for memory_id in memory.superseded_by:
            try:
                if self.memory_store.get(memory_id).status == MemoryStatus.ACTIVE:
                    return True
            except KeyError:
                continue
        return False

    @staticmethod
    def _scope_match(memory_scope: Sequence[str], task_scope: Sequence[str], *, empty_score: float) -> float:
        memory_values = set(memory_scope)
        task_values = set(task_scope)
        if not task_values:
            return 0.0
        if not memory_values:
            return empty_score
        return len(memory_values & task_values) / float(len(task_values))


__all__ = ["RetrievalGate", "RetrievalTask", "task_from_any"]
