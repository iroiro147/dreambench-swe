"""Tests for CSPRNG trap-skeleton secret injection."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INJECTOR_PATH = ROOT / "scripts" / "inject_secrets.py"


def _load_injector():
    spec = importlib.util.spec_from_file_location("inject_secrets", INJECTOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(root: Path, placeholders: dict) -> None:
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "skeleton_id": "skel-test-csprng",
                "placeholders": placeholders,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_single_trap_skeleton(root: Path, *, include_leak: bool = False) -> None:
    _write_manifest(
        root,
        {
            "SECRET_MARKER": {"format_class": "marker-row EXPORT-{{5A}}-{{3A}}"},
            "SECRET_RID": {"format_class": "id-format RID-{{5A}}-{{4D}}"},
            "SECRET_KEY": {"format_class": "config key cfg_{{6L}}"},
            "SECRET_THRESHOLD": {"format_class": "numeric threshold THR-{{3D}}"},
        },
    )
    record = {
        "seq_id": "trap-alpha",
        "seq_type": "reviewer-preference",
        "repo": "configly",
        "initial_commit": "seed",
        "sessions": [
            {
                "session_index": 1,
                "instruction": "Remember export marker {{SECRET_MARKER}} and row id {{SECRET_RID}}.",
                "oracle_id": "trap-alpha-s1",
            },
            {
                "session_index": 2,
                "instruction": "Use config key {{SECRET_KEY}}.",
                "inject_after": ["trap-alpha-e1"],
                "oracle_id": "trap-alpha-s2",
            },
            {
                "session_index": 3,
                "instruction": "Emit the maintained threshold {{SECRET_THRESHOLD}}.",
                "inject_after": ["trap-alpha-e2"],
                "oracle_id": "trap-alpha-s3",
            },
        ],
        "events": [
            {
                "event_id": "trap-alpha-e1",
                "after_session": 1,
                "kind": "human_feedback",
                "content": "Export rows use {{SECRET_MARKER}} with id {{SECRET_RID}}.",
            },
            {
                "event_id": "trap-alpha-e2",
                "after_session": 2,
                "kind": "human_feedback",
                "content": "The config key {{SECRET_KEY}} gates threshold {{SECRET_THRESHOLD}}.",
            },
        ],
        "oracle_labels": [],
    }
    (root / "sequence.jsonl").write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    oracle_dir = root / "oracles" / "trap-alpha"
    oracle_dir.mkdir(parents=True)
    (oracle_dir / "s3_test.py").write_text(
        "def test_secret_contract():\n"
        "    expected = '{{SECRET_MARKER}}|{{SECRET_RID}}|{{SECRET_KEY}}|{{SECRET_THRESHOLD}}'\n"
        "    assert expected\n",
        encoding="utf-8",
    )
    refsol_dir = root / "refsol" / "trap-alpha"
    refsol_dir.mkdir(parents=True)
    (refsol_dir / "s3.py-diff").write_text(
        "--- a/out.txt\n"
        "+++ b/out.txt\n"
        "@@\n"
        "-old\n"
        "+{{SECRET_MARKER}}|{{SECRET_RID}}|{{SECRET_KEY}}|{{SECRET_THRESHOLD}}\n",
        encoding="utf-8",
    )
    if include_leak:
        (root / "public-notes.txt").write_text("Do not leak {{SECRET_MARKER}} here.\n", encoding="utf-8")


def test_injection_consistency_and_format_classes(tmp_path: Path) -> None:
    injector = _load_injector()
    skeleton = tmp_path / "skeleton"
    skeleton.mkdir()
    _write_single_trap_skeleton(skeleton)

    out = tmp_path / "final"
    result = injector.inject_directory(skeleton, out)

    sequence_text = (out / "sequence.jsonl").read_text(encoding="utf-8")
    oracle_text = (out / "oracles" / "trap-alpha" / "s3_test.py").read_text(encoding="utf-8")
    refsol_text = (out / "refsol" / "trap-alpha" / "s3.py-diff").read_text(encoding="utf-8")
    all_text = "\n".join([sequence_text, oracle_text, refsol_text])

    marker = re.search(r"EXPORT-[A-Za-z0-9]{5}-[A-Za-z0-9]{3}", all_text)
    rid = re.search(r"RID-[A-Za-z0-9]{5}-[0-9]{4}", all_text)
    key = re.search(r"cfg_[a-z]{6}", all_text)
    threshold = re.search(r"THR-[0-9]{3}", all_text)
    assert marker and rid and key and threshold

    for value in (marker.group(0), rid.group(0), key.group(0), threshold.group(0)):
        assert sequence_text.count(value) >= 1
        assert oracle_text.count(value) == 1
        assert refsol_text.count(value) == 1

    assert "{{" not in sequence_text
    assert "{{" not in oracle_text
    assert "{{" not in refsol_text
    assert result.seq_ids == ("trap-alpha",)

    provenance = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert provenance["skeleton_id"] == "skel-test-csprng"
    assert provenance["generator"]["rng"] == "python-secrets"
    assert provenance["generator"]["deterministic"] is False
    assert "seed" not in json.dumps(provenance).lower()
    for value in (marker.group(0), rid.group(0), key.group(0), threshold.group(0)):
        assert value not in json.dumps(provenance)


def test_leakage_check_rejects_secret_outside_trap_assets(tmp_path: Path) -> None:
    injector = _load_injector()
    skeleton = tmp_path / "skeleton"
    skeleton.mkdir()
    _write_single_trap_skeleton(skeleton, include_leak=True)

    with pytest.raises(injector.SecretLeakageError):
        injector.inject_directory(skeleton, tmp_path / "final")


def test_cross_trap_generated_values_are_unique(tmp_path: Path) -> None:
    injector = _load_injector()
    skeleton = tmp_path / "skeleton"
    skeleton.mkdir()
    _write_manifest(
        skeleton,
        {
            "SECRET_A": {"format_class": "marker-row EXPORT-{{10A}}"},
            "SECRET_B": {"format_class": "marker-row EXPORT-{{10A}}"},
        },
    )
    records = [
        {
            "seq_id": "trap-a",
            "seq_type": "convention-learning",
            "repo": "configly",
            "initial_commit": "seed",
            "sessions": [{"session_index": 1, "instruction": "Use {{SECRET_A}}"}],
            "events": [],
            "oracle_labels": [],
        },
        {
            "seq_id": "trap-b",
            "seq_type": "convention-learning",
            "repo": "configly",
            "initial_commit": "seed",
            "sessions": [{"session_index": 1, "instruction": "Use {{SECRET_B}}"}],
            "events": [],
            "oracle_labels": [],
        },
    ]
    (skeleton / "sequence.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    injector.inject_directory(skeleton, tmp_path / "final")

    sequence_text = (tmp_path / "final" / "sequence.jsonl").read_text(encoding="utf-8")
    values = re.findall(r"EXPORT-[A-Za-z0-9]{10}", sequence_text)
    assert len(values) == 2
    assert len(set(values)) == 2


def test_regenerating_same_skeleton_produces_different_secret(tmp_path: Path) -> None:
    injector = _load_injector()
    skeleton = tmp_path / "skeleton"
    skeleton.mkdir()
    _write_manifest(skeleton, {"SECRET_A": {"format_class": "marker-row EXPORT-{{16A}}"}})
    record = {
        "seq_id": "trap-alpha",
        "seq_type": "reviewer-preference",
        "repo": "configly",
        "initial_commit": "seed",
        "sessions": [{"session_index": 1, "instruction": "Use {{SECRET_A}}"}],
        "events": [],
        "oracle_labels": [],
    }
    (skeleton / "sequence.jsonl").write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    injector.inject_directory(skeleton, tmp_path / "final-a")
    injector.inject_directory(skeleton, tmp_path / "final-b")

    a_text = (tmp_path / "final-a" / "sequence.jsonl").read_text(encoding="utf-8")
    b_text = (tmp_path / "final-b" / "sequence.jsonl").read_text(encoding="utf-8")
    a_value = re.search(r"EXPORT-[A-Za-z0-9]{16}", a_text)
    b_value = re.search(r"EXPORT-[A-Za-z0-9]{16}", b_text)
    assert a_value and b_value
    assert a_value.group(0) != b_value.group(0)
