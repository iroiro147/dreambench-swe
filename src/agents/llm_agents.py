"""Runtime LLM-backed agents for the DreamBench-SWE experiment runner.

Imports are offline-safe.  Subprocess and network effects are only performed
inside ``run_task`` for the model-backed agents.  ``StubAgent`` is fully
in-process and is the only agent used by the runner selftest path.
"""
from __future__ import annotations

import json
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import difflib
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from agents.coding_agent_adapter import AgentAdapter, TaskResult


GRID_MODELS = {"glm-latest", "kimi-latest"}
GRID_MODEL_PREFIX = "grid:"
GRID_DEFAULT_TIMEOUT_SECONDS = 300.0
GRID_WAKE_MAX_TOKENS = 4096
HIDDEN_TASK_KEYS = {"reference_patch", "expected_pass", "injected_memory_event", "error_taxonomy_hint", "oracle_cmd"}
GRID_WAKE_SYSTEM_PROMPT = (
    "Return only a complete unified diff: diff --git lines plus --- a/path, +++ b/path, "
    "and accurate @@ hunks. No prose."
)
CODEX_ARGS = [
    "codex",
    "exec",
    "--sandbox",
    "danger-full-access",
    "--skip-git-repo-check",
    "-c",
    "approval_policy=never",
    "-c",
    "mcp_servers={}",
]

_CONTAINER_RUNTIME_READY_CACHE: Optional[bool] = None
_CONTAINER_RUNTIME_READY_ERROR: Optional[str] = None
_GRID_CONTAINER_READY_CACHE: Optional[bool] = None
_GRID_CONTAINER_READY_ERROR: Optional[str] = None
_CODEX_AGENT_IMAGE = os.environ.get("DREAMBENCH_CODEX_AGENT_IMAGE", "dreambench-swe-codex-agent:latest")
_CODEX_CONTAINER_NETWORK = os.environ.get("DREAMBENCH_CODEX_CONTAINER_NETWORK", "bridge")
_GRID_AGENT_IMAGE = os.environ.get("DREAMBENCH_GRID_AGENT_IMAGE", "dreambench-swe-api-agent:latest")
_GRID_CONTAINER_NETWORK = os.environ.get("DREAMBENCH_GRID_CONTAINER_NETWORK", _CODEX_CONTAINER_NETWORK)
_GRID_AGENT_CONTAINER_DEFAULT = os.environ.get("DREAMBENCH_GRID_AGENT_CONTAINER", "1").lower() not in {"0", "false", "no"}


class ContainerIsolationUnavailable(RuntimeError):
    """Raised when the Docker/OrbStack container boundary cannot be established."""


class GridRequestTimeout(TimeoutError):
    """Raised when the GRID wake request or API-agent container times out."""


class GridContainerExecutionError(RuntimeError):
    """Raised when the GRID API-agent container exits before returning JSON."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.metadata = dict(metadata)


class CodexAgent(AgentAdapter):
    """Adapter around ``codex exec``.

    The constructor only records configuration.  ``run_task`` expects a
    materialized worktree path in the task mapping under ``worktree`` or
    ``task_dir`` and shells out from that directory.
    """

    def __init__(self, model: str = "gpt-5.5", *, timeout_seconds: Optional[float] = None) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.last_run: Dict[str, Any] = {}

    def run_task(self, task: Any, memory_context: Any) -> TaskResult:
        public_task = _task_mapping(task)
        worktree = _worktree_from_task(public_task)
        prompt = _agent_prompt(public_task, _context_items(memory_context))
        cmd = [*CODEX_ARGS, "-m", self.model, "-"]

        steps = [{"action": "codex exec", "cwd": str(worktree), "argv": _redacted_cmd(cmd)}]
        try:
            with _codex_jail(
                cmd,
                cwd=worktree,
                read_write_roots=[worktree],
                purpose="wake",
            ) as jailed:
                steps[0]["argv"] = _redacted_cmd(jailed.cmd)
                steps[0]["jail"] = jailed.metadata
                isolation_mode = str(jailed.metadata.get("isolation_mode") or "container")
                proc = _run_reaped_subprocess(
                    jailed.cmd,
                    cwd=jailed.cwd,
                    timeout_seconds=self.timeout_seconds,
                    env=jailed.env,
                    input_text=prompt,
                    container_name=str(jailed.metadata.get("container_name") or ""),
                )
        except ContainerIsolationUnavailable as exc:
            diff = _git_diff(worktree)
            self.last_run = {
                "agent": "codex",
                "model": self.model,
                "worktree": str(worktree),
                "command": _redacted_cmd(cmd),
                "stdout": "",
                "stderr": str(exc),
                "exit_code": None,
                "diff": diff,
                "steps": steps,
                "usage": {},
                "outcome": "isolation_unavailable",
                "error_type": "isolation_unavailable",
                "isolation_mode": "unavailable",
            }
            return TaskResult(
                patch=diff,
                passed=False,
                steps=len(steps),
                tokens=_token_count(prompt),
                error_type="isolation_unavailable",
                error_detail=str(exc),
            )
        except RuntimeError as exc:
            diff = _git_diff(worktree)
            self.last_run = {
                "agent": "codex",
                "model": self.model,
                "worktree": str(worktree),
                "command": _redacted_cmd(cmd),
                "stdout": "",
                "stderr": str(exc),
                "exit_code": None,
                "diff": diff,
                "steps": steps,
                "usage": {},
                "outcome": "isolation_failed",
                "error_type": "codex_isolation_failed",
                "isolation_mode": "unavailable",
            }
            return TaskResult(
                patch=diff,
                passed=False,
                steps=len(steps),
                tokens=_token_count(prompt),
                error_type="codex_isolation_failed",
                error_detail=str(exc),
            )
        except subprocess.TimeoutExpired:
            diff = _git_diff(worktree)
            self.last_run = {
                "agent": "codex", "model": self.model, "worktree": str(worktree),
                "command": _redacted_cmd(cmd), "stdout": "",
                "stderr": f"codex timed out after {self.timeout_seconds}s",
                "exit_code": None, "diff": diff, "steps": steps, "usage": {},
                "outcome": "timeout", "error_type": "codex_timeout",
                "isolation_mode": locals().get("isolation_mode", "unknown"),
            }
            return TaskResult(
                patch=diff,
                passed=False,
                steps=len(steps),
                tokens=_token_count(prompt),
                error_type="codex_timeout",
                error_detail=f"codex timed out after {self.timeout_seconds}s",
            )
        diff = _git_diff(worktree)
        usage = _parse_usage(proc.stdout + "\n" + proc.stderr)
        tokens = usage.get("total_tokens")
        if tokens is None:
            tokens = _token_count(prompt) + _token_count(proc.stdout) + _token_count(proc.stderr) + _token_count(diff)

        error_type = None if proc.returncode == 0 else _codex_error_type(proc.stderr)
        self.last_run = {
            "agent": "codex",
            "model": self.model,
            "worktree": str(worktree),
            "command": _redacted_cmd(cmd),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "diff": diff,
            "steps": steps,
            "usage": usage,
            "outcome": "completed" if proc.returncode == 0 else "agent_error",
            "error_type": error_type,
            "isolation_mode": locals().get("isolation_mode", "unknown"),
        }
        return TaskResult(
            patch=diff,
            passed=proc.returncode == 0,
            steps=len(steps),
            tokens=int(tokens),
            error_type=error_type,
            error_detail=None if error_type is None else _tail(proc.stderr, limit=500),
        )


class _JailedInvocation:
    def __init__(self, *, cmd: List[str], cwd: Path, env: Dict[str, str], metadata: Dict[str, Any]) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.metadata = metadata


class GridAgent(AgentAdapter):
    """OpenAI-compatible GRID adapter with a small edit-test loop."""

    def __init__(
        self,
        model: str,
        *,
        max_steps: int = 1,
        secrets_path: Optional[Path | str] = None,
        timeout_seconds: Optional[float] = None,
        use_container: Optional[bool] = None,
    ) -> None:
        provider_model = _normalize_grid_model(model)
        if provider_model not in GRID_MODELS:
            known = ", ".join(sorted(GRID_MODELS))
            raise ValueError(f"unsupported GRID model {model!r}; expected one of {known}")
        self.model = f"{GRID_MODEL_PREFIX}{provider_model}"
        self.provider_model = provider_model
        self.max_steps = max(1, int(max_steps))
        self.secrets_path = Path(secrets_path) if secrets_path is not None else _repo_root() / "experiments" / "secrets.env"
        self.timeout_seconds = _grid_timeout_seconds(timeout_seconds)
        self.use_container = _GRID_AGENT_CONTAINER_DEFAULT if use_container is None else bool(use_container)
        self.last_run: Dict[str, Any] = {}
        self._last_request_metadata: Dict[str, Any] = {}

    def run_task(self, task: Any, memory_context: Any) -> TaskResult:
        from experiments.env import load_env

        public_task = _task_mapping(task)
        worktree = _worktree_from_task(public_task)
        context = _context_items(memory_context)
        transcript: List[Dict[str, Any]] = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        last_error: Optional[str] = None

        try:
            env = _read_env_file(self.secrets_path)
            base_url = env.get("GRID_BASE_URL") or os.environ.get("GRID_BASE_URL")
            api_key = env.get("GRID_API_KEY") or os.environ.get("GRID_API_KEY")
            if not base_url:
                raise RuntimeError(f"GRID_BASE_URL is missing from {self.secrets_path}")
            if not api_key:
                raise RuntimeError(f"GRID_API_KEY is missing from {self.secrets_path}")

            for step_index in range(1, self.max_steps + 1):
                files_view = _show_files(worktree)
                prompt = _grid_prompt(public_task, context, files_view, transcript)
                response_text, usage = self._chat_completion(base_url, api_key, prompt, worktree=worktree)
                _add_usage(total_usage, usage)
                patch = _extract_diff(response_text)
                step_record: Dict[str, Any] = {
                    "action": "grid chat/completions",
                    "step_index": step_index,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "response_excerpt": response_text[:4000],
                    "diff": patch,
                    "request": dict(self._last_request_metadata),
                }
                if not patch.strip():
                    last_error = "missing_diff"
                    step_record["outcome"] = last_error
                    transcript.append(step_record)
                    break

                diff_info = load_env.production_diff(patch)
                step_record["production_files"] = diff_info["production_files"]
                step_record["rejected_files"] = diff_info["rejected_files"]
                if diff_info["rejected_files"]:
                    last_error = "disallowed_edit"
                    step_record["outcome"] = last_error
                    transcript.append(step_record)
                    break
                if not diff_info["production_diff"].strip():
                    last_error = "empty_production_diff"
                    step_record["outcome"] = last_error
                    transcript.append(step_record)
                    break

                apply_result = _apply_grid_patch(worktree, diff_info["production_diff"])
                step_record["apply"] = apply_result
                if not apply_result["passed"]:
                    last_error = "patch_apply_failed"
                    transcript.append(step_record)
                    continue
                transcript.append(step_record)
                last_error = None
                break
        except GridRequestTimeout as exc:
            return self._failure_result(
                worktree,
                transcript,
                total_usage,
                error_type="grid_timeout",
                failure_reason=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - adapter failures must stay in TaskResult space.
            if isinstance(exc, GridContainerExecutionError):
                self._last_request_metadata = dict(exc.metadata)
            return self._failure_result(
                worktree,
                transcript,
                total_usage,
                error_type="grid_exec_failed",
                failure_reason=f"{type(exc).__name__}: {exc}",
            )

        passed = bool(transcript and transcript[-1].get("apply", {}).get("passed"))
        diff = _git_diff(worktree) if passed else (patch if "patch" in locals() and str(patch).strip() else _git_diff(worktree))
        terminal_error = None if passed else "grid_exec_failed"
        self.last_run = self._last_run_metadata(
            worktree=worktree,
            steps=transcript,
            usage=total_usage,
            diff=diff,
            outcome="patch_applied" if passed else "failed",
            error_type=terminal_error,
            failure_reason=None if passed else last_error,
        )
        return TaskResult(
            patch=diff,
            passed=passed,
            steps=len(transcript),
            tokens=int(total_usage.get("total_tokens") or _token_count(diff)),
            error_type=terminal_error,
            error_detail=None if passed else _tail(last_error, limit=500),
        )

    def _failure_result(
        self,
        worktree: Path,
        transcript: List[Dict[str, Any]],
        total_usage: Dict[str, int],
        *,
        error_type: str,
        failure_reason: str,
    ) -> TaskResult:
        diff = _git_diff(worktree)
        self.last_run = self._last_run_metadata(
            worktree=worktree,
            steps=transcript,
            usage=total_usage,
            diff=diff,
            outcome="timeout" if error_type == "grid_timeout" else "agent_error",
            error_type=error_type,
            failure_reason=failure_reason,
        )
        return TaskResult(
            patch=diff,
            passed=False,
            steps=max(1, len(transcript)),
            tokens=int(total_usage.get("total_tokens") or _token_count(diff)),
            error_type=error_type,
            error_detail=_tail(failure_reason, limit=500),
        )

    def _last_run_metadata(
        self,
        *,
        worktree: Path,
        steps: Sequence[Mapping[str, Any]],
        usage: Mapping[str, int],
        diff: str,
        outcome: str,
        error_type: Optional[str],
        failure_reason: Optional[str],
    ) -> Dict[str, Any]:
        request_metadata = dict(self._last_request_metadata)
        return {
            "agent": "grid",
            "model": self.model,
            "provider_model": self.provider_model,
            "worktree": str(worktree),
            "steps": list(steps),
            "usage": dict(usage),
            "diff": diff,
            "outcome": outcome,
            "error_type": error_type,
            "failure_reason": failure_reason,
            "isolation_mode": request_metadata.get("isolation_mode") or ("container" if self.use_container else "host"),
            "auth_material": request_metadata.get("auth_material") or _grid_auth_metadata(),
            "request": request_metadata,
        }

    def _chat_completion(self, base_url: str, api_key: str, prompt: str, *, worktree: Path) -> Tuple[str, Dict[str, int]]:
        if self.use_container:
            _ensure_grid_container_ready()
            invocation = _grid_container_invocation(
                base_url=base_url,
                api_key=api_key,
                model=self.provider_model,
                worktree=worktree,
                timeout_seconds=self.timeout_seconds,
                command=["python3", "-c", _GRID_CONTAINER_COMPLETION_SCRIPT],
            )
            self._last_request_metadata = dict(invocation.metadata)
            data = _grid_chat_completion_in_container(
                invocation=invocation,
                prompt=prompt,
                timeout_seconds=self.timeout_seconds,
            )
            self._last_request_metadata = data.pop("_metadata")
            return _grid_response_text_and_usage(data)

        endpoint = base_url.rstrip("/") + "/chat/completions"
        self._last_request_metadata = _grid_host_request_metadata(endpoint)
        payload = {
            "model": self.provider_model,
            "messages": [
                {
                    "role": "system",
                    "content": GRID_WAKE_SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": _grid_wake_max_tokens(),
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
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GRID request failed: {_http_error_detail(exc)}") from exc
        except Exception as exc:
            if _is_timeout_exception(exc):
                raise GridRequestTimeout(f"GRID request timed out after {self.timeout_seconds}s") from exc
            raise RuntimeError(f"GRID request failed: {exc}") from exc

        return _grid_response_text_and_usage(data)


class StubAgent(AgentAdapter):
    """Deterministic in-process agent for selftests.

    It deliberately does not consume oracle labels or reference patches.  The
    runner's stub oracle only checks that the offline control path completed.
    """

    def __init__(self) -> None:
        self.last_run: Dict[str, Any] = {}

    def run_task(self, task: Any, memory_context: Any) -> TaskResult:
        public_task = _task_mapping(task)
        negative_control = bool(public_task.get("negative_control"))
        patch = "" if negative_control else _stub_patch(public_task, _context_items(memory_context))
        passed = bool(patch.strip()) and not negative_control
        tokens = _token_count(public_task.get("instruction") or public_task.get("prompt") or "") + _token_count(patch)
        self.last_run = {
            "agent": "stub",
            "model": "stub",
            "steps": [{"action": "return-reference-patch" if patch else "noop"}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens},
            "diff": patch,
            "outcome": "passed" if passed else "negative_control_noop",
            "memory_context_count": len(_context_items(memory_context)),
        }
        return TaskResult(
            patch=patch,
            passed=passed,
            steps=1,
            tokens=tokens,
            error_type=None if passed else "negative_control",
        )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _task_mapping(task: Any) -> Dict[str, Any]:
    if isinstance(task, Mapping):
        data = dict(task)
    elif hasattr(task, "to_dict"):
        data = dict(task.to_dict())
    else:
        data = {
            key: getattr(task, key)
            for key in (
                "id",
                "repo",
                "seq_id",
                "sequence_id",
                "seq_type",
                "session_index",
                "instruction",
                "prompt",
                "worktree",
                "task_dir",
                "files",
            )
            if hasattr(task, key)
        }
    for key in HIDDEN_TASK_KEYS:
        data.pop(key, None)
    return data


def _stub_patch(task: Mapping[str, Any], memory_context: Sequence[Mapping[str, Any]]) -> str:
    task_id = task.get("id") or _task_id(task)
    instruction = str(task.get("instruction") or task.get("prompt") or "").strip()
    memory_count = len(memory_context)
    return "\n".join([
        f"stub_patch: {task_id}",
        f"instruction_words: {_token_count(instruction)}",
        f"admitted_memory_items: {memory_count}",
        "",
    ])


def _context_items(memory_context: Any) -> List[Dict[str, Any]]:
    if memory_context is None:
        return []
    if isinstance(memory_context, Mapping):
        values: Iterable[Any] = memory_context.get("memories", [memory_context])  # type: ignore[assignment]
    elif isinstance(memory_context, str):
        values = [{"id": "text-context", "content": memory_context}]
    elif isinstance(memory_context, Iterable):
        values = memory_context
    else:
        values = [memory_context]

    out: List[Dict[str, Any]] = []
    for item in values:
        if isinstance(item, Mapping):
            out.append(dict(item))
        elif hasattr(item, "to_dict"):
            out.append(dict(item.to_dict()))
        else:
            out.append({"id": repr(type(item)), "content": str(item)})
    return out


def _worktree_from_task(task: Mapping[str, Any]) -> Path:
    value = task.get("worktree") or task.get("task_dir")
    if not value:
        raise ValueError("task must include a materialized worktree path")
    return Path(str(value)).resolve()


def _normalize_grid_model(model: str) -> str:
    value = str(model or "").strip()
    if value.startswith(GRID_MODEL_PREFIX):
        value = value[len(GRID_MODEL_PREFIX):]
    return value


def _grid_response_text_and_usage(data: Mapping[str, Any]) -> Tuple[str, Dict[str, int]]:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("GRID response did not include choices")
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise RuntimeError("GRID response choice was malformed")
    message = first_choice.get("message") or {}
    if not isinstance(message, Mapping):
        raise RuntimeError("GRID response message was malformed")
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("GRID response message did not include string content")
    usage = data.get("usage") or {}
    usage_map = usage if isinstance(usage, Mapping) else {}
    return content, {
        "prompt_tokens": int(usage_map.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_map.get("completion_tokens") or 0),
        "total_tokens": int(usage_map.get("total_tokens") or 0),
    }


def _grid_auth_metadata() -> Dict[str, Any]:
    return {
        "mounted": [],
        "env": ["GRID_BASE_URL", "GRID_API_KEY"],
        "secret_values_recorded": False,
    }


def _grid_host_request_metadata(endpoint: str) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(endpoint)
    return {
        "type": "host-http",
        "isolation_mode": "host",
        "endpoint_host": parsed.netloc,
        "endpoint_path": parsed.path,
        "auth_material": _grid_auth_metadata(),
    }


def _grid_container_metadata(
    *,
    container_name: str,
    worktree: Path,
    base_url: str,
    container_base_url: str,
    container_network: str,
    docker_extra_args: Sequence[str],
    loopback_strategy: str,
) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(base_url)
    container_parsed = urllib.parse.urlparse(container_base_url)
    rewritten = base_url != container_base_url
    return {
        "type": "docker-container",
        "purpose": "wake-grid",
        "container_name": container_name,
        "image": _GRID_AGENT_IMAGE,
        "network": container_network,
        "container_cwd": "/work",
        "worktree": str(worktree.resolve()),
        "mounts": ["/work"],
        "auth_material": _grid_auth_metadata(),
        "user": f"{os.getuid()}:{os.getgid()}",
        "isolation_mode": "container",
        "base_url_host": parsed.netloc,
        "base_url_path": parsed.path,
        "container_base_url_host": container_parsed.netloc,
        "container_base_url_path": container_parsed.path,
        "base_url_rewritten_for_container": rewritten,
        "loopback_strategy": loopback_strategy,
        "docker_extra_args": list(docker_extra_args),
    }


def _grid_container_invocation(
    *,
    base_url: str,
    api_key: str,
    model: str,
    worktree: Path,
    timeout_seconds: float,
    command: Sequence[str],
) -> _JailedInvocation:
    container_name = f"dreambench-grid-wake-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    docker_bin = _docker_bin()
    container_base_url, container_network, docker_extra_args, loopback_strategy = _grid_container_base_url(base_url)
    metadata = _grid_container_metadata(
        container_name=container_name,
        worktree=worktree,
        base_url=base_url,
        container_base_url=container_base_url,
        container_network=container_network,
        docker_extra_args=docker_extra_args,
        loopback_strategy=loopback_strategy,
    )
    docker_cmd = [
        docker_bin,
        "run",
        "--rm",
        "-i",
        "--name",
        container_name,
        "--network",
        container_network,
        *docker_extra_args,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "GRID_BASE_URL",
        "-e",
        "GRID_API_KEY",
        "-e",
        "GRID_MODEL",
        "-e",
        "GRID_TIMEOUT_SECONDS",
        "-e",
        "GRID_MAX_TOKENS",
        "-v",
        f"{worktree.resolve()}:/work:rw",
        "-w",
        "/work",
        _GRID_AGENT_IMAGE,
        *[str(part) for part in command],
    ]
    env = os.environ.copy()
    env.update(
        {
            "GRID_BASE_URL": container_base_url,
            "GRID_API_KEY": api_key,
            "GRID_MODEL": model,
            "GRID_TIMEOUT_SECONDS": str(timeout_seconds),
            "GRID_MAX_TOKENS": str(_grid_wake_max_tokens()),
        }
    )
    return _JailedInvocation(cmd=docker_cmd, cwd=worktree, env=env, metadata=metadata)


def _grid_chat_completion_in_container(
    *,
    invocation: _JailedInvocation,
    prompt: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    metadata = dict(invocation.metadata)
    container_name = str(metadata.get("container_name") or "")
    try:
        proc = _run_reaped_subprocess(
            invocation.cmd,
            cwd=invocation.cwd,
            timeout_seconds=max(float(timeout_seconds) + 15.0, 20.0),
            env=invocation.env,
            input_text=json.dumps({"prompt": prompt}),
            container_name=container_name,
        )
    except OSError as exc:
        raise GridContainerExecutionError(f"GRID API-agent container launch failed: {exc}", metadata=metadata) from exc
    except subprocess.TimeoutExpired as exc:
        raise GridRequestTimeout(f"GRID API-agent container timed out after {timeout_seconds}s") from exc
    if proc.returncode == 124:
        raise GridRequestTimeout(f"GRID request timed out after {timeout_seconds}s")
    if proc.returncode != 0:
        detail = _tail((proc.stderr or "") + "\n" + (proc.stdout or ""), limit=1200)
        raise GridContainerExecutionError(f"GRID API-agent container failed: {detail}", metadata=metadata)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GridContainerExecutionError(
            f"GRID API-agent returned malformed JSON: {_tail(proc.stdout)}",
            metadata=metadata,
        ) from exc
    if not isinstance(data, Mapping):
        raise GridContainerExecutionError("GRID API-agent returned non-object JSON", metadata=metadata)
    result = dict(data)
    result["_metadata"] = metadata
    return result


def _grid_container_base_url(base_url: str) -> Tuple[str, str, List[str], str]:
    parsed = urllib.parse.urlparse(str(base_url or ""))
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return base_url, _GRID_CONTAINER_NETWORK, [], "none"

    if _GRID_CONTAINER_NETWORK == "host":
        return base_url, "host", [], "configured-host-network"

    if _GRID_CONTAINER_NETWORK == "bridge" and sys.platform.startswith("linux"):
        return base_url, "host", [], "linux-host-network"

    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"host.docker.internal{port}"
    rewritten = urllib.parse.urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )
    return rewritten, _GRID_CONTAINER_NETWORK, ["--add-host", "host.docker.internal:host-gateway"], "host-gateway"


def _is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        return isinstance(reason, (socket.timeout, TimeoutError))
    return False


def _http_error_detail(exc: urllib.error.HTTPError, *, limit: int = 1200) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - diagnostics must not mask the original HTTP error.
        body = ""
    reason = getattr(exc, "reason", "") or ""
    detail = f"HTTP {exc.code} {reason}".strip()
    if body:
        detail = f"{detail}: {_tail(body, limit=limit)}"
    return detail


def _grid_timeout_seconds(timeout_seconds: Optional[float]) -> float:
    if timeout_seconds is not None:
        return float(timeout_seconds)
    return float(os.environ.get("DREAMBENCH_GRID_TIMEOUT") or GRID_DEFAULT_TIMEOUT_SECONDS)


def _grid_wake_max_tokens() -> int:
    return int(os.environ.get("DREAMBENCH_GRID_MAX_TOKENS") or GRID_WAKE_MAX_TOKENS)


def _apply_grid_patch(worktree: Path, patch_text: str) -> Dict[str, Any]:
    normalized_patch, normalization = _normalize_grid_patch_headers(patch_text)
    attempts: List[Dict[str, Any]] = []
    strategies = [
        ("git_apply", ["apply", "--whitespace=nowarn"]),
        ("git_apply_recount", ["apply", "--whitespace=nowarn", "--recount"]),
        ("git_apply_3way", ["apply", "--whitespace=nowarn", "--3way"]),
    ]
    for strategy, args in strategies:
        proc = _git_apply(worktree, args, normalized_patch)
        attempt = {
            "strategy": strategy,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "passed": proc.returncode == 0,
        }
        attempts.append(attempt)
        if proc.returncode == 0:
            return {
                "passed": True,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
                "strategy": strategy,
                "attempts": attempts,
                "normalization": normalization,
            }
    last = attempts[-1] if attempts else {"stdout": "", "stderr": "", "exit_code": None}
    return {
        "passed": False,
        "stdout": str(last.get("stdout") or ""),
        "stderr": str(last.get("stderr") or ""),
        "exit_code": last.get("exit_code"),
        "strategy": None,
        "attempts": attempts,
        "normalization": normalization,
    }


def _git_apply(worktree: Path, args: Sequence[str], patch_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "diff.external=", *args],
        cwd=str(worktree),
        input=patch_text,
        text=True,
        capture_output=True,
    )


def _normalize_grid_patch_headers(patch_text: str) -> Tuple[str, Dict[str, Any]]:
    lines = patch_text.splitlines(keepends=True)
    normalized: List[str] = []
    synthesized: List[Dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("diff --git "):
            normalized.append(lines[index])
            index += 1
            continue

        block: List[str] = []
        while index < len(lines):
            if block and lines[index].startswith("diff --git "):
                break
            block.append(lines[index])
            index += 1
        normalized_block, block_synthesized = _normalize_grid_diff_block(block)
        normalized.extend(normalized_block)
        if block_synthesized:
            synthesized.append(block_synthesized)

    normalized_text = "".join(normalized)
    return normalized_text, {
        "changed": normalized_text != patch_text,
        "synthesized_header_count": len(synthesized),
        "synthesized_headers": synthesized,
    }


def _normalize_grid_diff_block(block: Sequence[str]) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    if not block:
        return [], None
    diff_paths = _diff_git_paths(block[0])
    if diff_paths is None:
        return list(block), None

    first_hunk_index = next((index for index, line in enumerate(block) if line.startswith("@@ ")), None)
    if first_hunk_index is None:
        return list(block), None

    pre_hunk = block[:first_hunk_index]
    has_old_header = any(line.startswith("--- ") for line in pre_hunk)
    has_new_header = any(line.startswith("+++ ") for line in pre_hunk)
    if has_old_header and has_new_header:
        return list(block), None

    old_header, new_header, reason = _synthesized_grid_headers(diff_paths, block)
    insertion: List[str] = []
    if not has_old_header:
        insertion.append(f"--- {old_header}\n")
    if not has_new_header:
        insertion.append(f"+++ {new_header}\n")
    if not insertion:
        return list(block), None

    normalized = [*block[:first_hunk_index], *insertion, *block[first_hunk_index:]]
    return normalized, {
        "old": old_header,
        "new": new_header,
        "reason": reason,
        "diff_old": diff_paths[0],
        "diff_new": diff_paths[1],
    }


def _diff_git_paths(line: str) -> Optional[Tuple[str, str]]:
    parts = line.strip().split(maxsplit=3)
    if len(parts) != 4 or parts[0] != "diff" or parts[1] != "--git":
        return None
    old_path = _strip_diff_prefix(parts[2], "a/")
    new_path = _strip_diff_prefix(parts[3], "b/")
    if not old_path or not new_path:
        return None
    return old_path, new_path


def _strip_diff_prefix(path: str, prefix: str) -> str:
    value = path.strip().strip('"')
    if value == "/dev/null":
        return value
    return value[len(prefix):] if value.startswith(prefix) else value


def _synthesized_grid_headers(diff_paths: Tuple[str, str], block: Sequence[str]) -> Tuple[str, str, str]:
    old_path, new_path = diff_paths
    old_header = "/dev/null" if old_path == "/dev/null" else f"a/{old_path}"
    new_header = "/dev/null" if new_path == "/dev/null" else f"b/{new_path}"
    reason = "missing_unified_headers"
    joined_pre_hunk = "".join(line for line in block if not line.startswith("@@ "))
    hunk = next((line for line in block if line.startswith("@@ ")), "")
    hunk_match = re.match(r"^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@", hunk)
    old_start = int(hunk_match.group("old_start")) if hunk_match else None
    new_start = int(hunk_match.group("new_start")) if hunk_match else None

    if "new file mode" in joined_pre_hunk or old_start == 0:
        old_header = "/dev/null"
        reason = "missing_new_file_headers"
    if "deleted file mode" in joined_pre_hunk or new_start == 0:
        new_header = "/dev/null"
        reason = "missing_deleted_file_headers"
    return old_header, new_header, reason


_GRID_CONTAINER_COMPLETION_SCRIPT = r"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

payload = json.loads(sys.stdin.read() or "{}")
base_url = os.environ["GRID_BASE_URL"].rstrip("/")
api_key = os.environ["GRID_API_KEY"]
model = os.environ["GRID_MODEL"]
timeout = float(os.environ.get("GRID_TIMEOUT_SECONDS") or "300")
max_tokens = int(os.environ.get("GRID_MAX_TOKENS") or "4096")
request_body = {
    "model": model,
    "messages": [
        {"role": "system", "content": "Return only a complete unified diff: diff --git lines plus --- a/path, +++ b/path, and accurate @@ hunks. No prose."},
        {"role": "user", "content": str(payload.get("prompt") or "")},
    ],
    "temperature": 0,
    "max_tokens": max_tokens,
}
request = urllib.request.Request(
    base_url + "/chat/completions",
    data=json.dumps(request_body).encode("utf-8"),
    headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        sys.stdout.write(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    detail = "GRID request failed: HTTP " + str(exc.code)
    if getattr(exc, "reason", None):
        detail += " " + str(exc.reason)
    if body:
        detail += ": " + body[:1200]
    sys.stderr.write(detail)
    raise SystemExit(1)
except (socket.timeout, TimeoutError) as exc:
    sys.stderr.write("GRID request timed out: " + str(exc))
    raise SystemExit(124)
except urllib.error.URLError as exc:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        sys.stderr.write("GRID request timed out: " + str(exc))
        raise SystemExit(124)
    sys.stderr.write("GRID request failed: " + str(exc))
    raise SystemExit(1)
""".strip()


@contextmanager
def _codex_jail(
    cmd: Sequence[str],
    *,
    cwd: Path,
    read_write_roots: Sequence[Path],
    purpose: str,
) -> Iterator[_JailedInvocation]:
    """Run real Codex CLI invocations inside the Docker/OrbStack container jail.

    Stand-in commands used by unit tests are left in-process.  Every real
    ``codex exec`` benchmark path must go through Docker and fail closed if the
    runtime, image, or file-based Codex auth mount is unavailable.
    """

    worktree = Path(read_write_roots[0]).resolve() if read_write_roots else None
    with _codex_container_invocation(cmd, cwd=cwd, worktree=worktree, purpose=purpose) as jailed:
        yield jailed


@contextmanager
def _codex_container_invocation(
    cmd: Sequence[str],
    *,
    cwd: Path,
    worktree: Optional[Path],
    purpose: str,
) -> Iterator[_JailedInvocation]:
    base_cmd = [str(part) for part in cmd]
    cwd = cwd.resolve()
    if not _is_codex_cli_command(base_cmd):
        yield _JailedInvocation(
            cmd=base_cmd,
            cwd=cwd,
            env=os.environ.copy(),
            metadata={"type": "none", "reason": "non-codex-test-stand-in"},
        )
        return

    _ensure_codex_container_ready()
    codex_home = _host_codex_home()
    container_name = f"dreambench-{purpose}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    docker_bin = _docker_bin()
    docker_cmd = [
        docker_bin,
        "run",
        "--rm",
        "-i",
        "--name",
        container_name,
        "--network",
        _CODEX_CONTAINER_NETWORK,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "CODEX_HOME=/codexhome",
        "-e",
        "HOME=/codexhome",
        "-e",
        "NO_COLOR=1",
        "-v",
        f"{codex_home}:/codex-home:ro",
    ]
    mount_points = ["/codex-home"]
    if worktree is not None:
        resolved_worktree = worktree.resolve()
        docker_cmd.extend(["-v", f"{resolved_worktree}:/work:rw", "-w", "/work"])
        mount_points.append("/work")
        container_cwd = "/work"
    else:
        docker_cmd.extend(["-w", "/tmp"])
        container_cwd = "/tmp"

    jailed_cmd = [*docker_cmd, _CODEX_AGENT_IMAGE, *base_cmd]
    metadata = {
        "type": "docker-container",
        "purpose": purpose,
        "container_name": container_name,
        "image": _CODEX_AGENT_IMAGE,
        "network": _CODEX_CONTAINER_NETWORK,
        "cwd": str(cwd),
        "container_cwd": container_cwd,
        "worktree": str(worktree.resolve()) if worktree is not None else None,
        "mounts": mount_points,
        "codex_home_mount": "/codex-home:ro",
        "writable_codex_home": "/tmp/codexhome",
        "user": f"{os.getuid()}:{os.getgid()}",
        "inner_codex_sandbox": _inner_codex_sandbox(base_cmd),
        "isolation_mode": "container",
    }
    yield _JailedInvocation(cmd=jailed_cmd, cwd=cwd, env=os.environ.copy(), metadata=metadata)


def _inner_codex_sandbox(cmd: Sequence[str]) -> str:
    for index, part in enumerate(cmd):
        if part == "--sandbox" and index + 1 < len(cmd):
            return str(cmd[index + 1])
    return "unknown"


def _ensure_codex_container_ready() -> None:
    global _CONTAINER_RUNTIME_READY_CACHE, _CONTAINER_RUNTIME_READY_ERROR
    if _CONTAINER_RUNTIME_READY_CACHE:
        return
    if _CONTAINER_RUNTIME_READY_CACHE is False:
        raise ContainerIsolationUnavailable(_CONTAINER_RUNTIME_READY_ERROR or "container isolation unavailable")

    try:
        _ensure_docker_available()
        _ensure_codex_home_mountable()
        _ensure_codex_agent_image()
    except ContainerIsolationUnavailable as exc:
        _CONTAINER_RUNTIME_READY_CACHE = False
        _CONTAINER_RUNTIME_READY_ERROR = str(exc)
        raise

    _CONTAINER_RUNTIME_READY_CACHE = True
    _CONTAINER_RUNTIME_READY_ERROR = None


def _ensure_docker_available() -> None:
    docker_bin = _docker_bin()
    try:
        proc = subprocess.run(
            [docker_bin, "info", "--format", "{{.ServerVersion}}"],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContainerIsolationUnavailable(f"isolation_unavailable: docker info failed: {exc}") from exc
    if proc.returncode != 0:
        detail = _tail(proc.stderr or proc.stdout, limit=600)
        raise ContainerIsolationUnavailable(f"isolation_unavailable: docker daemon unavailable: {detail}")


def _docker_bin() -> str:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise ContainerIsolationUnavailable("isolation_unavailable: docker binary not found")
    return docker_bin


def _ensure_codex_home_mountable() -> None:
    codex_home = _host_codex_home()
    missing = [name for name in ("auth.json", "config.toml") if not (codex_home / name).is_file()]
    if missing:
        joined = ", ".join(missing)
        raise ContainerIsolationUnavailable(f"isolation_unavailable: missing ~/.codex file(s): {joined}")


def _host_codex_home() -> Path:
    return (Path.home() / ".codex").resolve()


def _ensure_codex_agent_image() -> None:
    docker_bin = _docker_bin()
    try:
        inspect = subprocess.run(
            [docker_bin, "image", "inspect", _CODEX_AGENT_IMAGE],
            text=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContainerIsolationUnavailable(f"isolation_unavailable: docker image inspect failed: {exc}") from exc
    if inspect.returncode == 0:
        return

    dockerfile = _repo_root() / "scripts" / "Dockerfile.codex-agent"
    if not dockerfile.is_file():
        raise ContainerIsolationUnavailable(f"isolation_unavailable: missing {dockerfile}")
    try:
        build = subprocess.run(
            [docker_bin, "build", "-t", _CODEX_AGENT_IMAGE, "-f", str(dockerfile), str(_repo_root())],
            text=True,
            capture_output=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContainerIsolationUnavailable(f"isolation_unavailable: docker image build failed: {exc}") from exc
    if build.returncode != 0:
        detail = _tail((build.stderr or "") + "\n" + (build.stdout or ""), limit=1200)
        raise ContainerIsolationUnavailable(f"isolation_unavailable: failed to build {_CODEX_AGENT_IMAGE}: {detail}")


def _ensure_grid_container_ready() -> None:
    global _GRID_CONTAINER_READY_CACHE, _GRID_CONTAINER_READY_ERROR
    if _GRID_CONTAINER_READY_CACHE:
        return
    if _GRID_CONTAINER_READY_CACHE is False:
        raise ContainerIsolationUnavailable(_GRID_CONTAINER_READY_ERROR or "GRID API-agent container unavailable")

    try:
        _ensure_docker_available()
        _ensure_grid_agent_image()
    except ContainerIsolationUnavailable as exc:
        _GRID_CONTAINER_READY_CACHE = False
        _GRID_CONTAINER_READY_ERROR = str(exc)
        raise

    _GRID_CONTAINER_READY_CACHE = True
    _GRID_CONTAINER_READY_ERROR = None


def _ensure_grid_agent_image() -> None:
    docker_bin = _docker_bin()
    try:
        inspect = subprocess.run(
            [docker_bin, "image", "inspect", _GRID_AGENT_IMAGE],
            text=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContainerIsolationUnavailable(f"isolation_unavailable: docker image inspect failed: {exc}") from exc
    if inspect.returncode == 0:
        return

    dockerfile = _repo_root() / "scripts" / "Dockerfile.api-agent"
    if not dockerfile.is_file():
        raise ContainerIsolationUnavailable(f"isolation_unavailable: missing {dockerfile}")
    try:
        build = subprocess.run(
            [docker_bin, "build", "-t", _GRID_AGENT_IMAGE, "-f", str(dockerfile), str(_repo_root())],
            text=True,
            capture_output=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContainerIsolationUnavailable(f"isolation_unavailable: docker image build failed: {exc}") from exc
    if build.returncode != 0:
        detail = _tail((build.stderr or "") + "\n" + (build.stdout or ""), limit=1200)
        raise ContainerIsolationUnavailable(f"isolation_unavailable: failed to build {_GRID_AGENT_IMAGE}: {detail}")


def _force_remove_container(container_name: str) -> None:
    name = str(container_name or "").strip()
    if not name:
        return
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return
    try:
        subprocess.run(
            [docker_bin, "rm", "-f", name],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _is_codex_cli_command(cmd: Sequence[str]) -> bool:
    if len(cmd) < 2 or str(cmd[1]) != "exec":
        return False
    executable = Path(str(cmd[0])).name
    return executable == "codex"


def _agent_prompt(task: Mapping[str, Any], memory_context: Sequence[Mapping[str, Any]]) -> str:
    instruction = task.get("instruction") or task.get("prompt") or ""
    memory_text = _format_memory(memory_context)
    return "\n\n".join([
        "You are solving one DreamBench-SWE task in the current repository.",
        f"Task id: {task.get('id') or _task_id(task)}",
        f"Instruction:\n{instruction}",
        f"Admitted memory context:\n{memory_text}",
        "Edit the repository to satisfy the task oracle. Do not use network calls.",
    ])


def _grid_prompt(
    task: Mapping[str, Any],
    memory_context: Sequence[Mapping[str, Any]],
    files_view: str,
    transcript: Sequence[Mapping[str, Any]],
) -> str:
    previous = json.dumps(list(transcript)[-2:], indent=2, sort_keys=True)
    return "\n\n".join([
        "Produce a minimal unified diff for this DreamBench-SWE task.",
        f"Task id: {task.get('id') or _task_id(task)}",
        f"Instruction:\n{task.get('instruction') or task.get('prompt') or ''}",
        f"Memory context:\n{_format_memory(memory_context)}",
        f"Repository files:\n{files_view}",
        f"Previous attempts:\n{previous}",
        "Return only a git-apply-compatible unified diff.",
    ])


def _format_memory(items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return "(none)"
    lines = []
    for item in items:
        lines.append(
            f"- {item.get('id', 'memory')}: "
            f"{item.get('content', '')} "
            f"[status={item.get('status', 'unknown')}, type={item.get('type', 'unknown')}]"
        )
    return "\n".join(lines)


def _task_id(task: Mapping[str, Any]) -> str:
    seq_id = task.get("seq_id") or task.get("sequence_id") or "task"
    session = int(task.get("session_index") or 0)
    return f"{seq_id}-s{session:02d}" if session else str(seq_id)


def _git_diff(worktree: Path) -> str:
    proc = subprocess.run(
        ["git", "-c", "diff.external=", "diff", "--binary", "--no-ext-diff"],
        cwd=str(worktree),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout + _untracked_file_diffs(worktree)


def _codex_error_type(stderr: str) -> str:
    lowered = str(stderr or "").lower()
    if "sandbox_apply" in lowered or "operation not permitted" in lowered and "sandbox" in lowered:
        return "codex_isolation_failed"
    return "codex_exec_failed"


def _run_reaped_subprocess(
    cmd: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: Optional[float],
    env: Optional[Mapping[str, str]] = None,
    input_text: Optional[str] = None,
    container_name: str = "",
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=dict(env) if env is not None else None,
        text=True,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout_seconds)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout or "", stderr or "")
    except subprocess.TimeoutExpired:
        _force_remove_container(container_name)
        _kill_process_group(pgid)
        _reap_process(proc)
        raise
    finally:
        if proc.poll() is None:
            _kill_process_group(pgid)
            _reap_process(proc)
        else:
            _kill_process_group(pgid)


def _kill_process_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reap_process(proc: subprocess.Popen[str]) -> None:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _untracked_file_diffs(worktree: Path) -> str:
    proc = subprocess.run(
        ["git", "-c", "diff.external=", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(worktree),
        text=False,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return ""
    chunks: List[str] = []
    for raw_name in proc.stdout.split(b"\0"):
        if not raw_name:
            continue
        rel = raw_name.decode("utf-8", errors="replace")
        path = worktree / rel
        if not path.is_file() or ".git" in Path(rel).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        chunks.append(_addition_diff(rel, text))
    return "".join(chunks)


def _addition_diff(rel_path: str, text: str) -> str:
    lines = text.splitlines(keepends=True)
    body = "".join(difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{rel_path}"))
    return f"diff --git a/{rel_path} b/{rel_path}\nnew file mode 100644\n{body}"


def _show_files(worktree: Path, *, max_files: int = 80, max_bytes_per_file: int = 4000) -> str:
    chunks: List[str] = []
    skipped = {".git", "__pycache__", ".pytest_cache"}
    files = [
        path
        for path in sorted(worktree.rglob("*"))
        if path.is_file() and not any(part in skipped for part in path.relative_to(worktree).parts)
    ][:max_files]
    for path in files:
        rel = path.relative_to(worktree)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        chunks.append(f"--- {rel}\n{text[:max_bytes_per_file]}")
    return "\n\n".join(chunks)


def _extract_diff(text: str) -> str:
    fence = re.search(r"```(?:diff|patch)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    candidate = fence.group(1) if fence else text
    marker = re.search(r"(?m)^diff --git ", candidate)
    if marker:
        candidate = candidate[marker.start():]
        patch_end = re.search(r"(?m)^PATCH_END\b", candidate)
        if patch_end:
            candidate = candidate[:patch_end.start()]
        return candidate.strip() + "\n"
    return candidate.strip() + ("\n" if candidate.strip() else "")


def _parse_usage(text: str) -> Dict[str, int]:
    usage: Dict[str, int] = {}
    patterns = {
        "prompt_tokens": r"prompt[_ ]tokens[^0-9]*(\d+)",
        "completion_tokens": r"(?:completion|output)[_ ]tokens[^0-9]*(\d+)",
        "total_tokens": r"total[_ ]tokens[^0-9]*(\d+)",
    }
    lowered = text.lower()
    for key, pattern in patterns.items():
        match = re.search(pattern, lowered)
        if match:
            usage[key] = int(match.group(1))
    return usage


def _add_usage(total: Dict[str, int], usage: Mapping[str, int]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = int(total.get(key, 0)) + int(usage.get(key, 0))


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _trim_oracle(result: Mapping[str, Any], *, limit: int = 4000) -> Dict[str, Any]:
    return {
        "passed": bool(result.get("passed")),
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout") or "")[:limit],
        "stderr": str(result.get("stderr") or "")[:limit],
    }


def _redacted_cmd(cmd: Sequence[str]) -> List[str]:
    return [str(part) for part in cmd]


def _token_count(text: Any) -> int:
    return len(re.findall(r"\S+", str(text or "")))


def _tail(value: Optional[Any], *, limit: int = 1200) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


__all__ = ["CodexAgent", "GridAgent", "StubAgent", "GRID_MODELS"]
