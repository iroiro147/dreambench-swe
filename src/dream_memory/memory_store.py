"""In-memory DreamForge memory store.

The store persists ``MemoryItem`` objects from ``schemas.py`` and deliberately
has no destructive delete operation. Maintenance operators can add derived
memories, update metadata, mark stale, supersede older derived memories, and
soft-delete derived memories by status while preserving every item and its
provenance for audit.
"""
from __future__ import annotations

import json
import re
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from dream_memory.schemas import MemoryItem, MemoryStatus, Provenance


MemoryLike = Union[MemoryItem, Dict[str, Any]]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./-]+", text.lower()))


def _as_list(value: Optional[Union[str, Sequence[str]]]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def memory_from_dict(payload: Dict[str, Any]) -> MemoryItem:
    """Build a ``MemoryItem`` from a JSON-compatible dictionary.

    ``created_at`` and ``updated_at`` are allowed to be ``None`` in prompt
    output contracts; when absent or null we let the schema defaults fill them.
    Unknown keys are ignored so older JSON snapshots remain loadable after
    adding unrelated fields elsewhere in the project.
    """
    data = dict(payload)
    if isinstance(data.get("provenance"), Provenance):
        provenance = data["provenance"]
    else:
        provenance = Provenance(**dict(data.get("provenance") or {}))
    data["provenance"] = provenance

    for key in ("created_at", "updated_at"):
        if data.get(key) is None:
            data.pop(key, None)
    if data.get("id") is None:
        data.pop("id", None)

    allowed = {field.name for field in fields(MemoryItem)}
    filtered = {key: value for key, value in data.items() if key in allowed}
    return MemoryItem(**filtered)


class MemoryStore:
    """Append-preserving in-memory store for DreamForge memories."""

    def __init__(
        self,
        items: Optional[Iterable[MemoryLike]] = None,
        *,
        namespace: Optional[str] = None,
    ) -> None:
        self.namespace = namespace
        self._items: Dict[str, MemoryItem] = {}
        self._order: List[str] = []
        for item in items or []:
            self.add(item)

    def __len__(self) -> int:
        return len(self._order)

    def __contains__(self, memory_id: str) -> bool:
        return memory_id in self._items

    def add(self, memory: MemoryLike, *, allow_existing: bool = False) -> MemoryItem:
        """Add a memory item without replacing an existing item.

        Existing ids are rejected by default because replacing an item would
        obscure the audit trail. Pass ``allow_existing=True`` only for idempotent
        loading or pipeline retries; it still keeps the original object.
        """
        item = memory if isinstance(memory, MemoryItem) else memory_from_dict(memory)
        if item.id in self._items:
            if allow_existing:
                return self._items[item.id]
            raise ValueError(f"memory id already exists: {item.id}")
        self._items[item.id] = item
        self._order.append(item.id)
        return item

    def get(self, memory_id: str) -> MemoryItem:
        try:
            return self._items[memory_id]
        except KeyError as exc:
            raise KeyError(f"unknown memory id: {memory_id}") from exc

    def get_all(
        self,
        *,
        status: Optional[Union[MemoryStatus, str]] = None,
        include_inactive: bool = True,
    ) -> List[MemoryItem]:
        items = [self._items[memory_id] for memory_id in self._order]
        if not include_inactive:
            items = [item for item in items if item.status == MemoryStatus.ACTIVE]
        if status is not None:
            wanted = status if isinstance(status, MemoryStatus) else MemoryStatus(status)
            items = [item for item in items if item.status == wanted]
        return list(items)

    def update(
        self,
        memory_id: str,
        updates: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> MemoryItem:
        """Update allowed metadata fields on an existing memory.

        This method mutates the stored object in place so provenance and object
        identity remain stable. It refuses id changes and provenance
        replacement with invalid shapes. Setting ``status=deleted`` is a
        logical soft-delete; it never removes the stored record.
        """
        item = self.get(memory_id)
        patch: Dict[str, Any] = {}
        if updates:
            patch.update(updates)
        patch.update(kwargs)
        if "id" in patch and patch["id"] != memory_id:
            raise ValueError("memory id cannot be changed")
        patch.pop("id", None)

        allowed = {field.name for field in fields(MemoryItem)}
        original_values = {key: getattr(item, key) for key in patch if key in allowed}
        original_updated_at = item.updated_at
        try:
            for key, value in patch.items():
                if key not in allowed:
                    raise ValueError(f"unknown MemoryItem field: {key}")
                if key == "status":
                    status = value if isinstance(value, MemoryStatus) else MemoryStatus(value)
                    value = status
                elif key == "provenance" and isinstance(value, dict):
                    value = Provenance(**value)
                setattr(item, key, value)

            item.updated_at = _utcnow()
            item.__post_init__()
        except Exception:
            for key, value in original_values.items():
                setattr(item, key, value)
            item.updated_at = original_updated_at
            raise
        return item

    def supersede(self, old_id: str, newer: Union[str, MemoryLike]) -> Tuple[MemoryItem, MemoryItem]:
        """Mark ``old_id`` as superseded by ``newer`` without deleting either."""
        old = self.get(old_id)
        if isinstance(newer, str):
            new_item = self.get(newer)
        elif isinstance(newer, MemoryItem) and newer.id in self:
            new_item = self.get(newer.id)
        elif isinstance(newer, dict) and newer.get("id") in self:
            new_item = self.get(str(newer["id"]))
        else:
            new_item = self.add(newer)

        old.supersede_with(new_item.id)
        if old.id not in new_item.supersedes:
            new_item.supersedes.append(old.id)
        new_item.updated_at = _utcnow()
        return old, new_item

    def mark_stale(self, memory_id: str, score: float = 1.0) -> MemoryItem:
        item = self.get(memory_id)
        item.mark_stale(score)
        return item

    def soft_delete(self, memory_id: str) -> MemoryItem:
        """Logically delete a memory while preserving the record and provenance."""
        item = self.get(memory_id)
        item.status = MemoryStatus.DELETED
        item.updated_at = _utcnow()
        return item

    def search(
        self,
        keyword: Optional[str] = None,
        *,
        tags: Optional[Sequence[str]] = None,
        scope: Optional[Union[str, Dict[str, Any]]] = None,
        status: Optional[Union[MemoryStatus, str]] = None,
        include_inactive: bool = False,
        limit: Optional[int] = None,
        with_scores: bool = False,
    ) -> Union[List[MemoryItem], List[Tuple[float, MemoryItem]]]:
        """Search active memories by keyword, tag, and scope signals.

        This is intentionally symbolic and deterministic. It is not the final
        retrieval gate; it is broad candidate generation for tests and small
        offline runs.
        """
        query_tokens = _tokens(keyword or "")
        query_tags = {tag.lower() for tag in (tags or [])}
        scope_data = self._normalize_scope(scope)

        scored: List[Tuple[float, MemoryItem]] = []
        for item in self.get_all(status=status, include_inactive=include_inactive):
            score = 0.0

            if query_tokens:
                haystack = _tokens(" ".join([item.content, " ".join(item.retrieval_tags)]))
                overlap = len(query_tokens & haystack)
                if overlap == 0:
                    continue
                score += overlap / float(len(query_tokens))

            if query_tags:
                item_tags = {tag.lower() for tag in item.retrieval_tags}
                tag_overlap = len(query_tags & item_tags)
                if tag_overlap:
                    score += 2.0 * tag_overlap / float(len(query_tags))
                elif not query_tokens:
                    continue

            scope_score = self._scope_score(item, scope_data)
            if scope_data and scope_score == 0.0 and not query_tokens and not query_tags:
                continue
            score += scope_score

            if not query_tokens and not query_tags and not scope_data:
                score = 1.0
            scored.append((score, item))

        scored.sort(key=lambda pair: (-pair[0], pair[1].created_at, pair[1].id))
        if limit is not None:
            scored = scored[:limit]
        if with_scores:
            return scored
        return [item for _, item in scored]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespace": self.namespace,
            "memories": [self._items[memory_id].to_dict() for memory_id in self._order],
        }

    def save(self, path: Union[str, Path]) -> None:
        target = Path(path)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    save_json = save

    @classmethod
    def load(cls, path: Union[str, Path]) -> "MemoryStore":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return cls(data)
        return cls(data.get("memories", []), namespace=data.get("namespace"))

    load_json = load

    @staticmethod
    def _normalize_scope(scope: Optional[Union[str, Dict[str, Any]]]) -> Dict[str, Any]:
        if scope is None:
            return {}
        if isinstance(scope, str):
            return {"repo_scope": scope}
        data = dict(scope)
        return {
            "repo_scope": data.get("repo_scope") or data.get("repo"),
            "files": _as_list(data.get("files") or data.get("file_scope")),
            "symbols": _as_list(data.get("symbols") or data.get("symbol_scope")),
            "task_id": data.get("task_id"),
            "tasks": _as_list(data.get("tasks") or data.get("task_scope")),
        }

    @staticmethod
    def _scope_score(item: MemoryItem, scope_data: Dict[str, Any]) -> float:
        if not scope_data:
            return 0.0
        score = 0.0
        repo_scope = scope_data.get("repo_scope")
        if repo_scope and item.repo_scope == repo_scope:
            score += 1.0

        files = set(scope_data.get("files") or [])
        if files and item.file_scope:
            score += len(files & set(item.file_scope)) / float(len(files))

        symbols = set(scope_data.get("symbols") or [])
        if symbols and item.symbol_scope:
            score += len(symbols & set(item.symbol_scope)) / float(len(symbols))

        task_ids = set(scope_data.get("tasks") or [])
        if scope_data.get("task_id"):
            task_ids.add(str(scope_data["task_id"]))
        if task_ids and item.task_scope:
            score += len(task_ids & set(item.task_scope)) / float(len(task_ids))
        return score


__all__ = ["MemoryStore", "memory_from_dict"]
