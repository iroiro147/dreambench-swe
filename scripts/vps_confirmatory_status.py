#!/usr/bin/env python3
"""Read-only VPS status snapshot for the Paper A v2 confirmatory fold."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = "root@157.90.114.41"
DEFAULT_REMOTE_ROOT = "<LOCAL_ROOT>"
DEFAULT_RUN_STAMP = "20260706T074759Z"
DEFAULT_EXPECTED_COUNT = 1890

REMAINING_GATES = (
    "NONLIVE_GRID_DONE rc=0",
    "LIVE_GRID_DONE rc=0",
    "no nonzero driver markers",
    "no RUN_GRID FINISH rc>0",
    "exactly 1890 condition-level result files",
    "no active run_grid driver for this run stamp",
    "rsync results/logs",
    "completion dry plans with zero RUN lines",
    "completion checker STATUS PASS failures=0",
    "canonical analyzers",
    "interpretation",
    "paper re-anchor",
    "package validator and freshness gates",
)


@dataclass(frozen=True)
class ConfirmatoryStatus:
    utc: str | None
    head: str | None
    sequence_sha256: str | None
    condition_group_count: int | None
    by_condition: dict[str, int]
    nonzero_finish_count: int | None
    disk_headroom: list[str]
    inode_headroom: list[str]
    done_markers: list[str]
    tail: list[str]

    @property
    def done_marker_status(self) -> str:
        if not self.done_markers:
            return "none"
        return "; ".join(self.done_markers)


def remote_snapshot_command(remote_root: str, run_stamp: str) -> str:
    root = shlex.quote(remote_root)
    run = shlex.quote(run_stamp)
    return textwrap.dedent(
        f"""
        cd {root}
        RUN={run}
        RESULT=experiments/results/v2-confirmatory-$RUN
        LOG=logs/grid/v2-confirmatory-$RUN
        printf "UTC "
        date -u +%Y-%m-%dT%H:%M:%SZ
        printf "HEAD "
        git rev-parse HEAD
        printf "SEQ_SHA256 "
        sha256sum experiments/env/sequences_confirmatory_v2.jsonl | awk '{{print $1}}'
        printf "COND_GROUP_COUNT "
        find "$RESULT" -mindepth 3 -maxdepth 3 -name results.json | wc -l
        printf "BY_CONDITION\\n"
        find "$RESULT" -mindepth 3 -maxdepth 3 -name results.json -print | awk -F/ '{{print $(NF-1)}}' | sort | uniq -c | sort -k2
        printf "NONZERO_FINISH_COUNT "
        (grep -E "FINISH .* rc=[1-9]" "$LOG/RUN_GRID.log" 2>/dev/null || true) | wc -l
        printf "DISK_HEADROOM\\n"
        df -h "$RESULT" "$LOG" . 2>/dev/null | awk 'NR==1 || !seen[$1 " " $6]++'
        printf "INODE_HEADROOM\\n"
        df -i "$RESULT" "$LOG" . 2>/dev/null | awk 'NR==1 || !seen[$1 " " $6]++'
        printf "DONE_MARKERS\\n"
        grep "_GRID_DONE" "$LOG"/*.driver.log 2>/dev/null || true
        printf "TAIL\\n"
        tail -n 24 "$LOG/RUN_GRID.log"
        """
    ).strip()


def parse_snapshot(text: str) -> ConfirmatoryStatus:
    utc: str | None = None
    head: str | None = None
    sequence_sha256: str | None = None
    condition_group_count: int | None = None
    by_condition: dict[str, int] = {}
    nonzero_finish_count: int | None = None
    disk_headroom: list[str] = []
    inode_headroom: list[str] = []
    done_markers: list[str] = []
    tail: list[str] = []
    section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("UTC "):
            utc = line.removeprefix("UTC ").strip()
            section = None
            continue
        if line.startswith("HEAD "):
            head = line.removeprefix("HEAD ").strip()
            section = None
            continue
        if line.startswith("SEQ_SHA256 "):
            sequence_sha256 = line.removeprefix("SEQ_SHA256 ").strip()
            section = None
            continue
        if line.startswith("COND_GROUP_COUNT "):
            condition_group_count = _parse_int(line.removeprefix("COND_GROUP_COUNT ").strip())
            section = None
            continue
        if line == "BY_CONDITION":
            section = "by_condition"
            continue
        if line.startswith("NONZERO_FINISH_COUNT "):
            nonzero_finish_count = _parse_int(line.removeprefix("NONZERO_FINISH_COUNT ").strip())
            section = None
            continue
        if line == "DISK_HEADROOM":
            section = "disk_headroom"
            continue
        if line == "INODE_HEADROOM":
            section = "inode_headroom"
            continue
        if line == "DONE_MARKERS":
            section = "done_markers"
            continue
        if line == "TAIL":
            section = "tail"
            continue

        if section == "by_condition" and line.strip():
            parts = line.split()
            if len(parts) >= 2:
                count = _parse_int(parts[0])
                if count is not None:
                    by_condition[parts[1]] = count
        elif section == "disk_headroom" and line.strip():
            disk_headroom.append(line.strip())
        elif section == "inode_headroom" and line.strip():
            inode_headroom.append(line.strip())
        elif section == "done_markers" and line.strip():
            done_markers.append(line.strip())
        elif section == "tail" and line.strip():
            tail.append(line)

    return ConfirmatoryStatus(
        utc=utc,
        head=head,
        sequence_sha256=sequence_sha256,
        condition_group_count=condition_group_count,
        by_condition=by_condition,
        nonzero_finish_count=nonzero_finish_count,
        disk_headroom=disk_headroom,
        inode_headroom=inode_headroom,
        done_markers=done_markers,
        tail=tail,
    )


def render_markdown(status: ConfirmatoryStatus, *, expected_count: int) -> str:
    progress = "unknown"
    if status.condition_group_count is not None:
        pct = 100.0 * status.condition_group_count / expected_count
        progress = f"{status.condition_group_count}/{expected_count} ({pct:.1f}%)"

    lines = [
        "# Paper A v2 Confirmatory VPS Status",
        "",
        f"- UTC: `{status.utc or 'unknown'}`",
        f"- VPS HEAD: `{status.head or 'unknown'}`",
        f"- Sequence SHA256: `{status.sequence_sha256 or 'unknown'}`",
        f"- Condition-level result files: `{progress}`",
        f"- Nonzero FINISH count: `{status.nonzero_finish_count if status.nonzero_finish_count is not None else 'unknown'}`",
        f"- Done markers: `{status.done_marker_status}`",
        "",
        "| condition | files |",
        "|---|---:|",
    ]
    for condition, count in sorted(status.by_condition.items()):
        lines.append(f"| `{condition}` | {count} |")
    lines.extend(["## Host Headroom", ""])
    lines.append("Disk:")
    lines.append("```")
    lines.extend(status.disk_headroom or ["unknown"])
    lines.append("```")
    lines.append("Inodes:")
    lines.append("```")
    lines.extend(status.inode_headroom or ["unknown"])
    lines.append("```")
    lines.extend(["", "## Tail", ""])
    if status.tail:
        lines.append("```")
        lines.extend(status.tail)
        lines.append("```")
    else:
        lines.append("_No tail lines captured._")
    return "\n".join(lines) + "\n"


def render_heartbeat(
    status: ConfirmatoryStatus,
    *,
    expected_count: int,
    remote_root: str,
    run_stamp: str,
) -> str:
    """Render a concise manager heartbeat from a read-only status snapshot."""
    progress = "unknown"
    if status.condition_group_count is not None:
        pct = 100.0 * status.condition_group_count / expected_count
        progress = f"{status.condition_group_count}/{expected_count} condition-level result files ({pct:.1f}%)"

    live_done = _has_done_marker(status, "LIVE_GRID_DONE")
    nonlive_done = _has_done_marker(status, "NONLIVE_GRID_DONE")
    bad_done_markers = [line for line in status.done_markers if re.search(r"_GRID_DONE rc=[1-9]", line)]

    blockers: list[str] = []
    if status.nonzero_finish_count is None:
        blockers.append("nonzero FINISH count unknown")
    elif status.nonzero_finish_count > 0:
        blockers.append(f"nonzero FINISH count is {status.nonzero_finish_count}")
    if bad_done_markers:
        blockers.append("nonzero GRID_DONE marker present")

    count_complete = status.condition_group_count == expected_count
    completion_candidate = live_done and nonlive_done and count_complete and not blockers
    if blockers:
        phase = "confirmatory fold needs manager inspection"
        next_action = "stop result-use path; inspect the failing marker/count before rsync or analyzers"
        human_action = "no, unless the manager inspection finds an auth, provider, or strategy decision"
    elif completion_candidate:
        phase = "confirmatory fold completion candidate"
        next_action = "run same-snapshot active-process proof, then rsync and zero-pending completion dry plans"
        human_action = "no"
    else:
        phase = "confirmatory fold monitoring"
        next_action = "continue read-only monitor cadence until both driver done markers and exact result count are proven"
        human_action = "no"

    remaining = _remaining_gates(
        live_done=live_done,
        nonlive_done=nonlive_done,
        count_complete=count_complete,
    )
    blocker_text = "; ".join(blockers) if blockers else "none current"
    headroom_text = _headroom_summary(status)

    lines = [
        f"Paper A heartbeat - snapshot {status.utc or 'unknown UTC'}",
        (
            f"Where: {phase}; run_stamp={run_stamp}; remote_root={remote_root}; "
            f"vps_head={status.head or 'unknown'}"
        ),
        f"Progress: {progress}. This is liveness only, not paper evidence.",
        f"Remaining gates: {', '.join(remaining)}.",
        f"Blockers: {blocker_text}.",
        f"Host headroom: {headroom_text}.",
        f"Next action: {next_action}.",
        f"Human action needed: {human_action}.",
        (
            "Linear/WORKLOG: append and mirror if this snapshot changes phase, lane, "
            "blocker, worker, or evidence-gate state; otherwise chat heartbeat only."
        ),
    ]
    return "\n".join(lines) + "\n"


def _has_done_marker(status: ConfirmatoryStatus, marker: str) -> bool:
    return any(marker in line and "rc=0" in line for line in status.done_markers)


def _headroom_summary(status: ConfirmatoryStatus) -> str:
    disk = _last_data_line(status.disk_headroom)
    inode = _last_data_line(status.inode_headroom)
    return f"disk=`{disk or 'unknown'}`; inode=`{inode or 'unknown'}`"


def _last_data_line(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if line and not line.lower().startswith("filesystem"):
            return line
    return None


def _remaining_gates(*, live_done: bool, nonlive_done: bool, count_complete: bool) -> list[str]:
    gates = list(REMAINING_GATES)
    if live_done:
        gates.remove("LIVE_GRID_DONE rc=0")
    if nonlive_done:
        gates.remove("NONLIVE_GRID_DONE rc=0")
    if count_complete:
        gates.remove("exactly 1890 condition-level result files")
    return gates


def _parse_int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


def run_remote_snapshot(host: str, remote_root: str, run_stamp: str) -> str:
    command = remote_snapshot_command(remote_root, run_stamp)
    proc = subprocess.run(
        ["ssh", host, command],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"ssh exited with rc={proc.returncode}")
    return proc.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--run-stamp", default=DEFAULT_RUN_STAMP)
    parser.add_argument("--expected-count", type=int, default=DEFAULT_EXPECTED_COUNT)
    parser.add_argument("--from-snapshot", type=Path, help="Parse an existing raw snapshot instead of SSH.")
    parser.add_argument("--format", choices=("markdown", "json", "heartbeat"), default="markdown")
    args = parser.parse_args(argv)

    try:
        raw = args.from_snapshot.read_text(encoding="utf-8") if args.from_snapshot else run_remote_snapshot(
            args.host,
            args.remote_root,
            args.run_stamp,
        )
        status = parse_snapshot(raw)
        if args.format == "json":
            print(json.dumps(status.__dict__, indent=2, sort_keys=True))
        elif args.format == "heartbeat":
            print(
                render_heartbeat(
                    status,
                    expected_count=args.expected_count,
                    remote_root=args.remote_root,
                    run_stamp=args.run_stamp,
                ),
                end="",
            )
        else:
            print(render_markdown(status, expected_count=args.expected_count), end="")
        return 0
    except Exception as exc:
        print(f"STATUS_SNAPSHOT_ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
