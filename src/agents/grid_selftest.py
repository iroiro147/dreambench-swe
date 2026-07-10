"""GRID wake-agent connectivity selftest.

Run from the repository root with:

    python3 -m agents.grid_selftest

The selftest deliberately avoids printing secret values.  It exercises the same
Docker image, env propagation, worktree mount, network mode, and loopback URL
translation used by ``GridAgent`` before making a live ``/models`` request.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from agents import llm_agents


_CONTAINER_ECHO_SCRIPT = r"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

tools = {
    "python3": shutil.which("python3"),
    "git": shutil.which("git"),
    "curl": shutil.which("curl"),
}
versions = {}
for name in ("python3", "git"):
    path = tools.get(name)
    if not path:
        continue
    proc = subprocess.run([path, "--version"], text=True, capture_output=True)
    versions[name] = (proc.stdout or proc.stderr).strip()

marker_path = Path("/work/grid-selftest.txt")
payload = {
    "cwd": os.getcwd(),
    "grid_base_url": os.environ.get("GRID_BASE_URL", ""),
    "grid_api_key_present": bool(os.environ.get("GRID_API_KEY")),
    "grid_model": os.environ.get("GRID_MODEL", ""),
    "timeout_seconds": os.environ.get("GRID_TIMEOUT_SECONDS", ""),
    "marker": marker_path.read_text(encoding="utf-8") if marker_path.exists() else None,
    "tools": tools,
    "versions": versions,
    "python_executable": sys.executable,
}
print(json.dumps(payload, sort_keys=True))
""".strip()


_CONTAINER_MODELS_SCRIPT = r"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

base_url = os.environ["GRID_BASE_URL"].rstrip("/")
api_key = os.environ["GRID_API_KEY"]
timeout = float(os.environ.get("GRID_TIMEOUT_SECONDS") or "90")
request = urllib.request.Request(
    base_url + "/models",
    headers={"Authorization": "Bearer " + api_key},
    method="GET",
)
try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        payload = {"status": response.status, "body_excerpt": body[:1000]}
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}
        models = data.get("data") if isinstance(data, dict) else None
        if isinstance(models, list):
            payload["model_count"] = len(models)
            payload["model_ids"] = [str(item.get("id")) for item in models[:10] if isinstance(item, dict)]
        print(json.dumps(payload, sort_keys=True))
except urllib.error.HTTPError as exc:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    detail = "HTTP " + str(exc.code)
    if getattr(exc, "reason", None):
        detail += " " + str(exc.reason)
    if body:
        detail += ": " + body[:1200]
    sys.stderr.write(detail)
    raise SystemExit(1)
except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
    sys.stderr.write(type(exc).__name__ + ": " + str(exc))
    raise SystemExit(1)
""".strip()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    use_container = _use_container(args)
    model = llm_agents._normalize_grid_model(args.model)
    secrets_path = Path(args.secrets_path).expanduser()

    ok = True
    config = _load_config(secrets_path)
    env_ok = _check_env(config=config, secrets_path=secrets_path, use_container=use_container, model=model)
    ok = ok and env_ok
    if not env_ok:
        return 1

    with tempfile.TemporaryDirectory(prefix="grid-selftest-") as tmp:
        worktree = Path(tmp)
        (worktree / "grid-selftest.txt").write_text("mounted-ok\n", encoding="utf-8")
        echo_ok = _container_echo(config=config, model=model, timeout_seconds=args.timeout_seconds, worktree=worktree)
        ok = ok and echo_ok
        models_ok = _models_request(
            config=config,
            model=model,
            timeout_seconds=args.timeout_seconds,
            worktree=worktree,
            use_container=use_container,
        )
        ok = ok and models_ok

    return 0 if ok else 1


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Selftest the DreamBench GRID wake-agent path.")
    parser.add_argument("--model", default=os.environ.get("DREAMBENCH_GRID_MODEL", "glm-latest"))
    parser.add_argument(
        "--secrets-path",
        default=str(llm_agents._repo_root() / "experiments" / "secrets.env"),
        help="Path to GRID_BASE_URL/GRID_API_KEY env file.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--container", action="store_true", help="Force the live /models request through Docker.")
    mode.add_argument("--host", action="store_true", help="Force the live /models request from the host.")
    return parser.parse_args(list(argv) if argv is not None else None)


def _use_container(args: argparse.Namespace) -> bool:
    if args.container:
        return True
    if args.host:
        return False
    return bool(llm_agents._GRID_AGENT_CONTAINER_DEFAULT)


def _load_config(secrets_path: Path) -> Dict[str, Any]:
    file_values = llm_agents._read_env_file(secrets_path)
    base_url = file_values.get("GRID_BASE_URL") or os.environ.get("GRID_BASE_URL") or ""
    api_key = file_values.get("GRID_API_KEY") or os.environ.get("GRID_API_KEY") or ""
    return {
        "base_url": base_url,
        "api_key": api_key,
        "base_url_source": "file" if file_values.get("GRID_BASE_URL") else "env" if os.environ.get("GRID_BASE_URL") else "missing",
        "api_key_source": "file" if file_values.get("GRID_API_KEY") else "env" if os.environ.get("GRID_API_KEY") else "missing",
        "secrets_file_exists": secrets_path.exists(),
    }


def _check_env(*, config: Mapping[str, Any], secrets_path: Path, use_container: bool, model: str) -> bool:
    missing = [name for name in ("base_url", "api_key") if not config.get(name)]
    if missing:
        _print_result(
            "host env/file check",
            False,
            f"missing {', '.join(missing)}; secrets_path={secrets_path}",
        )
        return False
    container_base_url, container_network, docker_extra_args, loopback_strategy = llm_agents._grid_container_base_url(
        str(config["base_url"])
    )
    detail = (
        f"secrets_file_exists={config['secrets_file_exists']} "
        f"base_url_source={config['base_url_source']} api_key_source={config['api_key_source']} "
        f"model={model} use_container={use_container} "
        f"container_network={container_network} loopback_strategy={loopback_strategy} "
        f"container_base_url_host={_netloc(container_base_url)} "
        f"docker_extra_args={docker_extra_args}"
    )
    _print_result("host env/file check", True, detail)
    return True


def _container_echo(
    *,
    config: Mapping[str, Any],
    model: str,
    timeout_seconds: float,
    worktree: Path,
) -> bool:
    try:
        llm_agents._ensure_grid_container_ready()
        invocation = llm_agents._grid_container_invocation(
            base_url=str(config["base_url"]),
            api_key=str(config["api_key"]),
            model=model,
            worktree=worktree,
            timeout_seconds=timeout_seconds,
            command=["python3", "-c", _CONTAINER_ECHO_SCRIPT],
        )
        proc = llm_agents._run_reaped_subprocess(
            invocation.cmd,
            cwd=invocation.cwd,
            timeout_seconds=max(timeout_seconds, 20.0),
            env=invocation.env,
            input_text="",
            container_name=str(invocation.metadata.get("container_name") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic command.
        _print_result("container echo", False, _raw_exception(exc))
        return False

    if proc.returncode != 0:
        _print_result("container echo", False, _tail((proc.stderr or "") + "\n" + (proc.stdout or "")))
        return False
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        _print_result("container echo", False, f"malformed JSON: {_tail(proc.stdout)}")
        return False

    checks = [
        payload.get("cwd") == "/work",
        payload.get("grid_api_key_present") is True,
        payload.get("marker") == "mounted-ok\n",
        bool(payload.get("tools", {}).get("python3")),
        bool(payload.get("tools", {}).get("git")),
    ]
    detail = (
        f"cwd={payload.get('cwd')} marker={payload.get('marker')!r} "
        f"grid_base_url_host={_netloc(str(payload.get('grid_base_url') or ''))} "
        f"tools={payload.get('tools')} versions={payload.get('versions')}"
    )
    _print_result("container echo", all(checks), detail)
    return all(checks)


def _models_request(
    *,
    config: Mapping[str, Any],
    model: str,
    timeout_seconds: float,
    worktree: Path,
    use_container: bool,
) -> bool:
    if use_container:
        return _models_request_container(
            config=config,
            model=model,
            timeout_seconds=timeout_seconds,
            worktree=worktree,
        )
    return _models_request_host(config=config, timeout_seconds=timeout_seconds)


def _models_request_host(*, config: Mapping[str, Any], timeout_seconds: float) -> bool:
    endpoint = str(config["base_url"]).rstrip("/") + "/models"
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": "Bearer " + str(config["api_key"])},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
    except urllib.error.HTTPError as exc:
        _print_result("models request", False, "host: " + llm_agents._http_error_detail(exc))
        return False
    except (urllib.error.URLError, socket.timeout, TimeoutError, json.JSONDecodeError) as exc:
        _print_result("models request", False, "host: " + _raw_exception(exc))
        return False

    models = data.get("data") if isinstance(data, Mapping) else None
    count = len(models) if isinstance(models, list) else "unknown"
    _print_result("models request", True, f"host endpoint_host={_netloc(endpoint)} model_count={count}")
    return True


def _models_request_container(
    *,
    config: Mapping[str, Any],
    model: str,
    timeout_seconds: float,
    worktree: Path,
) -> bool:
    try:
        llm_agents._ensure_grid_container_ready()
        invocation = llm_agents._grid_container_invocation(
            base_url=str(config["base_url"]),
            api_key=str(config["api_key"]),
            model=model,
            worktree=worktree,
            timeout_seconds=timeout_seconds,
            command=["python3", "-c", _CONTAINER_MODELS_SCRIPT],
        )
        proc = llm_agents._run_reaped_subprocess(
            invocation.cmd,
            cwd=invocation.cwd,
            timeout_seconds=max(timeout_seconds, 20.0),
            env=invocation.env,
            input_text="",
            container_name=str(invocation.metadata.get("container_name") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic command.
        _print_result("models request", False, "container: " + _raw_exception(exc))
        return False

    if proc.returncode != 0:
        _print_result("models request", False, "container: " + _tail((proc.stderr or "") + "\n" + (proc.stdout or "")))
        return False
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        _print_result("models request", False, "container malformed JSON: " + _tail(proc.stdout))
        return False

    detail = (
        f"container endpoint_host={invocation.metadata.get('container_base_url_host')} "
        f"rewritten={invocation.metadata.get('base_url_rewritten_for_container')} "
        f"model_count={payload.get('model_count', 'unknown')} "
        f"model_ids={payload.get('model_ids', [])}"
    )
    _print_result("models request", True, detail)
    return True


def _print_result(name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"{status} {name}: {detail}")


def _raw_exception(exc: BaseException) -> str:
    return _tail(f"{type(exc).__name__}: {exc}")


def _tail(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[-limit:]


def _netloc(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


if __name__ == "__main__":
    sys.exit(main())
