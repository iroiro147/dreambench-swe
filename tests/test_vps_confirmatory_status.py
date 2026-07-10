from __future__ import annotations

import json
from pathlib import Path

from scripts import vps_confirmatory_status as status


SAMPLE = """UTC 2026-07-06T17:17:04Z
HEAD 3ac349e873b1f9d03a25502629dc43083b8808ee
SEQ_SHA256 4966bad1e535aaa0165c8aa7f2cb6fefd1d4e4afca78accfd868591a40448743
COND_GROUP_COUNT 905
BY_CONDITION
     90 B0
     90 B5-MEM0-LIT
      5 DF
NONZERO_FINISH_COUNT 0
DISK_HEADROOM
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1       451G   31G  420G   7% /
INODE_HEADROOM
Filesystem       Inodes  IUsed    IFree IUse% Mounted on
/dev/sda1      30507008 512000 29995008    2% /
DONE_MARKERS
TAIL
2026-07-06T17:16:59Z START DF-s1-g07
2026-07-06T17:17:01Z FINISH DF-s1-g05 rc=0 elapsed=12.0s
"""


def test_parse_snapshot_extracts_status_fields() -> None:
    parsed = status.parse_snapshot(SAMPLE)

    assert parsed.utc == "2026-07-06T17:17:04Z"
    assert parsed.head == "3ac349e873b1f9d03a25502629dc43083b8808ee"
    assert parsed.sequence_sha256 == "4966bad1e535aaa0165c8aa7f2cb6fefd1d4e4afca78accfd868591a40448743"
    assert parsed.condition_group_count == 905
    assert parsed.by_condition == {"B0": 90, "B5-MEM0-LIT": 90, "DF": 5}
    assert parsed.nonzero_finish_count == 0
    assert parsed.disk_headroom[-1] == "/dev/sda1       451G   31G  420G   7% /"
    assert parsed.inode_headroom[-1] == "/dev/sda1      30507008 512000 29995008    2% /"
    assert parsed.done_markers == []
    assert parsed.tail[-1].endswith("rc=0 elapsed=12.0s")


def test_render_markdown_keeps_liveness_fields_visible() -> None:
    rendered = status.render_markdown(status.parse_snapshot(SAMPLE), expected_count=1890)

    assert "Condition-level result files: `905/1890 (47.9%)`" in rendered
    assert "| `B5-MEM0-LIT` | 90 |" in rendered
    assert "Done markers: `none`" in rendered
    assert "## Host Headroom" in rendered
    assert "420G" in rendered
    assert "29995008" in rendered
    assert "FINISH DF-s1-g05 rc=0" in rendered


def test_render_heartbeat_keeps_progress_as_liveness_only() -> None:
    rendered = status.render_heartbeat(
        status.parse_snapshot(SAMPLE),
        expected_count=1890,
        remote_root="<LOCAL_ROOT>",
        run_stamp="20260706T074759Z",
    )

    assert rendered.startswith("Paper A heartbeat - snapshot 2026-07-06T17:17:04Z")
    assert "Progress: 905/1890 condition-level result files (47.9%). This is liveness only, not paper evidence." in rendered
    assert "Remaining gates: NONLIVE_GRID_DONE rc=0, LIVE_GRID_DONE rc=0" in rendered
    assert "Blockers: none current." in rendered
    assert "Host headroom: disk=`/dev/sda1       451G   31G  420G   7% /`; inode=`/dev/sda1      30507008 512000 29995008    2% /`." in rendered
    assert "Human action needed: no." in rendered


def test_render_heartbeat_flags_nonzero_finish_count() -> None:
    snapshot = SAMPLE.replace("NONZERO_FINISH_COUNT 0", "NONZERO_FINISH_COUNT 2")
    rendered = status.render_heartbeat(
        status.parse_snapshot(snapshot),
        expected_count=1890,
        remote_root="<LOCAL_ROOT>",
        run_stamp="20260706T074759Z",
    )

    assert "Where: confirmatory fold needs manager inspection;" in rendered
    assert "Blockers: nonzero FINISH count is 2." in rendered
    assert "stop result-use path" in rendered


def test_cli_json_output_is_serializable(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    snapshot = tmp_path / "snapshot.txt"
    snapshot.write_text(SAMPLE, encoding="utf-8")

    rc = status.main(["--from-snapshot", str(snapshot), "--format", "json"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["condition_group_count"] == 905
    assert data["by_condition"]["B5-MEM0-LIT"] == 90
    assert data["disk_headroom"][-1].endswith("420G   7% /")

    payload = json.dumps(status.parse_snapshot(SAMPLE).__dict__)
    assert "B5-MEM0-LIT" in payload


def test_cli_heartbeat_output(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    snapshot = tmp_path / "snapshot.txt"
    snapshot.write_text(SAMPLE, encoding="utf-8")

    rc = status.main(["--from-snapshot", str(snapshot), "--format", "heartbeat"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert "Paper A heartbeat" in captured.out
    assert "Linear/WORKLOG:" in captured.out
