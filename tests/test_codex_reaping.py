"""Regression coverage for CodexAgent subprocess timeout reaping."""
from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agents import llm_agents  # noqa: E402


def test_codex_timeout_kills_own_process_group_and_children() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        worktree = root / "worktree"
        worktree.mkdir()
        pid_file = root / "pids.json"
        launcher = root / "long_running_codex_stand_in.py"
        _write_long_running_launcher(launcher)

        old_args = llm_agents.CODEX_ARGS
        llm_agents.CODEX_ARGS = [sys.executable, str(launcher), str(pid_file)]
        pids: dict[str, int] = {}
        try:
            agent = llm_agents.CodexAgent(timeout_seconds=0.75)
            result = agent.run_task({
                "id": "reaping-regression",
                "worktree": str(worktree),
                "prompt": "Trigger a timeout in the stand-in codex child.",
            }, [])

            pids = json.loads(pid_file.read_text(encoding="utf-8"))
            assert result.passed is False
            assert result.error_type == "codex_timeout"
            assert agent.last_run["error_type"] == "codex_timeout"
            assert _wait_for_pid_exit(pids["parent_pid"])
            assert _wait_for_pid_exit(pids["child_pid"]), "grandchild survived CodexAgent timeout cleanup"
            assert pids["parent_pgid"] == pids["parent_pid"]
            assert pids["child_pgid"] == pids["parent_pgid"]
        finally:
            llm_agents.CODEX_ARGS = old_args
            _kill_leftovers(pids)


def _write_long_running_launcher(path: Path) -> None:
    path.write_text(
        """
from __future__ import annotations

import json
import os
import subprocess
import sys
import time


pid_file = sys.argv[1]
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
payload = {
    "parent_pid": os.getpid(),
    "parent_pgid": os.getpgrp(),
    "child_pid": child.pid,
    "child_pgid": os.getpgid(child.pid),
}
with open(pid_file, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)

while True:
    time.sleep(1)
""".lstrip(),
        encoding="utf-8",
    )


def _wait_for_pid_exit(pid: int, *, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_leftovers(pids: dict[str, int]) -> None:
    for key in ("child_pid", "parent_pid"):
        pid = pids.get(key)
        if not pid or not _pid_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _wait_for_pid_exit(pid, seconds=1.0)


if __name__ == "__main__":
    test_codex_timeout_kills_own_process_group_and_children()
    print("test_codex_reaping.py: all tests passed")
