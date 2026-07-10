"""Unit tests for the GRID wake-agent adapter."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agents import llm_agents  # noqa: E402
from experiments import run_bench  # noqa: E402


TEST_GRID_KEY = "test" + "-grid" + "-key"


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _GridEndpointStub:
    def __init__(self, payload: dict[str, Any] | None = None, *, exc: BaseException | None = None) -> None:
        self.payload = payload or {}
        self.exc = exc
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: float) -> _Response:
        self.requests.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        if self.exc is not None:
            raise self.exc
        return _Response(self.payload)


def test_agent_for_grid_model_routes_to_grid_agent() -> None:
    grid_agent = run_bench._agent_for_model("grid:glm-latest")
    codex_agent = run_bench._agent_for_model("gpt-5.5")

    assert isinstance(grid_agent, llm_agents.GridAgent)
    assert grid_agent.model == "grid:glm-latest"
    assert grid_agent.provider_model == "glm-latest"
    assert isinstance(codex_agent, llm_agents.CodexAgent)
    assert codex_agent.model == "gpt-5.5"


def test_grid_agent_success_applies_stubbed_http_patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path)
    patch = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    response = {
        "choices": [{"message": {"content": patch}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }

    stub = _GridEndpointStub(response)
    monkeypatch.setattr(llm_agents.urllib.request, "urlopen", stub)
    agent = llm_agents.GridAgent(
        "grid:glm-latest",
        secrets_path=_secrets_file(tmp_path),
        timeout_seconds=1.0,
        use_container=False,
    )
    result = agent.run_task({"id": "grid-success", "worktree": str(worktree), "prompt": "Change app.py."}, [])

    assert result.passed is True
    assert result.error_type is None
    assert result.tokens == 18
    assert "+value = 2" in result.patch
    assert (worktree / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert agent.last_run["isolation_mode"] == "host"
    assert agent.last_run["auth_material"]["env"] == ["GRID_BASE_URL", "GRID_API_KEY"]
    assert agent.last_run["auth_material"]["mounted"] == []
    assert stub.requests[0]["url"] == "https://grid.example.test/v1/chat/completions"
    assert stub.requests[0]["authorization"] in {"Bearer " + TEST_GRID_KEY, "Bearer " + "<REDACTED>"}
    assert stub.requests[0]["body"]["model"] == "glm-latest"
    assert stub.requests[0]["body"]["max_tokens"] == 4096
    assert "--- a/path" in stub.requests[0]["body"]["messages"][0]["content"]


def test_grid_agent_timeout_default_is_300(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DREAMBENCH_GRID_TIMEOUT", raising=False)

    agent = llm_agents.GridAgent("grid:glm-latest", secrets_path=Path("unused"), use_container=False)

    assert agent.timeout_seconds == 300.0


def test_headerless_single_file_patch_round_trips_through_git_apply(tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path)
    patch = """diff --git a/app.py b/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

    result = llm_agents._apply_grid_patch(worktree, patch)

    assert result["passed"] is True
    assert result["strategy"] == "git_apply"
    assert result["normalization"]["synthesized_header_count"] == 1
    assert result["normalization"]["synthesized_headers"][0]["old"] == "a/app.py"
    assert result["normalization"]["synthesized_headers"][0]["new"] == "b/app.py"
    assert (worktree / "app.py").read_text(encoding="utf-8") == "value = 2\n"


def test_headerless_multi_file_patch_round_trips_through_git_apply(tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path, files={"app.py": "value = 1\n", "lib.py": "name = 'old'\n"})
    patch = """diff --git a/app.py b/app.py
@@ -1 +1 @@
-value = 1
+value = 2
diff --git a/lib.py b/lib.py
@@ -1 +1 @@
-name = 'old'
+name = 'new'
"""

    result = llm_agents._apply_grid_patch(worktree, patch)

    assert result["passed"] is True
    assert result["normalization"]["synthesized_header_count"] == 2
    assert (worktree / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (worktree / "lib.py").read_text(encoding="utf-8") == "name = 'new'\n"


def test_headerless_new_file_patch_uses_dev_null_header(tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path)
    patch = """diff --git a/new_module.py b/new_module.py
new file mode 100644
@@ -0,0 +1,2 @@
+created = True
+answer = 42
"""

    result = llm_agents._apply_grid_patch(worktree, patch)

    assert result["passed"] is True
    assert result["normalization"]["synthesized_headers"][0]["old"] == "/dev/null"
    assert result["normalization"]["synthesized_headers"][0]["new"] == "b/new_module.py"
    assert (worktree / "new_module.py").read_text(encoding="utf-8") == "created = True\nanswer = 42\n"


def test_already_valid_patch_normalization_is_idempotent_and_applies(tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path)
    patch = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-value = 1
+value = 4
"""

    normalized, metadata = llm_agents._normalize_grid_patch_headers(patch)
    result = llm_agents._apply_grid_patch(worktree, patch)

    assert normalized == patch
    assert metadata["changed"] is False
    assert metadata["synthesized_header_count"] == 0
    assert result["passed"] is True
    assert result["normalization"]["synthesized_header_count"] == 0
    assert (worktree / "app.py").read_text(encoding="utf-8") == "value = 4\n"


def test_headerless_deleted_file_patch_uses_dev_null_header(tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path, files={"app.py": "value = 1\n", "remove_me.py": "obsolete = True\n"})
    patch = """diff --git a/remove_me.py b/remove_me.py
deleted file mode 100644
@@ -1 +0,0 @@
-obsolete = True
"""

    result = llm_agents._apply_grid_patch(worktree, patch)

    assert result["passed"] is True
    assert result["normalization"]["synthesized_headers"][0]["old"] == "a/remove_me.py"
    assert result["normalization"]["synthesized_headers"][0]["new"] == "/dev/null"
    assert not (worktree / "remove_me.py").exists()


def test_grid_agent_applies_exact_headerless_glm_sample(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path, files={"configly/parser.py": _configly_parser_source()})
    sample = """PATCH_START ord=1
diff --git a/configly/parser.py b/configly/parser.py
@@ -34,7 +34,9 @@
-def dump_ini(config):
+def dump_ini(config, sort_keys=False):
     lines = []
-    for section, values in config.items():
+    sections = sorted(config.items()) if sort_keys else config.items()
+    for section, values in sections:
         lines.append(f'[{section}]')
-        for key, value in values.items():
+        items = sorted(values.items()) if sort_keys else values.items()
+        for key, value in items:
             lines.append(f'{key}={value}')
     return '\\n'.join(lines) + ('\\n' if lines else '')

PATCH_END
"""
    response = {
        "choices": [{"message": {"content": sample}}],
        "usage": {"prompt_tokens": 101, "completion_tokens": 55, "total_tokens": 156},
    }
    stub = _GridEndpointStub(response)
    monkeypatch.setattr(llm_agents.urllib.request, "urlopen", stub)
    agent = llm_agents.GridAgent(
        "grid:glm-latest",
        secrets_path=_secrets_file(tmp_path),
        timeout_seconds=1.0,
        use_container=False,
    )

    result = agent.run_task({"id": "grid-headerless-sample", "worktree": str(worktree), "prompt": "Sort INI output."}, [])

    assert result.passed is True
    assert result.patch.strip()
    assert "--- a/configly/parser.py" in result.patch
    assert "+++ b/configly/parser.py" in result.patch
    assert "def dump_ini(config, sort_keys=False):" in (worktree / "configly/parser.py").read_text(encoding="utf-8")
    apply_result = agent.last_run["steps"][0]["apply"]
    assert apply_result["strategy"] == "git_apply"
    assert apply_result["normalization"]["synthesized_header_count"] == 1
    assert agent.last_run["diff"] == result.patch


def test_grid_agent_timeout_returns_grid_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path)
    stub = _GridEndpointStub(exc=socket.timeout("stub timeout"))
    monkeypatch.setattr(llm_agents.urllib.request, "urlopen", stub)
    agent = llm_agents.GridAgent(
        "grid:glm-latest",
        secrets_path=_secrets_file(tmp_path),
        timeout_seconds=0.05,
        use_container=False,
    )
    result = agent.run_task({"id": "grid-timeout", "worktree": str(worktree), "prompt": "No-op."}, [])

    assert result.passed is False
    assert result.error_type == "grid_timeout"
    assert agent.last_run["outcome"] == "timeout"
    assert agent.last_run["error_type"] == "grid_timeout"


def test_grid_agent_http_error_detail_is_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    worktree = _git_worktree(tmp_path)
    http_error = urllib.error.HTTPError(
        "https://grid.example.test/v1/chat/completions",
        401,
        "Unauthorized",
        {},
        _BytesBody(b'{"error":"bad test token"}'),
    )
    stub = _GridEndpointStub(exc=http_error)
    monkeypatch.setattr(llm_agents.urllib.request, "urlopen", stub)
    agent = llm_agents.GridAgent(
        "grid:glm-latest",
        secrets_path=_secrets_file(tmp_path),
        timeout_seconds=1.0,
        use_container=False,
    )
    result = agent.run_task({"id": "grid-http-error", "worktree": str(worktree), "prompt": "No-op."}, [])

    assert result.passed is False
    assert result.error_type == "grid_exec_failed"
    assert result.error_detail is not None
    assert "HTTP 401" in result.error_detail
    assert "bad test token" in result.error_detail
    assert agent.last_run["failure_reason"].startswith("RuntimeError: GRID request failed: HTTP 401")


def test_grid_agent_container_rewrites_loopback_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worktree = _git_worktree(tmp_path)
    patch = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-value = 1
+value = 3
"""
    response = {
        "choices": [{"message": {"content": patch}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11},
    }
    seen: dict[str, Any] = {}

    def fake_run_reaped_subprocess(cmd, *, cwd, timeout_seconds, env, input_text=None, container_name=""):
        seen["cmd"] = list(cmd)
        seen["cwd"] = cwd
        seen["timeout_seconds"] = timeout_seconds
        seen["env"] = dict(env)
        seen["input_text"] = input_text
        seen["container_name"] = container_name
        network_index = seen["cmd"].index("--network") + 1
        seen["network"] = seen["cmd"][network_index]
        if "127.0.0.1" in seen["env"]["GRID_BASE_URL"] and seen["network"] != "host":
            return subprocess.CompletedProcess(cmd, 1, "", "connection refused inside container")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(response), "")

    monkeypatch.setattr(llm_agents, "_ensure_grid_container_ready", lambda: None)
    monkeypatch.setattr(llm_agents, "_docker_bin", lambda: "docker")
    monkeypatch.setattr(llm_agents, "_run_reaped_subprocess", fake_run_reaped_subprocess)
    monkeypatch.setattr(llm_agents.sys, "platform", "linux")

    agent = llm_agents.GridAgent(
        "grid:glm-latest",
        secrets_path=_secrets_file(tmp_path, base_url="http://127.0.0.1:4321/v1"),
        timeout_seconds=1.0,
        use_container=True,
    )
    result = agent.run_task({"id": "grid-loopback", "worktree": str(worktree), "prompt": "Change app.py."}, [])

    assert result.passed is True
    assert seen["network"] == "host"
    assert seen["env"]["GRID_BASE_URL"] == "http://127.0.0.1:4321/v1"
    assert "--add-host" not in seen["cmd"]
    assert agent.last_run["request"]["base_url_rewritten_for_container"] is False
    assert agent.last_run["request"]["loopback_strategy"] == "linux-host-network"
    assert agent.last_run["request"]["container_base_url_host"] == "127.0.0.1:4321"
    assert (worktree / "app.py").read_text(encoding="utf-8") == "value = 3\n"


def test_grid_agent_malformed_response_returns_grid_exec_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worktree = _git_worktree(tmp_path)
    response = {"choices": []}

    stub = _GridEndpointStub(response)
    monkeypatch.setattr(llm_agents.urllib.request, "urlopen", stub)
    agent = llm_agents.GridAgent(
        "grid:kimi-latest",
        secrets_path=_secrets_file(tmp_path),
        timeout_seconds=1.0,
        use_container=False,
    )
    result = agent.run_task({"id": "grid-malformed", "worktree": str(worktree), "prompt": "No-op."}, [])

    assert result.passed is False
    assert result.error_type == "grid_exec_failed"
    assert result.error_detail is not None
    assert "choices" in result.error_detail
    assert agent.last_run["outcome"] == "agent_error"
    assert agent.last_run["error_type"] == "grid_exec_failed"
    assert "choices" in agent.last_run["failure_reason"]


class _BytesBody:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        return None


def _secrets_file(tmp_path: Path, *, base_url: str = "https://grid.example.test/v1") -> Path:
    path = tmp_path / "secrets.env"
    path.write_text(f"GRID_BASE_URL={base_url}\nGRID_API_KEY={TEST_GRID_KEY}\n", encoding="utf-8")
    return path


def _configly_parser_source() -> str:
    padding = [f"# filler {index}\n" for index in range(1, 34)]
    body = [
        "def dump_ini(config):\n",
        "    lines = []\n",
        "    for section, values in config.items():\n",
        "        lines.append(f'[{section}]')\n",
        "        for key, value in values.items():\n",
        "            lines.append(f'{key}={value}')\n",
        "    return '\\n'.join(lines) + ('\\n' if lines else '')\n",
    ]
    return "".join([*padding, *body])


def _git_worktree(tmp_path: Path, *, files: dict[str, str] | None = None) -> Path:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    file_map = files or {"app.py": "value = 1\n"}
    for rel_path, content in file_map.items():
        path = worktree / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _run(["git", "init"], worktree)
    _run(["git", "add", "."], worktree)
    _run(
        [
            "git",
            "-c",
            "user.name=DreamBench Test",
            "-c",
            "user.email=dreambench@example.test",
            "commit",
            "-m",
            "init",
        ],
        worktree,
    )
    return worktree


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=True)


def _run_all() -> bool:
    raise SystemExit("use pytest for test_grid_wake_agent.py")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
