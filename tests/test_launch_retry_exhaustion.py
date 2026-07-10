"""The published live-rerun launchers must fail closed after retry exhaustion."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = (
    ("ops/launch_confirmatory.sh", "CONFIRM_COMPLETE"),
    ("ops/launch_synth.sh", "SYNTH_COMPLETE"),
)


@pytest.mark.parametrize(("launcher", "complete_marker"), LAUNCHERS)
def test_retry_exhaustion_returns_failure_without_complete_marker(
    tmp_path: Path,
    launcher: str,
    complete_marker: str,
) -> None:
    result, calls = run_launcher(tmp_path, launcher, exit_code=7)

    assert result.returncode == 7
    assert complete_marker not in result.stdout
    assert "RETRY_EXHAUSTED rc=7 tries=2" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == ["python3", "python3"]


@pytest.mark.parametrize(("launcher", "complete_marker"), LAUNCHERS)
def test_success_emits_complete_marker(
    tmp_path: Path,
    launcher: str,
    complete_marker: str,
) -> None:
    result, calls = run_launcher(tmp_path, launcher, exit_code=0)

    assert result.returncode == 0
    assert complete_marker in result.stdout
    assert "RETRY_EXHAUSTED" not in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == ["python3", "python3"]


def run_launcher(tmp_path: Path, launcher: str, *, exit_code: int) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.txt"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\nprintf 'python3\\n' >> \"$DREAMBENCH_TEST_CALLS\"\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    work_root = tmp_path / "work"
    (work_root / "experiments").mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "DREAMBENCH_ROOT": str(work_root),
            "DREAMBENCH_MAX_ATTEMPTS": "2",
            "DREAMBENCH_RETRY_DELAY_SECONDS": "0",
            "DREAMBENCH_TEST_CALLS": str(calls),
        }
    )
    result = subprocess.run(
        ["bash", str(ROOT / launcher)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result, calls
