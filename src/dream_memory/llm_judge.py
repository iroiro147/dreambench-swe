"""Injectable LLM judge for DreamForge sleep operators.

An LLMJudge wraps an injected ``complete(prompt: str) -> str`` callable
(stub in tests, real GPT-5.5/GRID client at run time) and exposes three
judge callables that are drop-in replacements for the deterministic defaults:

  consolidation_judge  — matches Extractor signature, used by Consolidator
  contradiction_judge  — matches ContradictionRepair.Judge signature
  replay_judge         — matches CounterfactualReplay.Judge signature

On unparseable LLM output, every method raises ValueError.  There is NO
silent regex fallback when a judge IS injected.  Contradiction rationale text
is treated as audit metadata, so missing/misspelled rationale keys do not
invalidate an otherwise usable decision.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from dream_memory.memory_store import memory_from_dict
from dream_memory.schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryType,
    Provenance,
    new_memory,
)


_CONSOLIDATION_TAG = "CONSOLIDATION_JUDGE_REQUEST"
_CONTRADICTION_TAG = "CONTRADICTION_JUDGE_REQUEST"
_REPLAY_TAG = "REPLAY_JUDGE_REQUEST"

_CONSOLIDATION_SCHEMA = """
Return a JSON array.  Each element represents ONE extracted memory and MUST
have these fields:
  memory_type      : one of episodic|semantic_project|procedural|failure|
                     human_feedback|constraint|contradiction|dream_artifact
  content          : non-empty string with the fact/lesson/event
  scope            : {repo, files, symbols, session_validity}
  provenance       : {trajectory_id, event_id, quote}
  confidence       : float 0-1
  risk_score       : float 0-1
  staleness_score  : float 0-1
  retrieval_tags   : list[str]
Return [] if nothing extractable.  Output ONLY the JSON array, no prose.
""".strip()

_CONTRADICTION_SCHEMA = """
Return a JSON object with exactly these fields:
  relation   : one of contradicts|supersedes|narrows_scope|no_conflict
  old_status : one of active|stale|superseded|requires_review
  new_status : one of active|requires_review
  scope_delta: string describing any scope change (empty string if none)
  rationale  : one-sentence reason for the decision
Output ONLY the JSON object, no prose.
""".strip()

_REPLAY_SCHEMA = """
Return a JSON object with exactly these fields:
  bad_action               : the specific observed or injected bad action
  evidence                 : trajectory evidence that proves it was wrong
  correct_alternative      : the concrete correct alternative
  future_retrieval_condition: condition under which this memory should fire
  scope                    : {repo, files, symbols}
Output ONLY the JSON object, no prose.  Return {} to skip replay for this
episode.
""".strip()


def _parse_json(raw: str, tag: str) -> Any:
    text = raw.strip()
    # GRID/LLM models (glm, kimi, gpt) commonly wrap JSON in markdown code
    # fences (```json ... ```) and sometimes add prose. Strip fences, then fall
    # back to extracting the outermost array/object by bracket span.
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop the opening ``` / ```json line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        i, j = text.find(open_ch), text.rfind(close_ch)
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(
        f"LLMJudge ({tag}): unparseable JSON from complete():\n{raw!r}"
    )


def _require_str(d: Dict[str, Any], key: str, tag: str) -> str:
    if key not in d or not str(d[key]).strip():
        raise ValueError(f"LLMJudge ({tag}): required field {key!r} missing or empty in {d!r}")
    return str(d[key])


def _optional_rationale(d: Dict[str, Any]) -> str:
    value = d.get("rationale")
    if value is not None and str(value).strip():
        return str(value).strip()
    for key, near_value in d.items():
        if key == "rationale":
            continue
        if str(key).lower().startswith("ration") and str(near_value).strip():
            return str(near_value).strip()
    return ""


def _require_float(d: Dict[str, Any], key: str, tag: str) -> float:
    if key not in d:
        raise ValueError(f"LLMJudge ({tag}): required field {key!r} missing in {d!r}")
    try:
        v = float(d[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"LLMJudge ({tag}): field {key!r} must be float, got {d[key]!r}") from exc
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"LLMJudge ({tag}): field {key!r} must be in [0,1], got {v}")
    return v


def _episode_sequence_scope(episode: Dict[str, Any]) -> Optional[str]:
    """Return the benchmark sequence namespace for repo_scope.

    LLM outputs use ``scope.repo`` for the real repository name, but the
    DreamBench harness uses a sequence id as the memory namespace. Repair and
    retrieval compare against that namespace, so memory creation must not copy
    the LLM's repo string into ``repo_scope``.
    """
    for key in ("repo_scope", "sequence_id"):
        value = episode.get(key)
        if value:
            return str(value)
    task_id = episode.get("task_id")
    if task_id:
        return re.sub(r"-s\d+$", "", str(task_id))
    return None


def _memory_from_llm_dict(raw: Dict[str, Any], episode: Dict[str, Any]) -> MemoryItem:
    tag = _CONSOLIDATION_TAG
    memory_type_str = _require_str(raw, "memory_type", tag)
    try:
        memory_type = MemoryType(memory_type_str)
    except ValueError as exc:
        raise ValueError(f"LLMJudge ({tag}): invalid memory_type {memory_type_str!r}") from exc

    content = _require_str(raw, "content", tag)
    confidence = _require_float(raw, "confidence", tag)
    risk_score = _require_float(raw, "risk_score", tag)
    staleness_score = _require_float(raw, "staleness_score", tag)
    retrieval_tags = list(raw.get("retrieval_tags") or [])

    scope = dict(raw.get("scope") or {})
    repo_scope = _episode_sequence_scope(episode)
    files = list(scope.get("files") or episode.get("file_paths") or episode.get("files") or [])
    symbols = list(scope.get("symbols") or [])

    prov_raw = dict(raw.get("provenance") or {})
    trajectory_id = str(prov_raw.get("trajectory_id") or episode.get("trajectory_id") or "")
    event_id = str(prov_raw.get("event_id") or episode.get("event_id") or "")
    provenance = Provenance(
        trajectory_ids=[trajectory_id] if trajectory_id else [],
        task_ids=[str(episode.get("task_id") or "")],
        file_paths=files,
        human_feedback_ids=[event_id] if event_id else [],
    )
    write_reason = f"llm_judge consolidation extraction (type={memory_type_str})"
    return new_memory(
        content=content,
        type=memory_type,
        write_reason=write_reason,
        provenance=provenance,
        confidence=confidence,
        risk_score=risk_score,
        staleness_score=staleness_score,
        retrieval_tags=retrieval_tags,
        repo_scope=repo_scope,
        file_scope=files,
        symbol_scope=symbols,
    )


def _replay_memories_from_llm_dict(
    raw: Dict[str, Any], episode: Dict[str, Any]
) -> List[MemoryItem]:
    if not raw:
        return []

    bad_action = str(raw.get("bad_action") or "")
    evidence = str(raw.get("evidence") or "")
    correct_alternative = str(raw.get("correct_alternative") or "")
    future_cond = str(raw.get("future_retrieval_condition") or "")
    scope = dict(raw.get("scope") or {})

    if not bad_action and not evidence and not correct_alternative:
        return []

    trajectory_id = str(episode.get("trajectory_id") or "unknown")
    task_id = str(episode.get("task_id") or "unknown")
    repo_scope = _episode_sequence_scope(episode)
    files = list(scope.get("files") or episode.get("file_paths") or episode.get("files") or [])
    symbols = list(scope.get("symbols") or [])

    provenance = Provenance(
        trajectory_ids=[trajectory_id],
        task_ids=[task_id],
        file_paths=files,
    )
    tags = ["counterfactual-replay", "llm-judge", task_id]

    content_parts = []
    if bad_action:
        content_parts.append(f"Bad action identified: {bad_action}.")
    if evidence:
        content_parts.append(f"Evidence: {evidence}.")
    if correct_alternative:
        content_parts.append(f"Correct alternative: {correct_alternative}.")
    if future_cond:
        content_parts.append(f"Retrieve when: {future_cond}.")
    content = " ".join(content_parts) or f"Replay for trajectory {trajectory_id}."

    memories: List[MemoryItem] = [
        new_memory(
            content=content,
            type=MemoryType.DREAM_ARTIFACT,
            write_reason="llm_judge counterfactual replay with concrete bad_action and alternative",
            provenance=provenance,
            status=MemoryStatus.ACTIVE,
            confidence=0.7,
            utility_score=0.75,
            risk_score=0.25,
            retrieval_tags=tags,
            repo_scope=repo_scope,
            file_scope=files,
            symbol_scope=symbols,
        )
    ]
    if bad_action and evidence:
        memories.append(new_memory(
            content=f"Failure lesson (trajectory {trajectory_id}): avoid `{bad_action}`. Evidence: {evidence}.",
            type=MemoryType.FAILURE,
            write_reason="llm_judge replay extracted specific bad action and evidence",
            provenance=provenance,
            confidence=0.72,
            utility_score=0.78,
            risk_score=0.22,
            retrieval_tags=tags + ["failure"],
            repo_scope=repo_scope,
            file_scope=files,
            symbol_scope=symbols,
        ))
    return memories


class LLMJudge:
    """Injectable LLM judge for DreamForge sleep operators.

    Args:
        complete: ``(prompt: str) -> str`` callable.  The real implementation
                  calls GPT-5.5 or a GRID model.  Tests inject a stub.
    """

    def __init__(self, complete: Callable[[str], str]) -> None:
        self._complete = complete

    # ------------------------------------------------------------------
    # Consolidation extractor
    # Signature: Extractor = Callable[[Dict[str, Any]], Sequence[Union[MemoryItem, Dict]]]
    # ------------------------------------------------------------------

    def consolidation_judge(
        self, payload: Dict[str, Any]
    ) -> List[Union[MemoryItem, Dict[str, Any]]]:
        """Extract typed memories from raw episodes via the injected LLM.

        Raises ValueError on unparseable LLM output.
        """
        episodes = list(payload.get("raw_episodes") or [])
        if not episodes:
            return []

        existing_summary = ""
        existing_items = payload.get("existing_memories") or []
        if existing_items:
            existing_summary = f"\nExisting memory count: {len(existing_items)}."

        results: List[MemoryItem] = []
        for episode in episodes:
            prompt = (
                f"{_CONSOLIDATION_TAG}\n"
                f"{_CONSOLIDATION_SCHEMA}\n\n"
                f"RAW EPISODE:\n{json.dumps(episode, default=str)}"
                f"{existing_summary}"
            )
            raw_text = self._complete(prompt)
            parsed = _parse_json(raw_text, _CONSOLIDATION_TAG)
            if not isinstance(parsed, list):
                raise ValueError(
                    f"LLMJudge ({_CONSOLIDATION_TAG}): expected JSON array, got {type(parsed).__name__}"
                )
            for item in parsed:
                if not isinstance(item, dict):
                    raise ValueError(
                        f"LLMJudge ({_CONSOLIDATION_TAG}): expected dict items, got {type(item).__name__}"
                    )
                results.append(_memory_from_llm_dict(item, episode))
        return results

    # ------------------------------------------------------------------
    # Contradiction judge
    # Signature: Judge = Callable[[MemoryItem, MemoryItem], Dict[str, Any]]
    # Returns dict compatible with ContradictionRepair.apply() expectations:
    #   contradicts, newer_supersedes, requires_review, reason
    # Also stores the raw LLM fields for scorer audit.
    # ------------------------------------------------------------------

    def contradiction_judge(
        self, existing: MemoryItem, candidate: MemoryItem
    ) -> Dict[str, Any]:
        """Decide the relation between existing and candidate memories via LLM.

        Returns a dict with keys:
          contradicts       (bool)
          newer_supersedes  (bool)
          requires_review   (bool)
          reason            (str — the LLM's rationale)
          relation          (str — raw LLM field for scorer audit)
          old_status        (str — raw LLM field)
          new_status        (str — raw LLM field)
          scope_delta       (str — raw LLM field)
          rationale         (str — raw LLM field)

        Raises ValueError on unparseable LLM output.
        """
        prompt = (
            f"{_CONTRADICTION_TAG}\n"
            f"{_CONTRADICTION_SCHEMA}\n\n"
            f"EXISTING MEMORY:\n{json.dumps(existing.to_dict(), default=str)}\n\n"
            f"CANDIDATE MEMORY:\n{json.dumps(candidate.to_dict(), default=str)}"
        )
        raw_text = self._complete(prompt)
        parsed = _parse_json(raw_text, _CONTRADICTION_TAG)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"LLMJudge ({_CONTRADICTION_TAG}): expected JSON object, got {type(parsed).__name__}"
            )

        relation = _require_str(parsed, "relation", _CONTRADICTION_TAG).strip()
        valid_relations = {"contradicts", "supersedes", "narrows_scope", "no_conflict"}
        if relation not in valid_relations:
            raise ValueError(
                f"LLMJudge ({_CONTRADICTION_TAG}): relation must be one of {valid_relations}, got {relation!r}"
            )

        old_status = str(parsed.get("old_status") or "").strip()
        new_status = str(parsed.get("new_status") or "").strip()
        scope_delta = str(parsed.get("scope_delta") or "")
        rationale = _optional_rationale(parsed)

        contradicts = relation != "no_conflict"
        newer_supersedes = relation in {"supersedes", "narrows_scope"}
        requires_review = not newer_supersedes and contradicts

        return {
            "contradicts": contradicts,
            "newer_supersedes": newer_supersedes,
            "requires_review": requires_review,
            "reason": rationale,
            # raw LLM fields preserved for scorer audit
            "relation": relation,
            "old_status": old_status,
            "new_status": new_status,
            "scope_delta": scope_delta,
            "rationale": rationale,
        }

    # ------------------------------------------------------------------
    # Replay judge
    # Signature: Judge = Callable[[Dict[str, Any]], Sequence[Union[MemoryItem, Dict]]]
    # ------------------------------------------------------------------

    def replay_judge(
        self, payload: Dict[str, Any]
    ) -> List[Union[MemoryItem, Dict[str, Any]]]:
        """Produce concrete counterfactual replay memories via the injected LLM.

        Raises ValueError on unparseable LLM output.
        """
        episode = dict(payload.get("episode") or {})
        prompt = (
            f"{_REPLAY_TAG}\n"
            f"{_REPLAY_SCHEMA}\n\n"
            f"EPISODE:\n{json.dumps(episode, default=str)}"
        )
        raw_text = self._complete(prompt)
        parsed = _parse_json(raw_text, _REPLAY_TAG)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"LLMJudge ({_REPLAY_TAG}): expected JSON object, got {type(parsed).__name__}"
            )
        return _replay_memories_from_llm_dict(parsed, episode)


__all__ = ["LLMJudge"]
