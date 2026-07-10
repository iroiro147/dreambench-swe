"""Headless cc2 Claude client for DreamForge sleep judges."""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


EMPTY_MCP_CONFIG = json.dumps({"mcpServers": {}})


class Cc2JudgeClient:
    """Callable ``complete(prompt) -> str`` wrapper for LLMJudge."""

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-8",
        timeout_seconds: float = 180.0,
        max_retries: int = 4,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.token_path = Path.home() / ".claude-dp" / "cc2-oauth-token"
        self.token = self._read_token(self.token_path)
        self.config_dir = Path(tempfile.mkdtemp(prefix="cc2-judge-cfg-"))
        self._fallback_config_dir = Path.home() / ".claude-dp"
        self._use_fallback_config = False
        self.calls = 0
        self.calls_by_tag = {"consolidation": 0, "contradiction": 0, "replay": 0}
        self.total_usage: Dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self.last_usage: Dict[str, int] = {}

    def __call__(self, prompt: str) -> str:
        attempts = self.max_retries + 1
        last_error = "unknown error"
        for attempt in range(attempts):
            try:
                data = self._run_claude(prompt)
                usage = _usage_dict(data.get("usage") or {})
                text = str(data.get("result") or "")
                self.calls += 1
                tag = _tag(prompt)
                self.calls_by_tag[tag] = int(self.calls_by_tag.get(tag, 0)) + 1
                self.last_usage = usage
                _add_usage(self.total_usage, usage)
                return text
            except Exception as exc:  # noqa: BLE001 - retry boundary normalizes CLI failures.
                last_error = str(exc)
                if not self._use_fallback_config and _is_auth_error(last_error):
                    self._use_fallback_config = True
                    last_error = f"{last_error}; retrying with {self._fallback_config_dir}"
                if attempt >= attempts - 1:
                    break
                backoff = min(2.0**attempt, 8.0) + random.uniform(0.0, 1.0)
                time.sleep(backoff)
        raise RuntimeError(f"cc2 judge request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _read_token(path: Path) -> str:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RuntimeError(f"cc2 OAuth token is missing at {path}") from exc
        except OSError as exc:
            raise RuntimeError(f"could not read cc2 OAuth token at {path}: {exc}") from exc
        if not token:
            raise RuntimeError(f"cc2 OAuth token is empty at {path}")
        return token

    def _run_claude(self, prompt: str) -> Mapping[str, Any]:
        claude_bin = _claude_bin()
        cmd = [
            claude_bin,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--no-session-persistence",
            "--strict-mcp-config",
            "--mcp-config",
            EMPTY_MCP_CONFIG,
        ]
        active_config_dir = self._fallback_config_dir if self._use_fallback_config else self.config_dir
        env = {
            **os.environ,
            "CLAUDE_CODE_OAUTH_TOKEN": self.token,
            "CLAUDE_CONFIG_DIR": str(active_config_dir),
        }
        try:
            completed = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                env=env,
                cwd=str(self.config_dir),
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude timed out after {self.timeout_seconds}s: {_tail(exc.stderr)}") from exc

        if completed.returncode != 0:
            raise RuntimeError(
                f"claude exited {completed.returncode}: stderr={_tail(completed.stderr)} stdout={_tail(completed.stdout)}"
            )
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"claude returned non-JSON stdout: stdout={_tail(completed.stdout)} stderr={_tail(completed.stderr)}"
            ) from exc
        if not isinstance(data, Mapping):
            raise RuntimeError(f"claude returned a non-object JSON envelope: {type(data).__name__}")
        if data.get("is_error") or data.get("api_error_status"):
            raise RuntimeError(
                "claude error envelope: "
                f"is_error={data.get('is_error')!r} api_error_status={data.get('api_error_status')!r} "
                f"result={_tail(data.get('result'))}"
            )
        return data


def _claude_bin() -> str:
    preferred = Path.home() / ".npm-global" / "bin" / "claude"
    if preferred.exists():
        return str(preferred)
    found = shutil.which("claude")
    if found:
        return found
    raise RuntimeError("claude binary not found at ~/.npm-global/bin/claude or on PATH")


def _tag(prompt: str) -> str:
    if "CONSOLIDATION_JUDGE_REQUEST" in prompt:
        return "consolidation"
    if "CONTRADICTION_JUDGE_REQUEST" in prompt:
        return "contradiction"
    if "REPLAY_JUDGE_REQUEST" in prompt:
        return "replay"
    return "unknown"


def _usage_dict(usage: Mapping[str, Any]) -> Dict[str, int]:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_creation_input_tokens = int(usage.get("cache_creation_input_tokens") or 0)
    cache_read_input_tokens = int(usage.get("cache_read_input_tokens") or 0)
    prompt_tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
    total_tokens = prompt_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _add_usage(total: Dict[str, int], usage: Mapping[str, int]) -> None:
    for key, value in usage.items():
        total[key] = int(total.get(key, 0)) + int(value or 0)


def _is_auth_error(message: str) -> bool:
    folded = message.lower()
    return any(
        marker in folded
        for marker in (
            "auth",
            "oauth",
            "login",
            "api key",
            "api_key",
            "anthropic_api_key",
            "unauthorized",
            "401",
        )
    )


def _tail(value: Optional[Any], *, limit: int = 1200) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


__all__ = ["Cc2JudgeClient"]
