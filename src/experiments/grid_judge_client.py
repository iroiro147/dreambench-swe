"""Single-shot GRID chat-completion client for DreamForge sleep judges.

The runner imports this only for live DF runs.  Dry-runs use StubJudgeClient in
``run_bench.py`` and never instantiate this helper.
"""
from __future__ import annotations

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agents.llm_agents import _read_env_file


JUDGE_MODELS = {"glm-latest", "kimi-latest", "gpt-5.5"}


class GridJudgeClient:
    """Callable ``complete(prompt) -> str`` wrapper for LLMJudge."""

    def __init__(
        self,
        *,
        model: str = "glm-latest",
        secrets_path: Optional[Path | str] = None,
        timeout_seconds: float = 300.0,
        max_tokens: int = 16384,
    ) -> None:
        if model not in JUDGE_MODELS:
            known = ", ".join(sorted(JUDGE_MODELS))
            raise ValueError(f"unsupported judge model {model!r}; expected one of {known}")
        self.model = model
        self.secrets_path = Path(secrets_path) if secrets_path is not None else _repo_root() / "experiments" / "secrets.env"
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.calls = 0
        self.calls_by_tag = {"consolidation": 0, "contradiction": 0, "replay": 0}
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.total_latency_seconds = 0.0

    def __call__(self, prompt: str) -> str:
        env = _read_env_file(self.secrets_path)
        base_url = env.get("GRID_BASE_URL") or os.environ.get("GRID_BASE_URL")
        api_key = env.get("GRID_API_KEY") or os.environ.get("GRID_API_KEY")
        if not base_url:
            raise RuntimeError(f"GRID_BASE_URL is missing from {self.secrets_path}")
        if not api_key:
            raise RuntimeError(f"GRID_API_KEY is missing from {self.secrets_path}")

        start = time.perf_counter()
        text, usage = self._chat_completion(base_url, api_key, prompt)
        self.total_latency_seconds += time.perf_counter() - start
        if int(usage.get("total_tokens") or 0) <= 0:
            usage = {
                "prompt_tokens": _token_count(prompt),
                "completion_tokens": _token_count(text),
                "total_tokens": _token_count(prompt) + _token_count(text),
            }
        self.calls += 1
        tag = _tag(prompt)
        self.calls_by_tag[tag] = int(self.calls_by_tag.get(tag, 0)) + 1
        _add_usage(self.total_usage, usage)
        return text

    def _chat_completion(self, base_url: str, api_key: str, prompt: str) -> tuple[str, Dict[str, int]]:
        endpoint = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON matching the user's requested schema. No prose.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        attempts = 6
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                if attempt == attempts:
                    raise RuntimeError(f"GRID judge request failed after {attempts} attempts: {exc}") from exc
                # 429-aware exponential backoff + jitter (GRID rate-limits concurrent judge calls)
                retry_after = 0.0
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429 and exc.headers:
                    try:
                        retry_after = float(exc.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        retry_after = 0.0
                backoff = max(retry_after, min(2.0 ** attempt, 30.0)) + random.uniform(0.0, 1.5)
                time.sleep(backoff)

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("GRID judge response did not include choices")
        message = choices[0].get("message") or {}
        usage = _usage_dict(data.get("usage") or {})
        return str(message.get("content") or ""), usage


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tag(prompt: str) -> str:
    if "CONSOLIDATION_JUDGE_REQUEST" in prompt:
        return "consolidation"
    if "CONTRADICTION_JUDGE_REQUEST" in prompt:
        return "contradiction"
    if "REPLAY_JUDGE_REQUEST" in prompt:
        return "replay"
    return "unknown"


def _usage_dict(usage: Mapping[str, Any]) -> Dict[str, int]:
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
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


def _token_count(text: Any) -> int:
    return len(str(text or "").split())


__all__ = ["GridJudgeClient"]
