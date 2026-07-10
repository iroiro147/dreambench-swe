from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts import validate_submission_package as validator


PACKAGE_NAME = validator.PACKAGE_NAME
HIDDEN_ORACLE_PREFIX = "experiments/env/" + "oracles/"
HIDDEN_REFSOL_PREFIX = "experiments/env/" + "refsol/"
LEGACY_PROJECT_NAME = "paper-" + "dreaming-agents"
PENDING_MARKER = "[" + "V2-" + "PENDING]"


def test_valid_synthetic_dist_package_passes(tmp_path: Path) -> None:
    dist = build_synthetic_dist(tmp_path, extra_files={"src/demo_pkg/__init__.py": b""})

    summary = validator.validate_dist_package(dist, mode="public")

    assert summary.package_name == PACKAGE_NAME
    assert summary.mode == "public"
    assert summary.manifest_file_count >= len(validator.REQUIRED_V2_ANALYSIS_ARTIFACTS)
    assert summary.checksum_file_count == summary.manifest_file_count + 1


def test_missing_release_version_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(package_dir)
    manifest_path = package_dir / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["release_version"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="public")

    assert "package MANIFEST.json field 'release_version' must be a non-empty string" in exc_info.value.errors


def test_wrong_release_version_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(package_dir)
    manifest_path = package_dir / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_version"] = "v2.0.4"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="public")

    assert (
        "package MANIFEST.json release_version is 'v2.0.4', expected 'v2.0.5'"
        in exc_info.value.errors
    )


def test_missing_required_v2_artifact_fails(tmp_path: Path) -> None:
    missing = "analysis/fold/v2_hygiene_oracle.tex"
    dist = build_synthetic_dist(tmp_path, omit={missing})

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_dist_package(dist, mode="public")

    assert f"required Paper A v2 package path missing from manifest: {missing}" in exc_info.value.errors


def test_required_v2_text_artifact_with_pending_marker_fails(tmp_path: Path) -> None:
    contaminated = "analysis/fold/v2_fold.json"
    dist = build_synthetic_dist(
        tmp_path,
        extra_files={
            contaminated: json.dumps({"status": PENDING_MARKER}, sort_keys=True).encode() + b"\n",
        },
    )

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_dist_package(dist, mode="public")

    assert (
        f"required Paper A v2 text artifact contains unresolved v2 pending marker: {contaminated}"
        in exc_info.value.errors
    )


def test_placeholder_filename_and_empty_code_file_fail(tmp_path: Path) -> None:
    dist = build_synthetic_dist(
        tmp_path,
        extra_files={
            "analysis/fold/v2_ladder_placeholder.pdf": b"%PDF-1.4\nvalidated\n",
            "scripts/empty_helper.py": b"",
        },
    )

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_dist_package(dist, mode="public")

    errors = "\n".join(exc_info.value.errors)
    assert "package contains obvious placeholder filename: analysis/fold/v2_ladder_placeholder.pdf" in errors
    assert "package contains empty placeholder-like file: scripts/empty_helper.py" in errors


def test_internal_checksum_mismatch_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(package_dir)
    target = package_dir / "analysis/fold/v2_fold.json"
    target.write_text(json.dumps({"corrupted": True}) + "\n", encoding="utf-8")

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="public")

    errors = "\n".join(exc_info.value.errors)
    assert "MANIFEST.json entry analysis/fold/v2_fold.json sha256 mismatch" in errors
    assert "package CHECKSUMS.sha256 entry analysis/fold/v2_fold.json sha256 mismatch" in errors


def test_invalid_manifest_mode_fails_without_expected_mode(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(package_dir)
    manifest_path = package_dir / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mode"] = "banana"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir)

    assert "package MANIFEST.json mode is 'banana'" in "\n".join(exc_info.value.errors)


def test_manifest_archive_parent_traversal_fails(tmp_path: Path) -> None:
    dist = build_synthetic_dist(tmp_path)
    manifest_path = dist / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive"] = "../outside.tar.gz"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_dist_package(dist, mode="public")

    assert "dist MANIFEST.json archive is not a safe relative path" in "\n".join(exc_info.value.errors)


def test_manifest_archive_name_must_match_mode(tmp_path: Path) -> None:
    dist = build_synthetic_dist(tmp_path)
    manifest_path = dist / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive"] = validator.PRIVATE_ARCHIVE
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_dist_package(dist, mode="public")

    assert "expected 'dreambench-swe-artifact.tar.gz' for mode 'public'" in "\n".join(exc_info.value.errors)


def test_private_package_requires_hidden_assets(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(package_dir, mode="private")

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="private")

    errors = "\n".join(exc_info.value.errors)
    assert f"private package missing reviewer-only asset prefix: {HIDDEN_ORACLE_PREFIX}" in errors
    assert f"private package missing reviewer-only asset prefix: {HIDDEN_REFSOL_PREFIX}" in errors


def test_private_package_with_hidden_assets_passes(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(
        package_dir,
        mode="private",
        extra_files={
            HIDDEN_ORACLE_PREFIX + "example/s1_test.py": b"def test_oracle():\n    assert True\n",
            HIDDEN_REFSOL_PREFIX + "example/solution.py": b"VALUE = 'reviewer only'\n",
        },
    )

    summary = validator.validate_package_directory(package_dir, mode="private")

    assert summary.mode == "private"


def test_package_directory_symlink_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(package_dir)
    outside = tmp_path / "outside.txt"
    outside.write_text("external\n", encoding="utf-8")
    symlink = package_dir / "artifact/external-link.txt"
    symlink.symlink_to(outside)

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="public")

    assert "package contains symlink path: artifact/external-link.txt" in "\n".join(exc_info.value.errors)


def test_forbidden_local_content_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(
        package_dir,
        extra_files={
            "analysis/fold/path-leak.md": (
                ("source=/root/" + LEGACY_PROJECT_NAME + "/experiments/results\n").encode()
                + b"home=/" + b"Users/<LOCAL_USER>/Desktop/projects/" + LEGACY_PROJECT_NAME.encode() + b"\n"
            ),
        },
    )

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="public")

    errors = "\n".join(exc_info.value.errors)
    assert "package file analysis/fold/path-leak.md contains forbidden private project name" in errors
    assert "package file analysis/fold/path-leak.md contains forbidden private VPS project root" in errors
    assert "package file analysis/fold/path-leak.md contains forbidden local user path" in errors


def test_public_hidden_asset_content_reference_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(
        package_dir,
        extra_files={
            "analysis/fold/leak.md": ("see " + HIDDEN_ORACLE_PREFIX + "example/s3_test.py\n").encode(),
        },
    )

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="public")

    assert (
        f"public package file analysis/fold/leak.md contains reviewer-only asset reference: {HIDDEN_ORACLE_PREFIX}"
        in "\n".join(exc_info.value.errors)
    )


def test_public_facing_nomenclature_leak_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE_NAME
    build_synthetic_package_dir(
        package_dir,
        extra_files={
            "README.md": b"# DreamBench-SWE\n\nDreamForge public label should fail.\n",
            "artifact/README.md": b"# Artifact\n\nDREAMFORGE_ROOT=. should fail.\n",
            "experiments/README.md": b"# Experiments\n\ndreambench-agent should fail.\n",
        },
    )

    with pytest.raises(validator.PackageValidationError) as exc_info:
        validator.validate_package_directory(package_dir, mode="public")

    errors = "\n".join(exc_info.value.errors)
    assert "public-facing package file README.md contains forbidden legacy public system name" in errors
    assert "public-facing package file artifact/README.md contains forbidden legacy public system name" in errors
    assert "public-facing package file experiments/README.md contains forbidden bare DreamBench name" in errors


def build_synthetic_dist(
    tmp_path: Path,
    *,
    mode: str = "public",
    omit: set[str] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> Path:
    dist = tmp_path / "dist"
    package_dir = tmp_path / PACKAGE_NAME
    dist.mkdir()
    build_synthetic_package_dir(package_dir, mode=mode, omit=omit, extra_files=extra_files)

    archive_name = validator.PRIVATE_ARCHIVE if mode == "private" else validator.PUBLIC_ARCHIVE
    archive_path = dist / archive_name
    create_tar_gz(package_dir, archive_path)
    (dist / "MANIFEST.json").write_bytes((package_dir / "MANIFEST.json").read_bytes())
    (dist / "CHECKSUMS.sha256").write_text(
        "\n".join(
            [
                f"{sha256_file(archive_path)}  {archive_path.name}",
                f"{sha256_file(dist / 'MANIFEST.json')}  MANIFEST.json",
                f"{sha256_file(package_dir / 'CHECKSUMS.sha256')}  {PACKAGE_NAME}/CHECKSUMS.sha256",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return dist


def build_synthetic_package_dir(
    package_dir: Path,
    *,
    mode: str = "public",
    omit: set[str] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> None:
    omit = omit or set()
    required_files = (
        validator.REQUIRED_V2_ANALYSIS_ARTIFACTS
        + validator.REQUIRED_V2_FIGURES
        + validator.REQUIRED_V2_INPUTS
        + validator.REQUIRED_V2_SCRIPTS
        + validator.REQUIRED_PACKAGE_PROOFS
        + (
            ".artifactignore",
            "Makefile",
            "artifact/README.md",
            "requirements-artifact.txt",
            "experiments/env/tasks.jsonl",
        )
    )
    payloads: dict[str, bytes] = {}
    for rel in required_files:
        if rel in omit:
            continue
        payloads[rel] = payload_for(rel)
    payloads.update(extra_files or {})

    for rel, content in payloads.items():
        path = package_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    manifest = {
        "package": PACKAGE_NAME,
        "release_version": validator.RELEASE_VERSION,
        "mode": mode,
        "archive": validator.PRIVATE_ARCHIVE if mode == "private" else validator.PUBLIC_ARCHIVE,
        "generated_at_utc": "2026-07-06T14:50:00Z",
        "anonymous": False,
        "raw_result_records_included": False,
        "exact_frozen_record_replay_supported": False,
        "live_rerun_is_new_experiment": True,
        "public_exclusions": [],
        "files": [
            {
                "path": rel,
                "source": rel,
                "size": (package_dir / rel).stat().st_size,
                "sha256": sha256_file(package_dir / rel),
                "role": role_for(rel),
                "scrubbed": False,
            }
            for rel in sorted(payloads)
        ],
    }
    (package_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_lines = []
    for path in sorted(item for item in package_dir.rglob("*") if item.is_file()):
        rel = path.relative_to(package_dir).as_posix()
        checksum_lines.append(f"{sha256_file(path)}  {rel}")
    (package_dir / "CHECKSUMS.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def payload_for(rel: str) -> bytes:
    if rel.endswith(".json"):
        return (json.dumps({"artifact": rel, "validated": True}, sort_keys=True) + "\n").encode()
    if rel.endswith(".jsonl"):
        return (json.dumps({"artifact": rel, "validated": True}, sort_keys=True) + "\n").encode()
    if rel.endswith(".py"):
        return f'"""Validation fixture for {rel}."""\n\nVALUE = {rel!r}\n'.encode()
    if rel.endswith(".tex"):
        return b"\\begin{tabular}{lr}\nmetric & value \\\\\n\\end{tabular}\n"
    if rel.endswith(".md"):
        return f"# {Path(rel).name}\n\nSynthetic validation content for Paper A.\n".encode()
    if rel.endswith(".txt"):
        return b"canonical validation proof\n"
    return f"validated content for {rel}\n".encode()


def role_for(rel: str) -> str:
    if rel.startswith("analysis/fold/"):
        return "frozen-analysis-artifact"
    if rel.startswith("scripts/"):
        return "analysis-or-smoke-script"
    if rel.startswith("experiments/env/"):
        return "public-fixture-or-sequence-metadata"
    return "support"


def create_tar_gz(package_dir: Path, archive_path: Path) -> None:
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as archive:
                for path in sorted([package_dir, *package_dir.rglob("*")]):
                    arcname = path.relative_to(package_dir.parent).as_posix()
                    info = archive.gettarinfo(str(path), arcname=arcname)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    if path.is_file():
                        archive.addfile(info, io.BytesIO(path.read_bytes()))
                    else:
                        archive.addfile(info)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
