"""Headless Codex CLI client for DreamForge sleep judges."""
from __future__ import annotations

import json
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agents.llm_agents import (
    ContainerIsolationUnavailable,
    _codex_container_invocation,
    _run_reaped_subprocess,
)


class CodexJudgeClient:
    """Callable ``complete(prompt) -> str`` wrapper for LLMJudge."""

    def __init__(
        self,
        *,
        model: str = "gpt-5.5",
        timeout_seconds: float = 240.0,
        max_retries: int = 4,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.calls = 0
        self.calls_by_tag = {"consolidation": 0, "contradiction": 0, "replay": 0}
        self.total_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.last_usage: Dict[str, int] = {}
        self.total_latency_seconds = 0.0
        self.last_isolation_mode: Optional[str] = None
        self.isolation_mode_counts: Dict[str, int] = {"container": 0}

    def __call__(self, prompt: str) -> str:
        attempts = self.max_retries + 1
        last_error = "unknown error"
        for attempt in range(attempts):
            try:
                start = time.perf_counter()
                text, usage = self._run_codex(prompt)
                self.total_latency_seconds += time.perf_counter() - start
                _validate_jsonish(text)
                self.calls += 1
                tag = _tag(prompt)
                self.calls_by_tag[tag] = int(self.calls_by_tag.get(tag, 0)) + 1
                if int(usage.get("total_tokens") or 0) <= 0:
                    usage = {
                        "prompt_tokens": _token_count(prompt),
                        "completion_tokens": _token_count(text),
                        "total_tokens": _token_count(prompt) + _token_count(text),
                    }
                self.last_usage = usage
                _add_usage(self.total_usage, usage)
                return text
            except ContainerIsolationUnavailable as exc:
                last_error = str(exc)
                break
            except Exception as exc:  # noqa: BLE001 - retry boundary normalizes CLI failures.
                last_error = str(exc)
                if attempt >= attempts - 1:
                    break
                backoff = min(2.0**attempt, 30.0) + random.uniform(0.0, 1.5)
                time.sleep(backoff)
        raise RuntimeError(f"codex judge request failed after {attempts} attempts: {last_error}")

    def _run_codex(self, prompt: str) -> tuple[str, Dict[str, int]]:
        with tempfile.TemporaryDirectory(prefix="codex-judge-") as tmp:
            prompt_dir = Path(tmp) / "empty-cwd"
            prompt_dir.mkdir()
            cmd = [
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--json",
                "-c",
                "approval_policy=never",
                "-c",
                "mcp_servers={}",
                "-m",
                self.model,
                "-",
            ]
            try:
                with _codex_container_invocation(
                    cmd,
                    cwd=prompt_dir,
                    worktree=None,
                    purpose="judge",
                ) as jailed:
                    completed = _run_reaped_subprocess(
                        jailed.cmd,
                        timeout_seconds=self.timeout_seconds,
                        cwd=jailed.cwd,
                        env=jailed.env,
                        input_text=prompt,
                        container_name=str(jailed.metadata.get("container_name") or ""),
                    )
                    isolation_mode = str(jailed.metadata.get("isolation_mode") or "container")
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"codex timed out after {self.timeout_seconds}s: {_tail(exc.stderr)}") from exc

            self.last_isolation_mode = isolation_mode
            self.isolation_mode_counts[isolation_mode] = int(self.isolation_mode_counts.get(isolation_mode, 0)) + 1

            if completed.returncode != 0:
                raise RuntimeError(
                    f"codex exited {completed.returncode}: "
                    f"stderr={_tail(completed.stderr)} stdout={_tail(completed.stdout)}"
                )

            text = _extract_last_assistant_message(completed.stdout).strip()
            if not text:
                raise RuntimeError(
                    f"codex returned an empty final message: stderr={_tail(completed.stderr)} "
                    f"stdout={_tail(completed.stdout)}"
                )
            return text, _usage_from_jsonl(completed.stdout)


def _tag(prompt: str) -> str:
    if "CONSOLIDATION_JUDGE_REQUEST" in prompt:
        return "consolidation"
    if "CONTRADICTION_JUDGE_REQUEST" in prompt:
        return "contradiction"
    if "REPLAY_JUDGE_REQUEST" in prompt:
        return "replay"
    return "unknown"


def _extract_last_assistant_message(stdout: str) -> str:
    last_text = ""
    for line in str(stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = _event_text(event)
        if text:
            last_text = text
    return last_text


def _event_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_event_text(item) for item in value]
        return "".join(part for part in parts if part)
    if not isinstance(value, Mapping):
        return ""

    role = str(value.get("role") or value.get("author") or "")
    event_type = str(value.get("type") or value.get("event") or "")
    if event_type in {"output_text", "text"} and value.get("text") is not None:
        return str(value.get("text") or "")
    if role == "assistant" or "assistant" in event_type or "message" in event_type:
        for key in ("text", "content", "message", "delta"):
            text = _event_text(value.get(key))
            if text:
                return text

    for key in ("item", "response", "payload", "data"):
        text = _event_text(value.get(key))
        if text:
            return text
    return ""


def _usage_from_jsonl(stdout: str) -> Dict[str, int]:
    total: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for line in str(stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = _find_usage(event)
        if usage:
            total = usage
    return total


def _find_usage(value: Any) -> Dict[str, int]:
    if isinstance(value, Mapping):
        usage = value.get("usage")
        if isinstance(usage, Mapping):
            return _usage_dict(usage)
        for item in value.values():
            found = _find_usage(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_usage(item)
            if found:
                return found
    return {}


def _usage_dict(usage: Mapping[str, Any]) -> Dict[str, int]:
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _add_usage(total: Dict[str, int], usage: Mapping[str, int]) -> None:
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    total["prompt_tokens"] = int(total.get("prompt_tokens", 0)) + prompt_tokens
    total["completion_tokens"] = int(total.get("completion_tokens", 0)) + completion_tokens
    total["total_tokens"] = int(total.get("total_tokens", 0)) + total_tokens


def _validate_jsonish(text: str) -> None:
    candidate = _strip_json_fence(text)
    try:
        json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"codex final message was not parseable JSON: {_tail(text)}") from exc


def _strip_json_fence(text: str) -> str:
    candidate = str(text or "").strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return candidate


def _token_count(text: Any) -> int:
    return len(str(text or "").split())


def _tail(value: Optional[Any], *, limit: int = 1200) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


__all__ = ["CodexJudgeClient"]
