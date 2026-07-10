"""DreamForge typed memory schema.

Implemented with the standard library (dataclasses + enum) so it runs and tests
green with zero third-party dependencies. The production target is Pydantic v2;
the field set and validation here mirror what a Pydantic model would enforce.

Design principle (see CLAUDE.md): raw episodes are first-class evidence. Consolidated
memories are DERIVED artifacts that always carry provenance back to source
trajectories, and contradiction repair SUPERSEDES rather than deletes. The
DELETED status is a logical soft-delete only: stores may mark derived memories
deleted to block retrieval, but they must preserve the record and provenance.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "mem") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC_PROJECT = "semantic_project"
    PROCEDURAL = "procedural"
    FAILURE = "failure"
    HUMAN_FEEDBACK = "human_feedback"
    CONSTRAINT = "constraint"
    CONTRADICTION = "contradiction"
    DREAM_ARTIFACT = "dream_artifact"


class MemoryStatus(str, Enum):
    """Lifecycle status for derived memories.

    ``DELETED`` is logical/soft-delete status, not permission to physically
    remove the record from the store.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    STALE = "stale"
    REQUIRES_REVIEW = "requires_review"
    DELETED = "deleted"


def _check_unit(name: str, value: float) -> None:
    if not (0.0 <= float(value) <= 1.0):
        raise ValueError(f"{name} must be in [0,1], got {value!r}")


@dataclass
class Provenance:
    """Auditable links from a derived memory back to raw evidence."""
    trajectory_ids: List[str] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)
    repo_commits: List[str] = field(default_factory=list)
    file_paths: List[str] = field(default_factory=list)
    command_outputs: List[str] = field(default_factory=list)
    human_feedback_ids: List[str] = field(default_factory=list)

    def is_grounded(self) -> bool:
        """A memory is grounded if it links to at least one piece of raw evidence."""
        return any([
            self.trajectory_ids, self.task_ids, self.repo_commits,
            self.file_paths, self.command_outputs, self.human_feedback_ids,
        ])


@dataclass
class MemoryItem:
    content: str
    type: MemoryType
    provenance: Provenance
    write_reason: str
    id: str = field(default_factory=lambda: _new_id())
    status: MemoryStatus = MemoryStatus.ACTIVE
    repo_scope: Optional[str] = None
    file_scope: List[str] = field(default_factory=list)
    symbol_scope: List[str] = field(default_factory=list)
    task_scope: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    confidence: float = 0.5
    utility_score: float = 0.5
    risk_score: float = 0.0
    staleness_score: float = 0.0
    contradicts: List[str] = field(default_factory=list)
    supersedes: List[str] = field(default_factory=list)
    superseded_by: List[str] = field(default_factory=list)
    human_verified: bool = False
    retrieval_tags: List[str] = field(default_factory=list)
    embedding_id: Optional[str] = None
    last_used_at: Optional[str] = None
    use_count: int = 0
    outcome_impact: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.type, MemoryType):
            self.type = MemoryType(self.type)
        if not isinstance(self.status, MemoryStatus):
            self.status = MemoryStatus(self.status)
        if not self.content or not self.content.strip():
            raise ValueError("content must be non-empty")
        if not self.write_reason or not self.write_reason.strip():
            raise ValueError("write_reason must be non-empty (provenance of the write)")
        for n in ("confidence", "utility_score", "risk_score", "staleness_score"):
            _check_unit(n, getattr(self, n))
        if self.use_count < 0:
            raise ValueError("use_count must be >= 0")

    # --- lifecycle helpers (the maintenance operators act through these) ---
    def is_active(self) -> bool:
        return self.status == MemoryStatus.ACTIVE

    def touch(self) -> None:
        """Record a retrieval/use of this memory."""
        self.use_count += 1
        self.last_used_at = _utcnow()
        self.updated_at = _utcnow()

    def mark_stale(self, score: float = 1.0) -> None:
        _check_unit("staleness_score", score)
        self.staleness_score = score
        self.status = MemoryStatus.STALE
        self.updated_at = _utcnow()

    def supersede_with(self, newer_id: str) -> None:
        """Contradiction repair: this memory is superseded, NOT deleted. Evidence is kept."""
        if newer_id not in self.superseded_by:
            self.superseded_by.append(newer_id)
        self.status = MemoryStatus.SUPERSEDED
        self.updated_at = _utcnow()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        return d


def new_memory(content: str, type: MemoryType, write_reason: str,
               provenance: Optional[Provenance] = None, **kwargs: Any) -> MemoryItem:
    """Factory enforcing the non-negotiable invariant: every memory carries a write_reason
    and a Provenance object (grounded memories link back to raw episodes)."""
    return MemoryItem(
        content=content, type=type, write_reason=write_reason,
        provenance=provenance or Provenance(), **kwargs,
    )
