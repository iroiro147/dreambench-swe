"""Offline memory-policy baselines for the DreamForge experiment harness.

The policies in this module are synthetic-harness implementations of B0-B7.
They are not claims of fidelity to third-party systems.  Where the paper design
depends on model calls, embeddings, or external baseline packages, the behavior
is behind injectable callables and defaults to deterministic stdlib logic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Type

from dream_memory.consolidation import Extractor, sleep_pipeline
from dream_memory.memory_store import MemoryStore
from dream_memory.retrieval import RetrievalGate
from dream_memory.schemas import MemoryItem, MemoryStatus, MemoryType, Provenance, new_memory
from dream_memory.trajectory_logger import Trajectory, TrajectoryLogger


SummaryCallable = Callable[[Dict[str, Any]], str]
ReflectionCallable = Callable[[Dict[str, Any]], Sequence[str]]
EmbedderCallable = Callable[[str], Sequence[str]]
SubtaskCallable = Callable[[Dict[str, Any]], Sequence[str]]
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEM0_FIXTURE_ROOT = ROOT / "experiments" / "fixtures" / "mem0"
MEM0_NAMESPACE_PREFIX = "df3seed"
MEM0_FIXTURE_ROOT = Path(os.environ.get("DREAMBENCH_MEM0_FIXTURE_ROOT") or DEFAULT_MEM0_FIXTURE_ROOT)
MEM0_EVENT_API_BASE = "https://api.mem0.ai/v1/event"
MEM0_READ_TOKEN_BUDGET = 1200
DF_TOKEN_BUDGET_READ = 1200
MEM0_FORBIDDEN_PAYLOAD_MARKERS = (
    "oracle_cmd",
    "reference_patch",
    "refsol",
    "experiments/env/oracles",
    "experiments/env/refsol",
    "experiments/env/sequences",
    "hidden oracle",
    "failure_observations",
)


def _mem0_fixture_root() -> Path:
    return Path(os.environ.get("DREAMBENCH_MEM0_FIXTURE_ROOT") or DEFAULT_MEM0_FIXTURE_ROOT)


def _mem0_namespace_prefix() -> str:
    return os.environ.get("DREAMBENCH_NAMESPACE_PREFIX") or MEM0_NAMESPACE_PREFIX


class MemoryPolicy(ABC):
    """Read/write contract shared by every baseline policy."""

    def __init__(self, *, name: str, label: str, description: str, read_limit: int = 6) -> None:
        self.name = name
        self.label = label
        self.description = description
        self.read_limit = read_limit
        self.last_read_context: List[Dict[str, Any]] = []
        self.last_retrieval_decisions: List[Dict[str, Any]] = []
        self.last_write_items: List[MemoryItem] = []
        self.last_write_count = 0
        self.last_sleep_tokens = 0

    @abstractmethod
    def read(self, task: Any) -> List[Dict[str, Any]]:
        """Return admitted memory context for the public task."""

    @abstractmethod
    def write(self, trajectory: Any) -> None:
        """Persist a completed trajectory or derived memories."""

    def memory_items(self) -> List[MemoryItem]:
        return []

    def snapshot(self) -> Dict[str, Any]:
        items = self.memory_items()
        active = [item for item in items if item.status == MemoryStatus.ACTIVE]
        return {
            "condition": self.name,
            "label": self.label,
            "description": self.description,
            "memory_count": len(items),
            "active_memory_count": len(active),
            "last_read_count": len(self.last_read_context),
            "last_write_count": self.last_write_count,
        }

    def describe(self) -> Dict[str, Any]:
        payload = self.snapshot()
        payload["policy_class"] = type(self).__name__
        return payload

    def _reset_write_stats(self) -> None:
        self.last_write_items = []
        self.last_write_count = 0
        self.last_sleep_tokens = 0

    def _set_read_context(self, context: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self.last_read_context = list(context)
        return self.last_read_context


class NoMemoryPolicy(MemoryPolicy):
    """B0: no external memory reads or writes."""

    def read(self, task: Any) -> List[Dict[str, Any]]:
        self.last_retrieval_decisions = []
        return self._set_read_context([])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()


class RawTrajectoryPolicy(MemoryPolicy):
    """B1: retrieve raw prior trajectory snippets, with no derived lifecycle."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.logger = TrajectoryLogger()

    def read(self, task: Any) -> List[Dict[str, Any]]:
        task_data = _task_mapping(task)
        query = _task_text(task_data)
        sequence_id = _sequence_id(task_data)
        limit = _effective_read_limit(task_data, self.read_limit)
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for episode in self.logger.raw_episodes():
            if _episode_sequence_id(episode) != sequence_id:
                continue
            score = _jaccard(_tokens(query), _tokens(_episode_text(episode)))
            scored.append((score, episode))
        scored.sort(key=lambda pair: (-pair[0], _stable_episode_key(pair[1])))
        context = [
            _context_from_episode(episode, rank=rank + 1, score=score)
            for rank, (score, episode) in enumerate(scored[:limit])
        ]
        self.last_retrieval_decisions = [
            {"memory_id": item["id"], "admitted": True, "score": item["score"], "reason": "raw_trajectory"}
            for item in context
        ]
        return self._set_read_context(context)

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        self.logger.append(_trajectory_from_any(trajectory))
        self.last_write_count = 1


class SummaryPolicy(MemoryPolicy):
    """B4: deterministic untyped session summaries."""

    def __init__(self, *, summarizer: Optional[SummaryCallable] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.summarizer = summarizer or default_summary
        self.store = MemoryStore(namespace=self.name)

    def read(self, task: Any) -> List[Dict[str, Any]]:
        items = _store_search(self.store, task, _effective_read_limit(task, self.read_limit))
        self.last_retrieval_decisions = [
            {"memory_id": item.id, "admitted": True, "reason": "summary_search"}
            for item in items
        ]
        return self._set_read_context([_context_from_memory(item) for item in items])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        content = self.summarizer(episode)
        if not content.strip():
            return
        memory = self.store.add(new_memory(
            content=content,
            type=MemoryType.EPISODIC,
            write_reason="deterministic summary baseline write",
            provenance=_provenance_for(episode),
            confidence=0.65,
            utility_score=0.45,
            risk_score=0.2,
            retrieval_tags=["summary", _episode_sequence_id(episode)],
            repo_scope=_episode_sequence_id(episode),
            file_scope=_as_list(episode.get("file_paths") or episode.get("files")),
            task_scope=[_episode_sequence_id(episode)],
        ))
        self.last_write_items = [memory]
        self.last_write_count = 1
        self.last_sleep_tokens = _token_count(memory.content)

    def memory_items(self) -> List[MemoryItem]:
        return self.store.get_all(include_inactive=True)


class ReflectionPolicy(MemoryPolicy):
    """B3: free-form verbal lessons after each session."""

    def __init__(self, *, reflector: Optional[ReflectionCallable] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reflector = reflector or default_reflections
        self.store = MemoryStore(namespace=self.name)

    def read(self, task: Any) -> List[Dict[str, Any]]:
        items = _store_search(self.store, task, _effective_read_limit(task, self.read_limit))
        self.last_retrieval_decisions = [
            {"memory_id": item.id, "admitted": True, "reason": "reflection_search"}
            for item in items
        ]
        return self._set_read_context([_context_from_memory(item) for item in items])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        writes: List[MemoryItem] = []
        for lesson in self.reflector(episode):
            memory_type = MemoryType.FAILURE if _episode_failed(episode) else MemoryType.PROCEDURAL
            if episode.get("human_feedback"):
                memory_type = MemoryType.HUMAN_FEEDBACK
            writes.append(self.store.add(new_memory(
                content=lesson,
                type=memory_type,
                write_reason="deterministic reflection baseline write",
                provenance=_provenance_for(episode),
                confidence=0.6,
                utility_score=0.6,
                risk_score=0.35,
                retrieval_tags=["reflection", _episode_sequence_id(episode)],
                repo_scope=_episode_sequence_id(episode),
                file_scope=_as_list(episode.get("file_paths") or episode.get("files")),
                task_scope=[_episode_sequence_id(episode)],
            )))
        self.last_write_items = writes
        self.last_write_count = len(writes)
        self.last_sleep_tokens = sum(_token_count(item.content) for item in writes)

    def memory_items(self) -> List[MemoryItem]:
        return self.store.get_all(include_inactive=True)


class VectorPolicy(MemoryPolicy):
    """B2: token-vector retrieval over prior traces.

    The default embedder is a deterministic token set.  A real embedding model
    can be injected through ``embedder`` by the experiment launcher.
    """

    def __init__(self, *, embedder: Optional[EmbedderCallable] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.embedder = embedder or default_embedder
        self.store = MemoryStore(namespace=self.name)

    def read(self, task: Any) -> List[Dict[str, Any]]:
        task_vec = set(self.embedder(_task_text(task)))
        scored: List[Tuple[float, MemoryItem]] = []
        sequence_id = _sequence_id(task)
        limit = _effective_read_limit(task, self.read_limit)
        for item in self.store.get_all(include_inactive=False):
            if item.repo_scope and item.repo_scope != sequence_id:
                continue
            score = _jaccard(task_vec, set(self.embedder(item.content)))
            if score > 0.0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], _stable_memory_key(pair[1])))
        selected = scored[:limit]
        self.last_retrieval_decisions = [
            {"memory_id": item.id, "admitted": True, "reason": "token_vector", "score": score}
            for score, item in selected
        ]
        return self._set_read_context([
            _context_from_memory(item, score=score)
            for score, item in selected
        ])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        content = _episode_text(episode)
        if not content.strip():
            return
        memory = self.store.add(new_memory(
            content=f"Trace chunk for {_episode_task_id(episode)}: {_trim(content)}",
            type=MemoryType.EPISODIC,
            write_reason="vector baseline trace chunk",
            provenance=_provenance_for(episode),
            confidence=0.7,
            utility_score=0.4,
            risk_score=0.25,
            retrieval_tags=["vector", _episode_sequence_id(episode)],
            repo_scope=_episode_sequence_id(episode),
            file_scope=_as_list(episode.get("file_paths") or episode.get("files")),
            task_scope=[_episode_sequence_id(episode)],
        ))
        self.last_write_items = [memory]
        self.last_write_count = 1
        self.last_sleep_tokens = _token_count(memory.content)

    def memory_items(self) -> List[MemoryItem]:
        return self.store.get_all(include_inactive=True)


class InstancePolicy(MemoryPolicy):
    """B5: deterministic instance/task-state memory substitute.

    This is an offline instance-memory substitute, not stock Mem0.  It records
    sequence-local task state and retrieves matching instances.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.store = MemoryStore(namespace=self.name)

    def read(self, task: Any) -> List[Dict[str, Any]]:
        items = _store_search(self.store, task, _effective_read_limit(task, self.read_limit))
        self.last_retrieval_decisions = [
            {"memory_id": item.id, "admitted": True, "reason": "instance_state"}
            for item in items
        ]
        return self._set_read_context([_context_from_memory(item) for item in items])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        event = dict(episode.get("injected_memory_event") or {})
        event_scope = dict(event.get("scope") or {}) if isinstance(event.get("scope"), Mapping) else {}
        content = " ".join([
            f"Instance state for {_episode_sequence_id(episode)}.",
            f"Task {_episode_task_id(episode)} outcome {episode.get('outcome', 'unknown')}.",
            str(event.get("content") or _first(episode.get("observations")) or ""),
        ]).strip()
        memory = self.store.add(new_memory(
            content=content,
            type=_memory_type_from_event(event, default=MemoryType.SEMANTIC_PROJECT),
            write_reason="deterministic instance-memory baseline write",
            provenance=_provenance_for(episode),
            confidence=float(event.get("confidence") or 0.65),
            utility_score=0.6,
            risk_score=0.2,
            retrieval_tags=["instance", _episode_sequence_id(episode), str(event.get("event_type", ""))],
            repo_scope=_episode_sequence_id(episode),
            file_scope=_as_list(event_scope.get("files") or episode.get("file_paths") or episode.get("files")),
            symbol_scope=_as_list(event_scope.get("symbols") or episode.get("symbols")),
            task_scope=[_episode_sequence_id(episode)],
        ))
        self.last_write_items = [memory]
        self.last_write_count = 1
        self.last_sleep_tokens = _token_count(memory.content)

    def memory_items(self) -> List[MemoryItem]:
        return self.store.get_all(include_inactive=True)


class Mem0Policy(MemoryPolicy):
    """Live B5-MEM0 baseline backed by mem0ai when available.

    The benchmark imports this policy in offline test environments, so all
    mem0 construction and calls degrade to read-empty/write-noop behavior when
    the package, hosted key, or network are unavailable.
    """

    def __init__(
        self,
        *,
        client: Optional[Any] = None,
        mem0_config: Optional[Mapping[str, Any]] = None,
        cache_root: Optional[Path] = None,
        run_id: Optional[str] = None,
        condition_id: Optional[str] = None,
        seed: Optional[int] = None,
        event_poll_timeout_seconds: float = 120.0,
        event_poll_interval_seconds: float = 0.5,
        event_api_base: str = MEM0_EVENT_API_BASE,
        read_token_budget: int = MEM0_READ_TOKEN_BUDGET,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.cache_root = Path(cache_root) if cache_root is not None else _mem0_fixture_root()
        self.mem0_config = _mem0_default_config(self.name) if mem0_config is None else dict(mem0_config)
        self.client_backend = "injected" if client is not None else "unavailable"
        self.unavailable_reason = ""
        self.run_id = str(run_id or os.environ.get("DREAMBENCH_RUN_ID") or "manual")
        self.condition_id = str(condition_id or self.name)
        self.seed = seed
        self.event_poll_timeout_seconds = float(event_poll_timeout_seconds)
        self.event_poll_interval_seconds = float(event_poll_interval_seconds)
        self.event_api_base = event_api_base.rstrip("/")
        self.read_token_budget = int(read_token_budget)
        self._blocked_namespaces: set[str] = set()
        self.client = client if client is not None else self._construct_client()

    def read(self, task: Any) -> List[Dict[str, Any]]:
        task_data = _task_mapping(task)
        query = _task_text(task_data)
        sequence_id = _sequence_id(task_data)
        namespace = self._namespace(sequence_id)
        limit = _effective_read_limit(task_data, self.read_limit)
        if limit <= 0:
            self.last_retrieval_decisions = []
            return self._set_read_context([])
        if self.client is None or namespace in self._blocked_namespaces or not query.strip():
            self.last_retrieval_decisions = []
            return self._set_read_context([])

        request = {"query": query, "filters": {"user_id": namespace}, "top_k": limit}
        try:
            response = self._search_mem0(query=query, user_id=namespace, limit=limit)
        except Exception as exc:  # noqa: BLE001 - live baseline must remain offline-safe.
            self._cache_exchange(namespace, "read", request, error=exc)
            self.last_retrieval_decisions = []
            return self._set_read_context([])

        self._cache_exchange(namespace, "read", request, response=response)
        results = _rank_mem0_results(_mem0_results(response))[:limit]
        context = self._apply_read_budget([
            _context_from_mem0_result(item, repo_scope=sequence_id, rank=index + 1)
            for index, item in enumerate(results)
        ], limit=limit)
        self.last_retrieval_decisions = [
            {
                "memory_id": item["id"],
                "admitted": True,
                "reason": "mem0_search",
                "score": item.get("score"),
                "repo_scope": sequence_id,
            }
            for item in context
        ]
        return self._set_read_context(context)

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        sequence_id = _episode_sequence_id(episode)
        namespace = self._namespace(sequence_id)
        if self.client is None or namespace in self._blocked_namespaces:
            return

        request: Dict[str, Any] = {"user_id": namespace}
        try:
            content = _mem0_sanitized_episode_text(episode)
            if not content.strip():
                return
            metadata = {
                "benchmark": "dreambench-swe",
                "condition": self.condition_id,
                "seed": self.seed if self.seed is not None else "unknown",
                "sequence_id": sequence_id,
                "run_id": self.run_id,
            }
            messages = [{"role": "user", "content": content}]
            request = {"messages": messages, "user_id": namespace, "metadata": metadata}
            _assert_mem0_payload_clean(content)
            response = self.client.add(messages=messages, user_id=namespace, metadata=metadata)
            if _mem0_event_status(response) == "FAILED":
                self._blocked_namespaces.add(namespace)
                event_id = _mem0_event_id(response)
                detail = f"event {event_id}" if event_id else "add response"
                raise RuntimeError(f"Mem0 add failed for {namespace}: {detail}")
        except Exception as exc:  # noqa: BLE001 - live baseline must remain offline-safe.
            self._cache_exchange(namespace, "write", request, error=exc)
            return

        self._cache_exchange(namespace, "write", request, response=response)
        self.last_write_count = 1
        self.last_sleep_tokens = _token_count(content)

    def _construct_client(self) -> Optional[Any]:
        api_key = os.environ.get("MEM0_API_KEY")
        if not api_key:
            self.unavailable_reason = "hosted missing MEM0_API_KEY"
            return None

        try:
            from mem0 import MemoryClient  # type: ignore

            client = MemoryClient(api_key=api_key)
            self.client_backend = "hosted"
            return client
        except Exception as exc:  # noqa: BLE001 - hosted baseline is optional.
            self.unavailable_reason = f"hosted {type(exc).__name__}: {exc}"
            return None

    def _search_mem0(self, *, query: str, user_id: str, limit: int) -> Any:
        return self.client.search(query=query, filters={"user_id": user_id}, top_k=limit)

    def _wait_for_mem0_add(self, response: Any, *, namespace: str) -> Any:
        event_id = _mem0_event_id(response)
        status = _mem0_event_status(response)
        if not event_id or status in {"", "SUCCEEDED"}:
            return response
        if status == "FAILED":
            raise RuntimeError(f"Mem0 add failed for {namespace}: event {event_id}")

        deadline = time.monotonic() + self.event_poll_timeout_seconds
        latest = response
        while time.monotonic() <= deadline:
            latest = self._get_mem0_event(event_id)
            status = _mem0_event_status(latest)
            if status == "SUCCEEDED":
                return {"add_response": response, "event": latest}
            if status == "FAILED":
                raise RuntimeError(f"Mem0 add failed for {namespace}: event {event_id}")
            if self.event_poll_interval_seconds > 0:
                time.sleep(self.event_poll_interval_seconds)
        raise TimeoutError(f"Mem0 add event {event_id} did not complete for {namespace}")

    def _get_mem0_event(self, event_id: str) -> Any:
        getter = getattr(self.client, "get_event", None)
        if callable(getter):
            try:
                return getter(event_id=event_id)
            except TypeError:
                return getter(event_id)

        api_key = os.environ.get("MEM0_API_KEY")
        if not api_key:
            raise RuntimeError("cannot poll Mem0 event without MEM0_API_KEY")
        request = urllib.request.Request(
            f"{self.event_api_base}/{event_id}/",
            headers={"Authorization": f"Token {api_key}", "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=10.0) as response:  # noqa: S310 - documented Mem0 API endpoint.
            return json.loads(response.read().decode("utf-8"))

    def _namespace(self, sequence_id: str) -> str:
        seed = self.seed if self.seed is not None else "unknown"
        namespace = f"{_mem0_namespace_prefix()}:{self.run_id}:{self.condition_id}:seed-{seed}:{sequence_id}"
        # Mem0 hosted (mem0ai 2.0.11) silently drops memories whose user_id contains a colon:
        # add() returns an event id but the memory is never retrievable via search(filters={user_id}).
        # Colons are the sole failure; hyphens/underscores are fine. Use a colon-free separator so
        # every write actually persists + retrieves. (Verified 2026-07-04 on the hosted API.)
        return namespace.replace(":", "__")

    def _apply_read_budget(self, context: Sequence[Dict[str, Any]], *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        admitted: List[Dict[str, Any]] = []
        tokens_used = 0
        item_limit = self.read_limit if limit is None else int(limit)
        for item in context:
            if len(admitted) >= item_limit:
                break
            content = str(item.get("content") or "")
            remaining = self.read_token_budget - tokens_used
            if remaining <= 0:
                break
            token_count = _token_count(content)
            next_item = dict(item)
            if token_count > remaining:
                next_item["content"] = " ".join(content.split()[:remaining])
                token_count = _token_count(str(next_item["content"]))
            tokens_used += token_count
            admitted.append(next_item)
        return admitted

    def _cache_exchange(
        self,
        namespace: str,
        op: str,
        request: Mapping[str, Any],
        *,
        response: Any = None,
        error: Optional[BaseException] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "op": op,
            "namespace": namespace,
            "backend": self.client_backend,
            "request": dict(request),
        }
        if error is not None:
            payload["error"] = {"type": type(error).__name__, "message": str(error)}
        else:
            payload["response"] = response
        try:
            cache_dir = self.cache_root / _safe_mem0_path_part(namespace)
            cache_dir.mkdir(parents=True, exist_ok=True)
            key = {"op": op, "namespace": namespace, "request": request}
            digest = hashlib.sha256(_mem0_json_dumps(key).encode("utf-8")).hexdigest()[:16]
            cache_path = cache_dir / f"{op}-{digest}.json"
            cache_path.write_text(_mem0_json_dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception:
            return


class Mem0LiteralPolicy(Mem0Policy):
    """B5-MEM0-LIT: Mem0 with LLM fact extraction bypassed for exact-token workloads."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        add_kwargs = self.mem0_config.get("add_kwargs")
        if not isinstance(add_kwargs, Mapping):
            add_kwargs = {}
        self.mem0_config = {
            **self.mem0_config,
            "storage_mode": "literal_raw_text",
            "add_kwargs": {**dict(add_kwargs), "infer": False},
        }

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        sequence_id = _episode_sequence_id(episode)
        namespace = self._namespace(sequence_id)
        if self.client is None or namespace in self._blocked_namespaces:
            return

        request: Dict[str, Any] = {"user_id": namespace, "storage_mode": "literal_raw_text", "infer": False}
        try:
            content = _mem0_sanitized_episode_text(episode)
            if not content.strip():
                return
            metadata = {
                "benchmark": "dreambench-swe",
                "condition": self.condition_id,
                "seed": self.seed if self.seed is not None else "unknown",
                "sequence_id": sequence_id,
                "run_id": self.run_id,
                "mem0_storage_mode": "literal_raw_text",
                "mem0_infer": False,
            }
            messages = [{"role": "user", "content": content}]
            request = {
                "messages": messages,
                "user_id": namespace,
                "metadata": metadata,
                "storage_mode": "literal_raw_text",
                "infer": False,
            }
            _assert_mem0_payload_clean(content)
            response = self._add_literal_mem0(
                content=content,
                messages=messages,
                user_id=namespace,
                metadata=metadata,
            )
            if _mem0_event_status(response) == "FAILED":
                self._blocked_namespaces.add(namespace)
                event_id = _mem0_event_id(response)
                detail = f"event {event_id}" if event_id else "add response"
                raise RuntimeError(f"Mem0 literal add failed for {namespace}: {detail}")
        except Exception as exc:  # noqa: BLE001 - live baseline must remain offline-safe.
            self._cache_exchange(namespace, "write", request, error=exc)
            return

        self._cache_exchange(namespace, "write", request, response=response)
        self.last_write_count = 1
        self.last_sleep_tokens = _token_count(content)

    def _add_literal_mem0(
        self,
        *,
        content: str,
        messages: Sequence[Mapping[str, str]],
        user_id: str,
        metadata: Mapping[str, Any],
    ) -> Any:
        add_memory = getattr(self.client, "add_memory", None)
        if callable(add_memory):
            try:
                return add_memory(memory=content, user_id=user_id, metadata=dict(metadata))
            except TypeError:
                return add_memory(content, user_id=user_id, metadata=dict(metadata))
        add_kwargs = self.mem0_config.get("add_kwargs")
        if not isinstance(add_kwargs, Mapping):
            add_kwargs = {"infer": False}
        return self.client.add(
            messages=list(messages),
            user_id=user_id,
            metadata=dict(metadata),
            **dict(add_kwargs),
        )


class SubtaskPolicy(MemoryPolicy):
    """B6: deterministic subtask-level memory over trajectory actions."""

    def __init__(self, *, subtask_extractor: Optional[SubtaskCallable] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.subtask_extractor = subtask_extractor or default_subtasks
        self.store = MemoryStore(namespace=self.name)

    def read(self, task: Any) -> List[Dict[str, Any]]:
        items = _store_search(self.store, task, _effective_read_limit(task, self.read_limit))
        self.last_retrieval_decisions = [
            {"memory_id": item.id, "admitted": True, "reason": "subtask_search"}
            for item in items
        ]
        return self._set_read_context([_context_from_memory(item) for item in items])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        writes: List[MemoryItem] = []
        for subtask in self.subtask_extractor(episode):
            writes.append(self.store.add(new_memory(
                content=subtask,
                type=MemoryType.PROCEDURAL,
                write_reason="deterministic subtask-memory baseline write",
                provenance=_provenance_for(episode),
                confidence=0.62,
                utility_score=0.62,
                risk_score=0.25,
                retrieval_tags=["subtask", _episode_sequence_id(episode)],
                repo_scope=_episode_sequence_id(episode),
                file_scope=_as_list(episode.get("file_paths") or episode.get("files")),
                task_scope=[_episode_sequence_id(episode)],
            )))
        self.last_write_items = writes
        self.last_write_count = len(writes)
        self.last_sleep_tokens = sum(_token_count(item.content) for item in writes)

    def memory_items(self) -> List[MemoryItem]:
        return self.store.get_all(include_inactive=True)


class TaskTrackerPolicy(MemoryPolicy):
    """B7: lightweight sequence-local task tracker memory.

    This baseline keeps explicit task state only. It does not run typed
    consolidation, contradiction repair, counterfactual replay, or retrieval
    gating; those belong to DreamForge full.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.store = MemoryStore(namespace=self.name)
        self._trackers: Dict[str, Dict[str, List[str]]] = {}
        self._tracker_memory_ids: Dict[str, str] = {}

    def read(self, task: Any) -> List[Dict[str, Any]]:
        sequence_id = _sequence_id(task)
        if _effective_read_limit(task, self.read_limit) <= 0:
            self.last_retrieval_decisions = []
            return self._set_read_context([])
        memory = self._tracker_for_sequence(sequence_id)
        if memory is None:
            self.last_retrieval_decisions = []
            return self._set_read_context([])
        self.last_retrieval_decisions = [
            {"memory_id": memory.id, "admitted": True, "reason": "task_tracker_scope", "repo_scope": sequence_id}
        ]
        return self._set_read_context([_context_from_memory(memory)])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        sequence_id = _episode_sequence_id(episode)
        state = self._trackers.setdefault(sequence_id, _empty_tracker_state())
        updates = _tracker_updates_from_episode(episode)
        for key, values in updates.items():
            state[key] = _merge_unique(state.get(key, []), values)

        content = _render_tracker_content(sequence_id, state)
        provenance = _provenance_for(episode)
        file_scope = _as_list(episode.get("file_paths") or episode.get("files"))
        memory_id = self._tracker_memory_ids.get(sequence_id)

        if memory_id:
            memory = self.store.get(memory_id)
            provenance = _merge_provenance(memory.provenance, provenance)
            file_scope = _merge_unique(memory.file_scope, file_scope)
            memory = self.store.update(
                memory.id,
                content=content,
                provenance=provenance,
                repo_scope=sequence_id,
                file_scope=file_scope,
                task_scope=[sequence_id],
                retrieval_tags=_tracker_tags(sequence_id, state),
                confidence=0.7,
                utility_score=0.65,
                risk_score=0.15,
            )
        else:
            memory = self.store.add(new_memory(
                content=content,
                type=MemoryType.EPISODIC,
                write_reason="deterministic task-tracker baseline write",
                provenance=provenance,
                confidence=0.7,
                utility_score=0.65,
                risk_score=0.15,
                retrieval_tags=_tracker_tags(sequence_id, state),
                repo_scope=sequence_id,
                file_scope=file_scope,
                task_scope=[sequence_id],
            ))
            self._tracker_memory_ids[sequence_id] = memory.id

        self.last_write_items = [memory]
        self.last_write_count = 1
        self.last_sleep_tokens = _token_count(memory.content)

    def memory_items(self) -> List[MemoryItem]:
        return self.store.get_all(include_inactive=True)

    def _tracker_for_sequence(self, sequence_id: str) -> Optional[MemoryItem]:
        memory_id = self._tracker_memory_ids.get(sequence_id)
        if memory_id:
            memory = self.store.get(memory_id)
            if memory.status == MemoryStatus.ACTIVE and memory.repo_scope == sequence_id:
                return memory

        matches = [
            item
            for item in self.store.get_all(include_inactive=False)
            if item.repo_scope == sequence_id and "task_tracker" in item.retrieval_tags
        ]
        return matches[-1] if matches else None


class DreamForgePolicy(MemoryPolicy):
    """DF: DreamForge full pipeline — typed consolidation, repair, replay,
    stale suppression, provenance gating, and retrieval gating.

    Args:
        llm_judge: optional LLMJudge instance.  When provided, its three
            judge callables are used for consolidation, contradiction repair,
            and counterfactual replay instead of the deterministic defaults.
            The real GPT-5.5/GRID client is wired here at run time; tests
            inject a stub via LLMJudge(complete=stub).
        maintenance_scope: 'local' (default) restricts sleep operators to
            memories whose scope intersects the current episode.  'global'
            runs whole-store repair (expensive, useful for ablation).
        extractor, replay_judge, repair_judge: direct callables that override
            llm_judge when explicitly provided (legacy path for unit tests).
    """

    def __init__(
        self,
        *,
        extractor: Optional[Extractor] = None,
        replay_judge: Optional[Callable[[Dict[str, Any]], Sequence[Any]]] = None,
        repair_judge: Optional[Callable[[MemoryItem, MemoryItem], Dict[str, Any]]] = None,
        llm_judge: Optional[Any] = None,
        maintenance_scope: str = "local",
        enable_consolidation: bool = True,
        enable_repair: bool = True,
        enable_replay: bool = True,
        enable_stale_suppression: bool = True,
        enable_retrieval_gate: bool = True,
        forced_consolidation: bool = False,
        enable_raw_evidence: bool = False,
        exclude_contradiction_from_read: bool = False,
        theta_admit: float = 1.0,
        token_budget_read: int = DF_TOKEN_BUDGET_READ,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # llm_judge provides all three callables if no explicit override is given
        if llm_judge is not None:
            self.extractor = extractor or llm_judge.consolidation_judge
            self.replay_judge = replay_judge or llm_judge.replay_judge
            self.repair_judge = repair_judge or llm_judge.contradiction_judge
        else:
            self.extractor = extractor
            self.replay_judge = replay_judge
            self.repair_judge = repair_judge
        self.maintenance_scope = maintenance_scope
        self.enable_consolidation = enable_consolidation
        self.enable_repair = enable_repair
        self.enable_replay = enable_replay
        self.enable_stale_suppression = enable_stale_suppression
        self.enable_retrieval_gate = enable_retrieval_gate
        self.forced_consolidation = forced_consolidation
        self.enable_raw_evidence = enable_raw_evidence
        self.exclude_contradiction_from_read = exclude_contradiction_from_read
        self.token_budget_read = int(token_budget_read)
        self.store = MemoryStore(namespace=self.name)
        self.gate = RetrievalGate(self.store, theta_admit=float(theta_admit))

    def read(self, task: Any) -> List[Dict[str, Any]]:
        if self.enable_stale_suppression:
            self._apply_prompt_staleness(task)
        query = {
            "text": _task_text(task),
            "tags": [_sequence_id(task), _seq_type(task), "consolidation"],
            "repo_scope": _sequence_id(task),
            "files": _task_files(task),
            "phase": "implementation",
            "token_budget_read": self.token_budget_read,
        }
        if self.exclude_contradiction_from_read:
            query["allowed_types"] = [
                memory_type
                for memory_type in MemoryType
                if memory_type is not MemoryType.CONTRADICTION
            ]
        if not self.enable_retrieval_gate:
            items = self._retrieve_without_gate(query, k=self.read_limit)
        elif not self.enable_stale_suppression:
            items = self._retrieve_with_stale_allowed(query, k=self.read_limit)
        else:
            items = self.gate.retrieve(query, k=self.read_limit)
            self.last_retrieval_decisions = list(self.gate.last_decisions)
        return self._set_read_context([_context_from_memory(item) for item in items])

    def write(self, trajectory: Any) -> None:
        self._reset_write_stats()
        episode = _episode_from_any(trajectory)
        if not self.enable_consolidation:
            if self.enable_raw_evidence:
                written = sleep_pipeline(
                    [episode],
                    self.store,
                    extractor=lambda _payload: [],
                    replay_judge=None,
                    repair_judge=None,
                    write=True,
                    maintenance_scope="global" if self.forced_consolidation else self.maintenance_scope,
                    enable_raw_evidence=True,
                )
            else:
                written = [self._write_raw_episode(episode)]
        else:
            sleep_kwargs: Dict[str, Any] = {
                "extractor": self.extractor,
                "write": True,
                "maintenance_scope": "global" if self.forced_consolidation else self.maintenance_scope,
                "enable_raw_evidence": self.enable_raw_evidence,
            }
            if self.enable_replay:
                if self.replay_judge is not None:
                    sleep_kwargs["replay_judge"] = self.replay_judge
            else:
                sleep_kwargs["replay_judge"] = None
            if self.enable_repair:
                if self.repair_judge is not None:
                    sleep_kwargs["repair_judge"] = self.repair_judge
            else:
                sleep_kwargs["repair_judge"] = None
            written = sleep_pipeline([episode], self.store, **sleep_kwargs)
        # Harness scope convention: memory is namespaced by sequence_id, and the
        # read query uses repo_scope=_sequence_id(task). An LLM judge may set
        # repo_scope from its scope.repo (the real repo name, e.g. "configly"),
        # which then fails the retrieval gate's repo_scope hard-check and silently
        # makes DF degrade to B0. Re-impose the convention every other policy uses.
        sequence_id = _episode_sequence_id(episode)
        for item in written:
            item.repo_scope = sequence_id
        self.last_write_items = list(written)
        self.last_write_count = len(written)
        self.last_sleep_tokens = sum(_token_count(item.content) for item in written)

    def memory_items(self) -> List[MemoryItem]:
        return self.store.get_all(include_inactive=True)

    def _apply_prompt_staleness(self, task: Any) -> None:
        prompt = _task_text(task).lower()
        if not any(marker in prompt for marker in ("stale", "superseded", "emergency has passed", "old ")):
            return
        sequence_id = _sequence_id(task)
        for item in self.store.get_all(status=MemoryStatus.ACTIVE):
            if item.repo_scope == sequence_id and item.type != MemoryType.CONTRADICTION:
                item.mark_stale(1.0)
                break

    def _write_raw_episode(self, episode: Dict[str, Any]) -> MemoryItem:
        sequence_id = _episode_sequence_id(episode)
        task_id = _episode_task_id(episode)
        content = _trim(_episode_text(episode), 1200) or f"Raw episode for task {task_id}."
        return self.store.add(new_memory(
            content=f"Raw episode for task {task_id}: {content}",
            type=MemoryType.EPISODIC,
            write_reason="episodic-only ablation raw episode write",
            provenance=_provenance_for(episode),
            confidence=1.0,
            utility_score=0.35,
            risk_score=0.1,
            retrieval_tags=["episodic", "ablation", sequence_id],
            repo_scope=sequence_id,
            file_scope=_as_list(episode.get("file_paths") or episode.get("files")),
            task_scope=[sequence_id],
        ))

    def _retrieve_without_gate(self, query: Dict[str, Any], *, k: int) -> List[MemoryItem]:
        scored: List[Tuple[float, MemoryItem]] = []
        rejected: List[Dict[str, Any]] = []
        for item in self.store.get_all(include_inactive=True):
            score = self.gate.score(item, query)
            if not self._query_allows_type(item, query):
                rejected.append({
                    "memory_id": item.id,
                    "admitted": False,
                    "reason": "type_policy",
                    "score": score,
                })
                continue
            scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].updated_at, pair[1].id))
        self.last_retrieval_decisions = rejected + [
            {
                "memory_id": item.id,
                "admitted": True,
                "reason": "retrieval_gate_disabled",
                "score": score,
            }
            for score, item in scored
        ]
        return self._select_retrieval_results(scored, query, k=k)

    def _retrieve_with_stale_allowed(self, query: Dict[str, Any], *, k: int) -> List[MemoryItem]:
        stale_reasons = {
            "status_stale",
            "status_superseded",
            "active_superseder",
            "stale_block",
            "expired_valid_until",
        }
        decisions: List[Dict[str, Any]] = []
        admitted: List[Tuple[float, MemoryItem]] = []
        for item in self.store.get_all(include_inactive=True):
            score = self.gate.score(item, query)
            if not self._query_allows_type(item, query):
                decisions.append({
                    "memory_id": item.id,
                    "admitted": False,
                    "reason": "type_policy",
                    "score": score,
                })
                continue
            passed, reason = self.gate.hard_gates(item, query)
            stale_allowed = (not passed and reason in stale_reasons)
            if not passed and not stale_allowed:
                decisions.append({
                    "memory_id": item.id,
                    "admitted": False,
                    "reason": reason,
                    "score": score,
                })
                continue
            if score < self.gate.theta_admit:
                decisions.append({
                    "memory_id": item.id,
                    "admitted": False,
                    "reason": "below_admission_threshold",
                    "score": score,
                })
                continue
            admitted.append((score, item))
            decisions.append({
                "memory_id": item.id,
                "admitted": True,
                "reason": f"stale_suppression_disabled:{reason}" if stale_allowed else "admitted",
                "score": score,
            })
        admitted.sort(key=lambda pair: (-pair[0], pair[1].updated_at, pair[1].id))
        self.last_retrieval_decisions = decisions
        return self._select_retrieval_results(admitted, query, k=k)

    @staticmethod
    def _query_allows_type(item: MemoryItem, query: Mapping[str, Any]) -> bool:
        allowed_types = query.get("allowed_types")
        if allowed_types is None:
            return True
        normalized = {
            memory_type if isinstance(memory_type, MemoryType) else MemoryType(memory_type)
            for memory_type in allowed_types
        }
        return item.type in normalized

    @staticmethod
    def _select_retrieval_results(
        scored: Sequence[Tuple[float, MemoryItem]],
        query: Mapping[str, Any],
        *,
        k: int,
    ) -> List[MemoryItem]:
        results: List[MemoryItem] = []
        tokens_used = 0
        budget = query.get("token_budget_read")
        for _, item in scored:
            if len(results) >= k:
                break
            estimated_tokens = max(1, len(item.content.split()))
            if budget is not None and tokens_used + estimated_tokens > int(budget):
                continue
            tokens_used += estimated_tokens
            item.touch()
            results.append(item)
        return results


@dataclass(frozen=True)
class BaselineSpec:
    name: str
    label: str
    policy_cls: Type[MemoryPolicy]
    description: str

    def create(self, **kwargs: Any) -> MemoryPolicy:
        return self.policy_cls(
            name=self.name,
            label=self.label,
            description=self.description,
            **kwargs,
        )


BASELINE_REGISTRY: Dict[str, BaselineSpec] = {
    "B0": BaselineSpec("B0", "NoMemory", NoMemoryPolicy, "No external memory read or write."),
    "B1": BaselineSpec("B1", "RawTrajectory", RawTrajectoryPolicy, "Raw prior trajectory retrieval only."),
    "B2": BaselineSpec("B2", "Vector", VectorPolicy, "Deterministic token-vector trace retrieval."),
    "B3": BaselineSpec("B3", "Reflection", ReflectionPolicy, "Free-form verbal lessons after sessions."),
    "B4": BaselineSpec("B4", "Summary", SummaryPolicy, "Deterministic untyped session summaries."),
    "B5": BaselineSpec("B5", "B5-Instance", InstancePolicy, "Offline instance-memory substitute."),
    "B6": BaselineSpec("B6", "Subtask", SubtaskPolicy, "Subtask-level trajectory action memory."),
    "B7": BaselineSpec("B7", "TaskTracker", TaskTrackerPolicy, "Sequence-local task tracker memory."),
}


def create_policy(condition: str, **kwargs: Any) -> MemoryPolicy:
    name = condition.upper()
    if name not in BASELINE_REGISTRY:
        known = ", ".join(sorted(BASELINE_REGISTRY))
        raise ValueError(f"unknown condition {condition!r}; expected one of {known}")
    return BASELINE_REGISTRY[name].create(**kwargs)


def available_conditions() -> List[str]:
    return sorted(BASELINE_REGISTRY)


def default_summary(episode: Dict[str, Any]) -> str:
    return " ".join([
        f"Summary for {_episode_sequence_id(episode)} task {_episode_task_id(episode)}.",
        f"Outcome: {episode.get('outcome', 'unknown')}.",
        _trim(str(_first(episode.get("observations")) or _first(episode.get("actions")) or "")),
    ]).strip()


def default_reflections(episode: Dict[str, Any]) -> List[str]:
    sequence_id = _episode_sequence_id(episode)
    event = dict(episode.get("injected_memory_event") or {})
    content = str(event.get("content") or _first(episode.get("observations")) or "")
    if episode.get("human_feedback"):
        return [f"Reflection for {sequence_id}: respect scoped human feedback `{_trim(str(episode['human_feedback']))}`."]
    if _episode_failed(episode):
        return [f"Reflection for {sequence_id}: avoid repeating the failed path around `{_trim(content)}`."]
    return [f"Reflection for {sequence_id}: prior successful path observed `{_trim(content)}`."]


def default_embedder(text: str) -> Sequence[str]:
    return sorted(_tokens(text))


def default_subtasks(episode: Dict[str, Any]) -> Sequence[str]:
    actions = _as_list(episode.get("actions"))
    if not actions:
        actions = [_episode_text(episode)]
    return [
        f"Subtask memory for {_episode_sequence_id(episode)}: action `{_trim(action)}` led to outcome {episode.get('outcome', 'unknown')}."
        for action in actions
        if str(action).strip()
    ][:3]


def _empty_tracker_state() -> Dict[str, List[str]]:
    return {
        "completed_tasks": [],
        "completed_subtasks": [],
        "done": [],
        "todo": [],
        "next": [],
        "decisions": [],
        "open_issues": [],
    }


def _tracker_updates_from_episode(episode: Dict[str, Any]) -> Dict[str, List[str]]:
    updates = _empty_tracker_state()
    task_id = _episode_task_id(episode)
    outcome = str(episode.get("outcome") or "").lower()
    if outcome in {"success", "passed"}:
        updates["completed_tasks"].append(task_id)
        updates["completed_subtasks"].extend(_tracker_values(episode.get("actions")))
    elif outcome:
        updates["open_issues"].append(f"{task_id}: outcome {episode.get('outcome')}")

    field_map = {
        "completed_subtasks": ("completed_subtasks", "subtasks_completed", "completed_steps"),
        "done": ("done", "done_items", "completed", "completed_items", "finished"),
        "todo": ("todo", "todos", "todo_items", "remaining", "remaining_work"),
        "next": ("next", "next_steps", "followups", "follow_ups"),
        "decisions": ("decisions", "decision_log"),
        "open_issues": ("open_issues", "issues", "blockers"),
    }
    for target, keys in field_map.items():
        for key in keys:
            updates[target].extend(_tracker_values(episode.get(key)))

    for nested_key in ("task_tracker", "tracker", "tracker_state"):
        nested = episode.get(nested_key)
        if isinstance(nested, Mapping):
            for target, keys in field_map.items():
                for key in (target, *keys):
                    updates[target].extend(_tracker_values(nested.get(key)))

    event = dict(episode.get("injected_memory_event") or {})
    event_content = str(event.get("content") or "").strip()
    event_type = str(event.get("event_type") or "").lower()
    if event_content:
        if event_type in {"todo", "next", "decision", "open_issue", "done"}:
            target = "open_issues" if event_type == "open_issue" else event_type
            updates[target].append(event_content)
        elif event_type == "observation":
            updates["done"].append(event_content)

    labeled = _labeled_tracker_lines(_episode_text(episode))
    for target, values in labeled.items():
        updates[target].extend(values)

    return {key: _merge_unique([], values) for key, values in updates.items()}


def _tracker_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        out: List[str] = []
        for key, item in value.items():
            if isinstance(item, bool):
                if item:
                    out.append(str(key))
            else:
                nested = _tracker_values(item)
                out.extend([f"{key}: {entry}" for entry in nested] or [str(key)])
        return out
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    try:
        return [str(item).strip() for item in value if str(item).strip()]
    except TypeError:
        scalar = str(value).strip()
        return [scalar] if scalar else []


def _labeled_tracker_lines(text: str) -> Dict[str, List[str]]:
    updates = _empty_tracker_state()
    label_map = {
        "todo": "todo",
        "done": "done",
        "next": "next",
        "decision": "decisions",
        "decisions": "decisions",
        "open": "open_issues",
        "open issue": "open_issues",
        "issue": "open_issues",
        "completed subtask": "completed_subtasks",
        "completed subtasks": "completed_subtasks",
        "subtask": "completed_subtasks",
    }
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        checkbox = re.match(r"^[-*]\s+\[(x|X| )\]\s+(.+)$", line)
        if checkbox:
            target = "done" if checkbox.group(1).lower() == "x" else "todo"
            updates[target].append(checkbox.group(2).strip())
            continue
        match = re.match(
            r"^(?:[-*]\s*)?(todo|done|next|decisions?|open issue|open|issue|completed subtasks?|subtask)\s*[:=-]\s*(.+)$",
            line,
            re.IGNORECASE,
        )
        if match:
            target = label_map[match.group(1).lower()]
            updates[target].append(match.group(2).strip())
    return updates


def _render_tracker_content(sequence_id: str, state: Mapping[str, Sequence[str]]) -> str:
    lines = [f"Task tracker for {sequence_id}."]
    labels = [
        ("completed_tasks", "Completed tasks"),
        ("completed_subtasks", "Completed subtasks"),
        ("done", "Done"),
        ("todo", "TODO"),
        ("next", "Next"),
        ("open_issues", "Open issues"),
        ("decisions", "Decisions"),
    ]
    for key, label in labels:
        values = [str(item) for item in state.get(key, []) if str(item).strip()]
        lines.append(f"{label}: {'; '.join(values) if values else 'none'}.")
    return "\n".join(lines)


def _tracker_tags(sequence_id: str, state: Mapping[str, Sequence[str]]) -> List[str]:
    return _merge_unique(
        ["task_tracker", "B7", sequence_id],
        state.get("completed_tasks", []),
        state.get("todo", []),
        state.get("next", []),
    )


def _merge_unique(*groups: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for group in groups:
        for item in group:
            value = str(item).strip()
            if value and value not in seen:
                out.append(value)
                seen.add(value)
    return out


def _merge_provenance(left: Provenance, right: Provenance) -> Provenance:
    return Provenance(
        trajectory_ids=_merge_unique(left.trajectory_ids, right.trajectory_ids),
        task_ids=_merge_unique(left.task_ids, right.task_ids),
        repo_commits=_merge_unique(left.repo_commits, right.repo_commits),
        file_paths=_merge_unique(left.file_paths, right.file_paths),
        command_outputs=_merge_unique(left.command_outputs, right.command_outputs),
        human_feedback_ids=_merge_unique(left.human_feedback_ids, right.human_feedback_ids),
    )


def _store_search(store: MemoryStore, task: Any, limit: int) -> List[MemoryItem]:
    if limit <= 0:
        return []
    sequence_id = _sequence_id(task)
    scored = store.search(
        keyword=_task_text(task),
        scope={"repo_scope": sequence_id, "files": _task_files(task), "task_id": sequence_id},
        include_inactive=False,
        with_scores=True,
    )
    scored_items = [
        (float(score), item)
        for score, item in scored
        if item.repo_scope == sequence_id
    ]
    if scored_items:
        scored_items.sort(key=lambda pair: (-pair[0], _stable_memory_key(pair[1])))
        return [item for _, item in scored_items[:limit]]
    fallback = [
        item
        for item in store.get_all(include_inactive=False)
        if item.repo_scope == sequence_id
    ]
    fallback_scored = [
        (_baseline_memory_score(item, task), item)
        for item in fallback
    ]
    fallback_scored.sort(key=lambda pair: (-pair[0], _stable_memory_key(pair[1])))
    return [item for _, item in fallback_scored[:limit]]


def _effective_read_limit(task: Any, default: int) -> int:
    target = _read_budget_event_target(task)
    if target is None:
        return int(default)
    try:
        return max(0, int(target))
    except (TypeError, ValueError):
        return int(default)


def _read_budget_event_target(task: Any) -> Optional[Any]:
    data = _task_mapping(task)
    candidates: List[Any] = [data.get("read_budget_event_target")]
    for key in ("validation_metadata", "authoring_metadata"):
        metadata = data.get(key)
        if isinstance(metadata, Mapping):
            candidates.append(metadata.get("read_budget_event_target"))
    sequence = data.get("_sequence_record")
    if isinstance(sequence, Mapping):
        for key in ("validation_metadata", "authoring_metadata"):
            metadata = sequence.get(key)
            if isinstance(metadata, Mapping):
                candidates.append(metadata.get("read_budget_event_target"))
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _baseline_memory_score(item: MemoryItem, task: Any) -> float:
    query_tokens = _tokens(_task_text(task))
    content_tokens = _tokens(" ".join([item.content, " ".join(item.retrieval_tags)]))
    score = _jaccard(query_tokens, content_tokens)
    sequence_id = _sequence_id(task)
    if sequence_id and item.repo_scope == sequence_id:
        score += 1.0
    task_files = set(_task_files(task))
    if task_files and item.file_scope:
        score += len(task_files & set(item.file_scope)) / float(len(task_files))
    return score


def _stable_memory_key(item: MemoryItem) -> Tuple[Any, ...]:
    event_key = _first_event_key(item.retrieval_tags)
    provenance = item.provenance
    payload = {
        "content_hash": _content_hash(item.content),
        "event_key": event_key,
        "file_scope": sorted(str(value) for value in item.file_scope),
        "provenance": {
            "command_outputs": sorted(str(value) for value in provenance.command_outputs),
            "file_paths": sorted(str(value) for value in provenance.file_paths),
            "human_feedback_ids": sorted(str(value) for value in provenance.human_feedback_ids),
            "repo_commits": sorted(str(value) for value in provenance.repo_commits),
            "task_ids": sorted(str(value) for value in provenance.task_ids),
            "trajectory_ids": sorted(str(value) for value in provenance.trajectory_ids),
        },
        "repo_scope": str(item.repo_scope or ""),
        "retrieval_tags": sorted(str(value) for value in item.retrieval_tags),
        "task_scope": sorted(str(value) for value in item.task_scope),
        "type": item.type.value,
    }
    return (event_key, json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _stable_episode_key(episode: Mapping[str, Any]) -> Tuple[Any, ...]:
    event = episode.get("injected_memory_event")
    event_id = ""
    if isinstance(event, Mapping):
        event_id = str(event.get("event_id") or event.get("id") or "")
    return (
        event_id,
        str(episode.get("trajectory_id") or ""),
        str(episode.get("session_id") or ""),
        str(episode.get("benchmark_task_id") or episode.get("task_id") or ""),
        _content_hash(_episode_text(dict(episode))),
    )


def _first_event_key(values: Iterable[str]) -> str:
    for raw_value in values:
        value = str(raw_value)
        match = re.match(r"^(?:event|event_id)[:=](.+)$", value)
        if match:
            return match.group(1)
    return ""


def _content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _rank_mem0_results(results: Sequence[Any]) -> List[Any]:
    def sort_key(indexed: Tuple[int, Any]) -> Tuple[Any, ...]:
        _, result = indexed
        data = dict(result) if isinstance(result, Mapping) else {"memory": str(result)}
        raw_score = data.get("score", data.get("similarity", data.get("_score")))
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0
        content = str(data.get("memory") or data.get("content") or data.get("text") or "")
        stable_id = str(data.get("id") or data.get("memory_id") or "")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
        event_id = str(metadata.get("event_id") or metadata.get("id") or "")
        payload_hash = _content_hash(_mem0_json_dumps(data))
        return (-score, event_id, _content_hash(content), stable_id, payload_hash)

    return [result for _, result in sorted(enumerate(results), key=sort_key)]


def _context_from_memory(memory: MemoryItem, *, score: Optional[float] = None) -> Dict[str, Any]:
    payload = {
        "id": memory.id,
        "kind": "memory",
        "content": memory.content,
        "type": memory.type.value,
        "status": memory.status.value,
        "confidence": memory.confidence,
        "utility_score": memory.utility_score,
        "risk_score": memory.risk_score,
        "staleness_score": memory.staleness_score,
        "retrieval_tags": list(memory.retrieval_tags),
        "repo_scope": memory.repo_scope,
        "file_scope": list(memory.file_scope),
        "task_scope": list(memory.task_scope),
        "superseded_by": list(memory.superseded_by),
        "provenance": memory.provenance.__dict__.copy(),
    }
    if score is not None:
        payload["score"] = float(score)
    return payload


def _context_from_episode(episode: Dict[str, Any], *, rank: int, score: float) -> Dict[str, Any]:
    return {
        "id": str(episode.get("trajectory_id") or episode.get("session_id") or f"raw-{rank}"),
        "kind": "raw_trajectory",
        "content": _trim(_episode_text(episode), 800),
        "type": "episodic",
        "status": "active",
        "confidence": 1.0,
        "utility_score": 0.35,
        "risk_score": 0.1,
        "staleness_score": 0.0,
        "repo_scope": _episode_sequence_id(episode),
        "file_scope": _as_list(episode.get("file_paths") or episode.get("files")),
        "task_scope": [_episode_sequence_id(episode)],
        "rank": rank,
        "score": float(score),
    }


def _context_from_mem0_result(result: Any, *, repo_scope: str, rank: int) -> Dict[str, Any]:
    data = dict(result) if isinstance(result, Mapping) else {"memory": str(result)}
    metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
    score = data.get("score", data.get("similarity", data.get("_score")))
    memory_id = str(data.get("id") or data.get("memory_id") or f"mem0-{rank}")
    content = str(data.get("memory") or data.get("content") or data.get("text") or "")
    payload = {
        "id": memory_id,
        "kind": "memory",
        "content": content,
        "type": str(data.get("type") or "episodic"),
        "status": str(data.get("status") or "active"),
        "confidence": _float_or(data.get("confidence"), 1.0),
        "utility_score": _float_or(data.get("utility_score"), 0.5),
        "risk_score": _float_or(data.get("risk_score"), 0.2),
        "staleness_score": _float_or(data.get("staleness_score"), 0.0),
        "retrieval_tags": _merge_unique(["mem0", repo_scope], _as_list(metadata.get("tags"))),
        "repo_scope": repo_scope,
        "file_scope": _as_list(metadata.get("file_scope") or metadata.get("files")),
        "task_scope": [repo_scope],
        "superseded_by": [],
        "provenance": {
            "trajectory_ids": _as_list(metadata.get("trajectory_id") or metadata.get("trajectory_ids")),
            "task_ids": _as_list(metadata.get("task_id") or metadata.get("task_ids")),
            "repo_commits": _as_list(metadata.get("repo_commit") or metadata.get("repo_commits")),
            "file_paths": _as_list(metadata.get("file_paths") or metadata.get("files")),
            "command_outputs": [],
            "human_feedback_ids": [],
        },
        "rank": rank,
    }
    if score is not None:
        try:
            payload["score"] = float(score)
        except (TypeError, ValueError):
            payload["score"] = score
    return payload


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mem0_results(response: Any) -> List[Any]:
    if response is None:
        return []
    if isinstance(response, Mapping):
        results = response.get("results")
        if isinstance(results, list):
            return results
        memories = response.get("memories")
        if isinstance(memories, list):
            return memories
        if any(key in response for key in ("memory", "content", "text")):
            return [response]
        return []
    if isinstance(response, list):
        return response
    return [response]


def _mem0_event_id(response: Any) -> str:
    if isinstance(response, Mapping):
        return str(response.get("event_id") or response.get("id") or "")
    return str(getattr(response, "event_id", "") or getattr(response, "id", "") or "")


def _mem0_event_status(response: Any) -> str:
    if isinstance(response, Mapping):
        return str(response.get("status") or "").upper()
    return str(getattr(response, "status", "") or "").upper()


def _mem0_sanitized_episode_text(episode: Mapping[str, Any]) -> str:
    episode_data = dict(episode)
    sequence_id = _mem0_clean_text(_episode_sequence_id(episode_data))
    task_id = _mem0_clean_text(_episode_task_id(episode_data))
    lines: List[str] = []

    event_lines = _mem0_sanitized_event_lines(episode)
    if event_lines:
        lines.extend(event_lines)

    prompt = _mem0_clean_text(str(episode.get("prompt") or episode.get("instruction") or ""))
    if prompt:
        lines.append(f"For sequence {sequence_id}, task {task_id}, the public task prompt was: {prompt}")

    actions = [_mem0_clean_text(item) for item in _as_list(episode.get("actions"))]
    actions = [item for item in actions if item]
    if actions:
        lines.append(f"The public agent actions were: {'; '.join(actions[:12])}.")

    for key, label in (("agent_patch", "agent_patch"), ("patch", "agent_patch"), ("production_diff", "production_diff")):
        diff = _mem0_clean_text(str(episode.get(key) or ""))
        if diff:
            readable_label = label.replace("_", " ")
            lines.append(f"The public {readable_label} was: {_trim(diff, 4000)}")

    outcome = str(episode.get("outcome") or episode.get("terminal_outcome") or "").lower()
    if outcome:
        passed = outcome in {"success", "passed", "true"}
        outcome_text = "passed" if passed else "failed"
        lines.append(f"The task outcome was {outcome_text}.")
    error_type = _mem0_clean_text(str(episode.get("error_type") or ""))
    if error_type:
        lines.append(f"The public error type was {error_type}.")

    if not lines:
        lines.append(f"DreamBench-SWE public episode memory for sequence {sequence_id}, task {task_id}.")

    content = "\n".join(line for line in lines if str(line).strip())
    _assert_mem0_payload_clean(content)
    return content


def _mem0_sanitized_event_lines(episode: Mapping[str, Any]) -> List[str]:
    events: List[Any] = []
    raw_events = episode.get("injected_memory_events")
    if isinstance(raw_events, list):
        events.extend(raw_events)
    raw_event = episode.get("injected_memory_event")
    if raw_event:
        events.append(raw_event)

    lines: List[str] = []
    seen: set[Tuple[str, str]] = set()
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = _mem0_clean_text(str(event.get("event_type") or event.get("kind") or "event"))
        content = _mem0_clean_text(str(event.get("content") or ""))
        if not content:
            payload = event.get("payload")
            if isinstance(payload, Mapping):
                content = _mem0_clean_text(" ".join(str(value) for value in payload.values()))
            elif payload is not None:
                content = _mem0_clean_text(str(payload))
        if not content:
            continue
        key = (event_type, content)
        if key in seen:
            continue
        seen.add(key)
        lines.append(content)
    return lines


def _mem0_clean_text(value: Any) -> str:
    text = str(value or "")
    replacements = (
        (r"experiments/env/(?:oracles|refsol|sequences)[^\s'\"`)]*", "[redacted-hidden-path]"),
        (r"/(?:private/)?tmp/[^\s'\"`)]*", "[redacted-temp-path]"),
        (r"/private/var/folders/[^\s'\"`)]*", "[redacted-temp-path]"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.strip()


def _assert_mem0_payload_clean(content: str) -> None:
    lowered = str(content or "").lower()
    leaked = [marker for marker in MEM0_FORBIDDEN_PAYLOAD_MARKERS if marker.lower() in lowered]
    if leaked:
        raise ValueError(f"Mem0 payload failed sanitizer guard: {', '.join(leaked)}")


def _mem0_default_config(namespace: str) -> Dict[str, Any]:
    safe_namespace = _safe_mem0_path_part(namespace)
    vector_path = Path(os.environ.get(
        "MEM0_OSS_VECTOR_PATH",
        str(Path(os.environ.get("TMPDIR", "/tmp")) / "dreambench-mem0" / safe_namespace / "chroma"),
    ))
    ollama_base_url = os.environ.get("MEM0_OLLAMA_BASE_URL", "http://localhost:11434")
    return {
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": f"dreambench_{safe_namespace}",
                "path": str(vector_path),
            },
        },
        "llm": {
            "provider": "ollama",
            "config": {
                "model": os.environ.get("MEM0_OSS_LLM_MODEL", "llama3.1:8b"),
                "temperature": 0,
                "max_tokens": 2000,
                "ollama_base_url": ollama_base_url,
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": os.environ.get("MEM0_OSS_EMBEDDER_MODEL", "nomic-embed-text"),
                "ollama_base_url": ollama_base_url,
            },
        },
    }


def _safe_mem0_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "unknown-sequence")).strip(".-")
    return safe or "unknown-sequence"


def _mem0_json_dumps(value: Any, *, indent: Optional[int] = None) -> str:
    return json.dumps(_mem0_jsonable(value), sort_keys=True, indent=indent, allow_nan=False)


def _mem0_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mem0_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_mem0_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _task_mapping(task: Any) -> Dict[str, Any]:
    if isinstance(task, Mapping):
        data = dict(task)
    elif hasattr(task, "to_dict"):
        data = dict(task.to_dict())
    else:
        data = {
            key: getattr(task, key)
            for key in ("id", "sequence_id", "seq_type", "session_index", "prompt", "files")
            if hasattr(task, key)
        }
    if "seq_type" in data:
        data["seq_type"] = getattr(data["seq_type"], "value", data["seq_type"])
    return data


def _task_text(task: Any) -> str:
    data = _task_mapping(task)
    return str(data.get("prompt") or data.get("text") or "")


def _sequence_id(task: Any) -> str:
    data = _task_mapping(task)
    if data.get("sequence_id"):
        return str(data["sequence_id"])
    task_id = str(data.get("id", "unknown-sequence"))
    return re.sub(r"-s\d+$", "", task_id)


def _seq_type(task: Any) -> str:
    data = _task_mapping(task)
    return str(data.get("seq_type") or "")


def _task_files(task: Any) -> List[str]:
    data = _task_mapping(task)
    files = _as_list(data.get("files") or data.get("file_scope"))
    if files:
        return files
    sequence_id = _sequence_id(data)
    return [f"experiments/data/tasks/{sequence_id}.json"]


def _trajectory_from_any(value: Any) -> Trajectory:
    if isinstance(value, Trajectory):
        return value
    if isinstance(value, Mapping):
        return Trajectory(**dict(value))
    raise TypeError(f"unsupported trajectory type: {type(value)!r}")


def _episode_from_any(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        if "raw_episode" in value:
            return dict(value.get("raw_episode") or {})
        if isinstance(value.get("episode"), Mapping):
            return dict(value.get("episode") or {})
        return dict(value)
    if hasattr(value, "raw_episode_view"):
        return dict(value.raw_episode_view())
    if hasattr(value, "to_dict"):
        data = dict(value.to_dict())
        if isinstance(data.get("episode"), Mapping):
            return dict(data.get("episode") or {})
        return dict(data.get("raw_episode") or data)
    raise TypeError(f"unsupported episode source: {type(value)!r}")


def _episode_sequence_id(episode: Dict[str, Any]) -> str:
    if episode.get("repo_scope"):
        return str(episode["repo_scope"])
    if episode.get("sequence_id"):
        return str(episode["sequence_id"])
    if episode.get("task_id"):
        return re.sub(r"-s\d+$", "", str(episode["task_id"]))
    return "unknown-sequence"


def _episode_task_id(episode: Dict[str, Any]) -> str:
    return str(episode.get("benchmark_task_id") or episode.get("task_id") or "unknown-task")


def _episode_text(episode: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("prompt", "outcome", "successful_recovery", "repo_fact", "human_feedback"):
        if episode.get(key):
            parts.append(str(episode[key]))
    for key in ("actions", "observations", "known_constraints", "failure_observations"):
        parts.extend(_as_list(episode.get(key)))
    event = dict(episode.get("injected_memory_event") or {})
    if event.get("content"):
        parts.append(str(event["content"]))
    return "\n".join(item for item in parts if item)


def _provenance_for(episode: Dict[str, Any]) -> Provenance:
    return Provenance(
        trajectory_ids=_as_list(episode.get("trajectory_id")),
        task_ids=[_episode_task_id(episode)],
        repo_commits=_as_list(episode.get("repo_commit")),
        file_paths=_as_list(episode.get("file_paths") or episode.get("files")),
        command_outputs=[_trim(item, 180) for item in _as_list(episode.get("observations")) if item][:5],
        human_feedback_ids=_as_list(episode.get("human_feedback_id") or episode.get("human_feedback_ids")),
    )


def _memory_type_from_event(event: Mapping[str, Any], *, default: MemoryType) -> MemoryType:
    value = str(event.get("memory_type") or "").strip()
    try:
        return MemoryType(value) if value else default
    except ValueError:
        return default


def _episode_failed(episode: Dict[str, Any]) -> bool:
    return str(episode.get("outcome") or "").lower() not in {"success", "passed"}


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./-]+", str(text or "").lower()))


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / float(len(left_set | right_set))


def _token_count(text: str) -> int:
    return len(str(text or "").split())


def _trim(text: str, limit: int = 220) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _first(value: Any) -> str:
    items = _as_list(value)
    return items[0] if items else ""


__all__ = [
    "BASELINE_REGISTRY",
    "BaselineSpec",
    "DreamForgePolicy",
    "InstancePolicy",
    "Mem0LiteralPolicy",
    "Mem0Policy",
    "MemoryPolicy",
    "NoMemoryPolicy",
    "RawTrajectoryPolicy",
    "ReflectionPolicy",
    "SubtaskPolicy",
    "SummaryPolicy",
    "TaskTrackerPolicy",
    "VectorPolicy",
    "available_conditions",
    "create_policy",
    "default_embedder",
    "default_reflections",
    "default_subtasks",
    "default_summary",
]
