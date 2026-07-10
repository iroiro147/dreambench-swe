#!/usr/bin/env python3
"""Loader and oracle runner for the executable DreamBench-SWE pilot tasks."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


ENV_ROOT = Path(__file__).resolve().parent
REPOS_ROOT = ENV_ROOT / "repos"
TASKS_PATH = ENV_ROOT / "tasks.jsonl"
ORACLES_ROOT = ENV_ROOT / "oracles"
DEFAULT_ORACLE_TIMEOUT_SECONDS = 120.0
GENERATED_FIXTURE_BASE_REF_COMPAT = {
    "configly": {
        "378da3f7a94032fa3aee22203423f4958303b313": "seed",
        "b065558b810cddf9bcdea29487e3b82311b84308": "seed",
    },
    "exprmini": {
        "3bde235ce45dfc2ebba08052d466e9c88d0125f4": "seed",
    },
    "todolite": {
        "a8ad576668b49d0c352f537daf5580082bd37896": "seed",
        "e2956d2a544b039689eee35840eedc374dd6a761": "seed",
    },
}
DISALLOWED_EDIT_REASON = "test_or_import_hook_edit"
GIT_EDIT_REASON = "git_metadata_edit"
ORACLE_RUNNER_SHADOW_REASON = "oracle_runner_shadow_edit"
_ORACLE_RUNNER_SHADOW_MODULES = {
    "_pytest",
    "iniconfig",
    "pluggy",
    "py",
    "pytest",
}
_PYTEST_ORACLE_RUNNER = r"""
import json
import os
import sys

payload = json.loads(sys.argv[1])
worktree = os.path.abspath(payload["worktree"])
pytest_args = list(payload["pytest_args"])
runner_cwd = os.getcwd()

clean_path = []
for entry in sys.path:
    if entry in {"", "."}:
        continue
    try:
        resolved = os.path.abspath(entry)
    except (TypeError, ValueError):
        resolved = None
    if resolved in {worktree, runner_cwd}:
        continue
    clean_path.append(entry)
sys.path[:] = clean_path

import pytest

os.chdir(worktree)
sys.path.insert(0, worktree)
raise SystemExit(pytest.main(pytest_args))
"""


def load_tasks(tasks_path: Path | str = TASKS_PATH) -> list[dict]:
    path = Path(tasks_path)
    tasks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            tasks.append(json.loads(line))
    return tasks


def load_sequence_records(records_path: Path | str) -> list[dict]:
    """Load memory-trap sequence records without replacing flat tasks.jsonl.

    The continuation slice uses one record per sequence:
    ``seq_id``, ``repo``, ``initial_commit``, ``sessions[]``, ``events[]``, and
    ``oracle_labels[]``.  Hidden oracle commands may be carried either on a
    session or in an ``oracles`` mapping keyed by ``oracle_id``; callers still
    sanitize them before constructing agent-visible task records.
    """

    path = Path(records_path)
    records = _read_json_records(path)
    return [_normalize_sequence_record(record, path=path, ordinal=index) for index, record in enumerate(records, start=1)]


def _read_json_records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("sequences"), list):
        return list(payload["sequences"])
    if isinstance(payload, dict):
        return [payload]
    raise ValueError(f"{path} must contain a JSON object, JSON list, or JSONL records")


def _normalize_sequence_record(record: dict, *, path: Path, ordinal: int) -> dict:
    if not isinstance(record, dict):
        raise ValueError(f"{path}:{ordinal} sequence record must be a JSON object")
    seq_id = str(record.get("seq_id") or record.get("sequence_id") or "").strip()
    if not seq_id:
        raise ValueError(f"{path}:{ordinal} sequence record missing seq_id")
    repo = str(record.get("repo") or "").strip()
    if not repo:
        raise ValueError(f"{path}:{ordinal} sequence record {seq_id!r} missing repo")
    initial_commit = str(record.get("initial_commit") or "").strip()
    if not initial_commit:
        raise ValueError(f"{path}:{ordinal} sequence record {seq_id!r} missing initial_commit")
    sessions = record.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError(f"{path}:{ordinal} sequence record {seq_id!r} must include non-empty sessions[]")
    events = record.get("events") or []
    if not isinstance(events, list):
        raise ValueError(f"{path}:{ordinal} sequence record {seq_id!r} events must be a list")
    oracle_labels = record.get("oracle_labels") or []
    if not isinstance(oracle_labels, list):
        raise ValueError(f"{path}:{ordinal} sequence record {seq_id!r} oracle_labels must be a list")
    oracle_cmds = _oracle_commands_by_id(record.get("oracles") or record.get("oracle_cmds") or {})

    normalized_sessions = []
    for session in sessions:
        if not isinstance(session, dict):
            raise ValueError(f"{path}:{ordinal} sequence record {seq_id!r} has a non-object session")
        session_index = int(session.get("session_index") or 0)
        if session_index <= 0:
            raise ValueError(f"{path}:{ordinal} sequence record {seq_id!r} session missing positive session_index")
        oracle_id = str(session.get("oracle_id") or f"{seq_id}-s{session_index}").strip()
        normalized = dict(session)
        normalized.update(
            {
                "seq_id": seq_id,
                "sequence_id": seq_id,
                "seq_type": record.get("seq_type") or record.get("sequence_type"),
                "repo": repo,
                "initial_commit": initial_commit,
                "session_index": session_index,
                "oracle_id": oracle_id,
            }
        )
        if "oracle_cmd" not in normalized and oracle_id in oracle_cmds:
            normalized["oracle_cmd"] = oracle_cmds[oracle_id]
        if "oracle_cmd" not in normalized:
            command = resolve_oracle_cmd(oracle_id, seq_id=seq_id, session_index=session_index)
            if command:
                normalized["oracle_cmd"] = command
        if "visible_files" in normalized and normalized["visible_files"] is None:
            normalized["visible_files"] = []
        normalized_sessions.append(normalized)

    normalized_sessions.sort(key=lambda item: int(item.get("session_index") or 0))
    normalized_record = {
        "seq_id": seq_id,
        "sequence_id": seq_id,
        "seq_type": record.get("seq_type") or record.get("sequence_type"),
        "repo": repo,
        "initial_commit": initial_commit,
        "sessions": normalized_sessions,
        "events": list(events),
        "oracle_labels": list(oracle_labels),
    }
    for metadata_key in ("validation_metadata", "authoring_metadata"):
        metadata = record.get(metadata_key)
        if isinstance(metadata, dict):
            normalized_record[metadata_key] = dict(metadata)
    return normalized_record


def _oracle_commands_by_id(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    commands: dict[str, str] = {}
    for oracle_id, spec in value.items():
        if isinstance(spec, dict):
            command = spec.get("oracle_cmd") or spec.get("cmd") or spec.get("command")
        else:
            command = spec
        if command:
            commands[str(oracle_id)] = str(command)
    return commands


def resolve_oracle_cmd(
    oracle_id: str,
    *,
    seq_id: str | None = None,
    session_index: int | None = None,
    oracles_root: Path | str = ORACLES_ROOT,
) -> str | None:
    """Resolve a sequence oracle id to the hidden pytest command, if present."""

    oracle_path = resolve_oracle_path(
        oracle_id,
        seq_id=seq_id,
        session_index=session_index,
        oracles_root=oracles_root,
    )
    if oracle_path is None or not oracle_path.exists():
        return None
    return f"{shlex.quote(sys.executable)} -m pytest -q {shlex.quote(str(oracle_path.resolve()))}"


def resolve_oracle_path(
    oracle_id: str,
    *,
    seq_id: str | None = None,
    session_index: int | None = None,
    oracles_root: Path | str = ORACLES_ROOT,
) -> Path | None:
    """Return ``oracles/<seq_id>/sN_test.py`` for a stable oracle id."""

    resolved_seq_id = str(seq_id or "").strip()
    resolved_session_index = session_index
    if not resolved_seq_id or not resolved_session_index:
        match = re.match(r"^(?P<seq_id>.+)-s(?P<session_index>0*[1-9]\d*)$", str(oracle_id or ""))
        if not match:
            return None
        resolved_seq_id = resolved_seq_id or match.group("seq_id")
        if not resolved_session_index:
            resolved_session_index = int(match.group("session_index"))
    if not resolved_seq_id or not resolved_session_index:
        return None
    return Path(oracles_root) / resolved_seq_id / f"s{int(resolved_session_index)}_test.py"


def _repo_path(repo: str | Path) -> Path:
    path = Path(repo)
    if path.exists():
        return path.resolve()
    candidate = REPOS_ROOT / str(repo)
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"unknown repo {repo!r}")


def _git(cwd: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "diff.external=", *args],
        cwd=str(cwd),
        input=input_text,
        text=True,
        capture_output=True,
    )


def materialize(repo: str | Path, base_ref: str, dest: str | Path | None = None) -> Path:
    repo_dir = _repo_path(repo)
    if dest is None:
        dest_path = Path(tempfile.mkdtemp(prefix=f"dreambench-{repo_dir.name}-"))
    else:
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(repo_dir), str(dest_path)],
        text=True,
        capture_output=True,
    )
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed: {clone.stderr}")
    resolved = _git(dest_path, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if resolved.returncode != 0:
        resolved = _git(dest_path, "rev-parse", "--verify", f"origin/{base_ref}^{{commit}}")
    if resolved.returncode != 0:
        compat_ref = _generated_fixture_compat_ref(repo_dir, str(base_ref))
        if compat_ref:
            resolved = _git(dest_path, "rev-parse", "--verify", f"{compat_ref}^{{commit}}")
    if resolved.returncode != 0:
        raise RuntimeError(f"could not resolve base_ref {base_ref!r}: {resolved.stderr}")
    commit = resolved.stdout.strip()
    checkout = _git(dest_path, "checkout", "--quiet", "--detach", commit)
    if checkout.returncode != 0:
        raise RuntimeError(f"git checkout failed: {checkout.stderr}")
    return dest_path


def _generated_fixture_compat_ref(repo_dir: Path, base_ref: str) -> str | None:
    try:
        repo_dir.resolve().relative_to(REPOS_ROOT.resolve())
    except ValueError:
        return None
    return GENERATED_FIXTURE_BASE_REF_COMPAT.get(repo_dir.name, {}).get(base_ref)


def apply_patch(worktree: str | Path, patch_text: str) -> dict:
    path = Path(worktree)
    proc = _git(path, "apply", "--whitespace=nowarn", input_text=patch_text)
    return {
        "passed": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
    }


def validate_oracle_cmd(oracle_cmd: Any) -> str:
    command = str(oracle_cmd or "").strip()
    if not command:
        raise ValueError("oracle_cmd is required and must be non-empty")
    return command


def _pytest_args_from_oracle_cmd(command: str) -> list[str] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    executable = Path(tokens[0]).name
    if executable.startswith("pytest"):
        return tokens[1:] or ["-q"]

    if not (executable.startswith("python") or executable == Path(sys.executable).name):
        return None
    for index, token in enumerate(tokens[:-1]):
        if token == "-m" and tokens[index + 1] == "pytest":
            return tokens[index + 2:] or ["-q"]
    return None


def _oracle_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _run_pytest_oracle(
    worktree: str | Path,
    pytest_args: list[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    payload = json.dumps(
        {
            "worktree": str(Path(worktree).resolve()),
            "pytest_args": pytest_args or ["-q"],
        }
    )
    with tempfile.TemporaryDirectory(prefix="dreambench-oracle-runner-") as runner_cwd:
        return subprocess.run(
            [sys.executable, "-c", _PYTEST_ORACLE_RUNNER, payload],
            cwd=runner_cwd,
            env=_oracle_env(),
            shell=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )


def run_oracle(
    worktree: str | Path,
    oracle_cmd: str,
    *,
    timeout_seconds: float = DEFAULT_ORACLE_TIMEOUT_SECONDS,
) -> dict:
    command = validate_oracle_cmd(oracle_cmd)
    try:
        pytest_args = _pytest_args_from_oracle_cmd(command)
        if pytest_args is not None:
            proc = _run_pytest_oracle(worktree, pytest_args, timeout_seconds=timeout_seconds)
        else:
            proc = subprocess.run(
                command,
                cwd=str(worktree),
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired as exc:
        return {
            "passed": False,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\noracle timed out after {timeout_seconds}s",
            "exit_code": None,
            "timed_out": True,
            "error_type": "oracle_timeout",
        }
    return {
        "passed": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "timed_out": False,
        "error_type": None if proc.returncode == 0 else "oracle_failed",
    }


def score_agent_diff(
    *,
    repo: str | Path,
    base_ref: str,
    agent_diff: str,
    oracle_cmd: str,
    scorer_root: str | Path | None = None,
    timeout_seconds: float = DEFAULT_ORACLE_TIMEOUT_SECONDS,
) -> dict:
    """Score an agent patch on a fresh checkout using production-code edits only."""

    command = validate_oracle_cmd(oracle_cmd)
    diff_info = production_diff(agent_diff)
    base_result = {
        "passed": False,
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "timed_out": False,
        "error_type": None,
        "agent_diff_files": diff_info["all_files"],
        "production_files": diff_info["production_files"],
        "stripped_paths": diff_info["stripped_paths"],
        "stripped_reasons": diff_info["stripped_reasons"],
        "rejected_files": diff_info["rejected_files"],
        "rejected_reasons": diff_info["rejected_reasons"],
        "production_diff": diff_info["production_diff"],
        "empty_production_diff": not bool(diff_info["production_diff"].strip()),
        "policy_rejected": bool(diff_info["rejected_files"]),
        "scorer_worktree": None,
        "apply": None,
    }
    if diff_info["rejected_files"]:
        reasons = [diff_info["rejected_reasons"].get(path) for path in diff_info["rejected_files"]]
        reason = next((value for value in reasons if value), GIT_EDIT_REASON)
        base_result["error_type"] = reason
        base_result["stderr"] = "agent edited scorer-sensitive path(s): " + ", ".join(diff_info["rejected_files"])
        return base_result
    if not diff_info["production_diff"].strip():
        base_result["error_type"] = "empty_production_diff"
        base_result["stderr"] = "agent produced no production-code diff"
        return base_result

    auto_root = scorer_root is None
    root = Path(scorer_root) if scorer_root is not None else Path(tempfile.mkdtemp(prefix="dreambench-scorer-"))
    scorer_dest: Path | None = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        scorer_dest = root / f"score-{uuid.uuid4().hex}"
        scorer_worktree = materialize(repo, base_ref, dest=scorer_dest)
        base_result["scorer_worktree"] = str(scorer_worktree)

        patch_result = apply_patch(scorer_worktree, diff_info["production_diff"])
        base_result["apply"] = patch_result
        if not patch_result["passed"]:
            base_result["error_type"] = "production_patch_apply_failed"
            base_result["stderr"] = patch_result.get("stderr", "")
            base_result["exit_code"] = patch_result.get("exit_code")
            return base_result

        oracle_result = run_oracle(scorer_worktree, command, timeout_seconds=timeout_seconds)
        base_result.update(oracle_result)
        return base_result
    finally:
        if scorer_dest is not None:
            shutil.rmtree(scorer_dest, ignore_errors=True)
        if auto_root:
            shutil.rmtree(root, ignore_errors=True)


def production_diff(agent_diff: str) -> dict:
    """Return the subset of a git diff that may be scored as production code."""

    production_blocks: list[str] = []
    production_files: list[str] = []
    stripped_paths: list[str] = []
    stripped_reasons: dict[str, str] = {}
    rejected_files: list[str] = []
    rejected_reasons: dict[str, str] = {}
    all_files: list[str] = []

    for block in _diff_blocks(agent_diff):
        paths = _block_paths(block)
        effective_paths = [path for path in paths if path != "/dev/null"]
        all_files.extend(effective_paths)
        git_paths = [path for path in effective_paths if _is_git_edit_path(path)]
        if git_paths:
            rejected_files.extend(git_paths)
            for path in git_paths:
                rejected_reasons[path] = GIT_EDIT_REASON
            continue
        runner_shadow_paths = [path for path in effective_paths if _is_oracle_runner_shadow_path(path)]
        if runner_shadow_paths:
            rejected_files.extend(runner_shadow_paths)
            for path in runner_shadow_paths:
                rejected_reasons[path] = ORACLE_RUNNER_SHADOW_REASON
            continue
        unscored_paths = [path for path in effective_paths if _is_stripped_edit_path(path)]
        if unscored_paths:
            stripped_paths.extend(unscored_paths)
            for path in unscored_paths:
                stripped_reasons[path] = DISALLOWED_EDIT_REASON
            continue
        production_blocks.append(block)
        production_files.extend(effective_paths)

    return {
        "production_diff": "".join(production_blocks),
        "production_files": _dedupe(production_files),
        "stripped_paths": _dedupe(stripped_paths),
        "stripped_reasons": stripped_reasons,
        "rejected_files": _dedupe(rejected_files),
        "rejected_reasons": rejected_reasons,
        "all_files": _dedupe(all_files),
    }


def _diff_blocks(patch_text: str) -> list[str]:
    lines = patch_text.splitlines(keepends=True)
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return ["".join(block) for block in blocks]


def _block_paths(block: str) -> list[str]:
    paths: list[str] = []
    header = re.search(r"^diff --git a/(.*?) b/(.*?)$", block, flags=re.MULTILINE)
    if header:
        paths.extend([header.group(1), header.group(2)])
    for marker in ("---", "+++"):
        for match in re.finditer(rf"^{re.escape(marker)}\s+([^\n]+)$", block, flags=re.MULTILINE):
            value = match.group(1).strip()
            if value == "/dev/null":
                paths.append(value)
                continue
            if value.startswith("a/") or value.startswith("b/"):
                paths.append(value[2:])
    return _dedupe(_normalize_diff_path(path) for path in paths if path)


def _normalize_diff_path(path: str) -> str:
    value = path.strip().strip('"')
    if value == "/dev/null":
        return value
    return str(Path(value))


def _is_git_edit_path(path: str) -> bool:
    normalized = _normalize_diff_path(path)
    if normalized == "/dev/null":
        return False
    parts = Path(normalized).parts
    return ".git" in parts


def _is_oracle_runner_shadow_path(path: str) -> bool:
    normalized = _normalize_diff_path(path)
    if normalized == "/dev/null":
        return False
    parts = Path(normalized).parts
    if not parts:
        return False
    top_level = parts[0]
    if top_level in _ORACLE_RUNNER_SHADOW_MODULES:
        return True
    if top_level.endswith(".py") and top_level[:-3] in _ORACLE_RUNNER_SHADOW_MODULES:
        return True
    return False


def _is_stripped_edit_path(path: str) -> bool:
    normalized = _normalize_diff_path(path)
    if normalized == "/dev/null":
        return False
    parts = Path(normalized).parts
    name = Path(normalized).name
    if parts and parts[0] == "tests":
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    if name in {"conftest.py", "pytest.ini", "sitecustomize.py"}:
        return True
    if name.endswith(".pth"):
        return True
    return False


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def validate_task(task: dict) -> tuple[bool, str]:
    base_dir = materialize(task["repo"], task["base_ref"])
    try:
        base_result = run_oracle(base_dir, task["oracle_cmd"])
        if base_result["passed"]:
            return False, "base oracle unexpectedly passed"
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)

    patched_dir = materialize(task["repo"], task["base_ref"])
    try:
        patch_result = apply_patch(patched_dir, task["reference_patch"])
        if not patch_result["passed"]:
            return False, "reference patch failed to apply: " + patch_result["stderr"]
        patched_result = run_oracle(patched_dir, task["oracle_cmd"])
        if not patched_result["passed"]:
            return False, (
                "patched oracle failed\nstdout:\n"
                + patched_result["stdout"]
                + "\nstderr:\n"
                + patched_result["stderr"]
            )
    finally:
        shutil.rmtree(patched_dir, ignore_errors=True)
    return True, "ok"


def main() -> int:
    tasks = load_tasks()
    failures = []
    for index, task in enumerate(tasks, start=1):
        ok, message = validate_task(task)
        label = f"{task['seq_id']}[s{task['session_index']:02d}] {task['repo']}"
        if ok:
            print(f"ok {index:02d} {label}")
        else:
            print(f"FAIL {index:02d} {label}: {message}")
            failures.append((task, message))
    print(f"validated {len(tasks) - len(failures)}/{len(tasks)} tasks")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
