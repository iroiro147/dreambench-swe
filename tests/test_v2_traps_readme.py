import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "experiments" / "env" / "v2-traps" / "README.md"
TRAPS_ROOT = ROOT / "experiments" / "env" / "v2-traps"
SKELETONS_ROOT = ROOT / "experiments" / "env" / "skeletons"
PENDING_MARKER = "[" + "V2-" + "PENDING]"


def _requires_v2_authoring_tree() -> None:
    if not README.is_file() or not TRAPS_ROOT.is_dir() or not SKELETONS_ROOT.is_dir():
        pytest.skip("v2 authoring skeleton tree is not included in the public artifact")


def _manifest_paths() -> dict[str, Path]:
    return {
        path.parent.name: path
        for path in sorted(SKELETONS_ROOT.glob("batch-*/*/manifest.json"))
    }


def _trap_rows() -> list[tuple[str, str, str, str, str, str]]:
    manifests = _manifest_paths()
    rows: list[tuple[str, str, str, str, str, str]] = []
    for sequence_path in sorted(TRAPS_ROOT.glob("*/sequence.json")):
        seq_id = sequence_path.parent.name
        sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
        manifest_path = manifests[seq_id]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows.append(
            (
                seq_id,
                manifest_path.parent.parent.name,
                str(manifest["construct_label"]),
                Path(str(manifest["fixture_repo"])).name,
                str(manifest["family_label"]),
                "yes" if sequence["clean_start_s3"] else "no",
            )
        )
    return rows


def test_readme_lists_every_finalized_v2_trap() -> None:
    _requires_v2_authoring_tree()

    body = README.read_text(encoding="utf-8")
    rows = _trap_rows()

    assert len(rows) == 30
    for seq_id, batch, construct, fixture, family, clean_start in rows:
        expected = (
            f"| `{seq_id}` | `{batch}` | `{construct}` | `{fixture}` | "
            f"{family} | {clean_start} |"
        )
        assert expected in body


def test_readme_documents_required_trap_files_and_commands() -> None:
    _requires_v2_authoring_tree()

    body = README.read_text(encoding="utf-8")

    for required in (
        "sequence.json",
        "secret_provenance.json",
        "oracles/",
        "refsol/",
        "scripts/inject_secrets.py",
        "experiments/validate_trap.py",
        "scripts/batch_validate.py",
        "PYTHONPATH=src python3 scripts/batch_validate.py experiments/env/v2-traps --dry",
    ):
        assert required in body


def test_readme_keeps_live_results_pending() -> None:
    _requires_v2_authoring_tree()

    body = README.read_text(encoding="utf-8")

    assert PENDING_MARKER in body
    assert "Live v2 fold results are not recorded" in body
