"""Regression coverage for v2 files in the public artifact package."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from scripts import package_artifact


V2_CANONICAL_OUTPUTS = (
    "analysis/fold/confirmatory.json",
    "analysis/fold/v2_fold.json",
    "analysis/fold/v2_confirmatory_clustered.json",
    "analysis/fold/v2_confirmatory_clustered.stdout.json",
    "analysis/fold/v2_hygiene_oracle.json",
    "analysis/fold/v2_hygiene_oracle.tex",
    "analysis/fold/v2_cost_frontier.json",
    "analysis/fold/v2_cost_frontier.tex",
    "analysis/fold/admission_funnel.json",
    "analysis/fold/admission_funnel.tex",
    "analysis/fold/admission_funnel.md",
    "analysis/fold/v2_tables.tex",
    "paper/figures/v2_construct_coverage.pdf",
    "paper/figures/v2_ladder.pdf",
    "paper/figures/v2_verbatim_vs_synthesis.pdf",
)

V2_INPUTS = (
    "experiments/env/sequences_confirmatory_v2.jsonl",
    "analysis/investigation-evidence/PREREGISTRATION.md",
)

V2_SCRIPTS = (
    "scripts/batch_validate.py",
    "scripts/construct_validity.py",
    "scripts/analyze_confirmatory_v2.py",
    "scripts/admission_funnel.py",
    "scripts/hygiene_oracle.py",
    "scripts/cost_frontier.py",
    "scripts/generate_tables_v2.py",
    "scripts/make_v2_figures.py",
    "scripts/check_paper_a_postfold_preflight.py",
    "scripts/validate_submission_package.py",
    "scripts/check_v2_artifact_freshness.py",
    "scripts/check_v2_confirmatory_completion.py",
    "scripts/check_paper_claim_hygiene.py",
    "scripts/check_arxiv_abstract.py",
    "scripts/check_paper_public_evidence.py",
    "scripts/check_release_coherence.py",
    "scripts/audit_public_tree.py",
    "scripts/inject_secrets.py",
    "scripts/Dockerfile.api-agent",
    "scripts/package_arxiv_source.py",
    "scripts/vps_confirmatory_status.py",
)

PACKAGE_PROOFS = (
    "LICENSE",
    "README.md",
    "requirements-artifact.txt",
    "docs/DATASHEET.md",
    "docs/trap_skeleton_spec.md",
    "analysis/fold/HERMETICITY-MANIFEST.md",
    "analysis/fold/CANARY-PROOF.txt",
    "analysis/fold/REPRO-MANIFEST.md",
    "analysis/fold/v2_post_analyzer_completion_check.txt",
    "analysis/investigation-evidence/PREREGISTRATION-V2.md",
    "analysis/investigation-evidence/V2-ANALYSIS-20260708T065236Z.md",
    "analysis/investigation-evidence/FABLE-CONSTRUCTS.md",
    "analysis/investigation-evidence/AUTHORING-WORKLIST.md",
    "analysis/investigation-evidence/SCALE1-FREEZE-DECISIONS.md",
    "analysis/investigation-evidence/CLUSTERED-STATS.md",
    "analysis/investigation-evidence/CONFIRMATORY-FOLD.md",
    "analysis/investigation-evidence/MEM0-ROW-FOLD-REPORT.md",
    "analysis/investigation-evidence/REPORT-Q-BENCH.md",
    "analysis/investigation-evidence/per_trap_matrix_and_leakage.json",
)

HIDDEN_ORACLE_PREFIX = "experiments/env/" + "oracles/"
HIDDEN_REFSOL_PREFIX = "experiments/env/" + "refsol/"
LEGACY_PROJECT_NAME = "paper-" + "dreaming-agents"

PUBLIC_HIDDEN_ASSETS = (
    HIDDEN_ORACLE_PREFIX + "v2-c1-fixture/s3_test.py",
    HIDDEN_REFSOL_PREFIX + "v2-c1-fixture/s3.patch",
)


def write_fixture_file(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        path.write_text(json.dumps({"path": rel, "fixture": True}) + "\n", encoding="utf-8")
    elif path.suffix == ".jsonl":
        path.write_text(json.dumps({"path": rel, "fixture": True}) + "\n", encoding="utf-8")
    elif path.suffix == ".py":
        path.write_text(f'"""Fixture script for {rel}."""\n\nVALUE = {rel!r}\n', encoding="utf-8")
    else:
        path.write_text(f"fixture for {rel}\n", encoding="utf-8")


def manifest_paths(manifest: dict[str, object]) -> set[str]:
    files = manifest["files"]
    assert isinstance(files, list)
    return {str(item["path"]) for item in files if isinstance(item, dict)}


def test_public_package_manifest_and_tar_include_v2_outputs_and_scripts(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()

    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS, *PUBLIC_HIDDEN_ASSETS):
        write_fixture_file(root, rel)

    monkeypatch.setattr(package_artifact, "ROOT", root)

    result = package_artifact.build_package(private=False, dist_dir=dist)

    assert result["mode"] == "public"
    manifest = json.loads((dist / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["release_version"] == "v2.0.5"
    paths = manifest_paths(manifest)
    expected_public_paths = {*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS}
    assert expected_public_paths <= paths
    assert not (set(PUBLIC_HIDDEN_ASSETS) & paths)

    roles = {item["path"]: item["role"] for item in manifest["files"]}
    assert {roles[path] for path in V2_CANONICAL_OUTPUTS} == {"frozen-analysis-artifact"}
    assert {roles[path] for path in V2_SCRIPTS} == {"analysis-or-smoke-script"}

    archive_path = dist / package_artifact.PUBLIC_ARCHIVE
    with tarfile.open(archive_path, "r:gz") as archive:
        tar_paths = set(archive.getnames())

    package_prefix = package_artifact.PACKAGE_NAME
    assert {f"{package_prefix}/{path}" for path in expected_public_paths} <= tar_paths
    assert not ({f"{package_prefix}/{path}" for path in PUBLIC_HIDDEN_ASSETS} & tar_paths)


def test_public_package_rebuild_is_deterministic(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    first_dist = tmp_path / "dist-a"
    second_dist = tmp_path / "dist-b"
    root.mkdir()

    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)

    monkeypatch.setattr(package_artifact, "ROOT", root)

    package_artifact.build_package(private=False, dist_dir=first_dist)
    package_artifact.build_package(private=False, dist_dir=second_dist)

    assert (first_dist / package_artifact.PUBLIC_ARCHIVE).read_bytes() == (
        second_dist / package_artifact.PUBLIC_ARCHIVE
    ).read_bytes()
    assert (first_dist / "MANIFEST.json").read_bytes() == (second_dist / "MANIFEST.json").read_bytes()
    assert (first_dist / "CHECKSUMS.sha256").read_bytes() == (second_dist / "CHECKSUMS.sha256").read_bytes()


def test_source_date_epoch_freezes_manifest_timestamp(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()
    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)
    monkeypatch.setattr(package_artifact, "ROOT", root)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")

    package_artifact.build_package(private=False, dist_dir=dist)

    manifest = json.loads((dist / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["generated_at_utc"] == "2023-11-14T22:13:20Z"


def test_invalid_source_date_epoch_fails(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()
    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)
    monkeypatch.setattr(package_artifact, "ROOT", root)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-an-epoch")

    with pytest.raises(RuntimeError, match="invalid SOURCE_DATE_EPOCH"):
        package_artifact.build_package(private=False, dist_dir=dist)


def test_rebuild_inherits_matching_scrubbed_provenance(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()
    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)
    readme = root / "artifact/README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("local path: <USER_HOME>/project\n", encoding="utf-8")
    monkeypatch.setattr(package_artifact, "ROOT", root)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")

    package_artifact.build_package(private=False, dist_dir=dist)
    first_manifest = json.loads((dist / "MANIFEST.json").read_text(encoding="utf-8"))
    readme_entry = next(item for item in first_manifest["files"] if item["path"] == "artifact/README.md")
    assert readme_entry["scrubbed"] is True

    readme.write_text("local path: <USER_HOME>/project\n", encoding="utf-8")
    monkeypatch.setenv("DREAMBENCH_RELEASE_MANIFEST", str(dist / "MANIFEST.json"))
    package_artifact.build_package(private=False, dist_dir=tmp_path / "rebuilt")
    rebuilt_manifest = json.loads((tmp_path / "rebuilt/MANIFEST.json").read_text(encoding="utf-8"))
    rebuilt_entry = next(item for item in rebuilt_manifest["files"] if item["path"] == "artifact/README.md")

    assert rebuilt_entry["sha256"] == readme_entry["sha256"]
    assert rebuilt_entry["scrubbed"] is True


def test_rebuild_does_not_inherit_scrubbed_flag_after_payload_change(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()
    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)
    monkeypatch.setattr(package_artifact, "ROOT", root)
    package_artifact.build_package(private=False, dist_dir=dist)
    manifest = json.loads((dist / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["path"] == "README.md")
    entry["scrubbed"] = True
    (dist / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")

    (root / "README.md").write_text("changed payload\n", encoding="utf-8")
    monkeypatch.setenv("DREAMBENCH_RELEASE_MANIFEST", str(dist / "MANIFEST.json"))
    package_artifact.build_package(private=False, dist_dir=tmp_path / "rebuilt")
    rebuilt_manifest = json.loads((tmp_path / "rebuilt/MANIFEST.json").read_text(encoding="utf-8"))
    rebuilt_entry = next(item for item in rebuilt_manifest["files"] if item["path"] == "README.md")

    assert rebuilt_entry["sha256"] != entry["sha256"]
    assert rebuilt_entry["scrubbed"] is False


def test_package_excludes_historical_empty_placeholders(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()
    (root / ".artifactignore").write_text(
        "scripts/analyze_results.py\nsrc/benchmarks/swe_task_interface.py\n",
        encoding="utf-8",
    )

    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)
    for rel in ("scripts/analyze_results.py", "src/benchmarks/swe_task_interface.py"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    monkeypatch.setattr(package_artifact, "ROOT", root)

    package_artifact.build_package(private=False, dist_dir=dist)
    manifest = json.loads((dist / "MANIFEST.json").read_text(encoding="utf-8"))
    paths = manifest_paths(manifest)

    assert "scripts/analyze_results.py" not in paths
    assert "src/benchmarks/swe_task_interface.py" not in paths


def test_package_rejects_source_symlink(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 'outside'\n", encoding="utf-8")
    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)
    link = root / "src/external.py"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")

    monkeypatch.setattr(package_artifact, "ROOT", root)

    with pytest.raises(RuntimeError, match="refusing to package symlink source file"):
        package_artifact.build_package(private=False, dist_dir=dist)


def test_package_fails_when_required_v2_output_missing(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()

    missing = "analysis/fold/v2_fold.json"
    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        if rel != missing:
            write_fixture_file(root, rel)

    monkeypatch.setattr(package_artifact, "ROOT", root)

    with pytest.raises(RuntimeError, match=f"required package file is missing: {missing}"):
        package_artifact.build_package(private=False, dist_dir=dist)


def test_package_scrubs_private_roots_and_rejects_forbidden_content(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()

    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS):
        write_fixture_file(root, rel)
    contaminated = root / "analysis/fold/v2_fold.json"
    contaminated.write_text(
        json.dumps(
            {
                "source_path": "/root/" + LEGACY_PROJECT_NAME + "/experiments/results/x/results.json",
                "local": "/" + "Users/<LOCAL_USER>/Desktop/projects/" + LEGACY_PROJECT_NAME + "/file.txt",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(package_artifact, "ROOT", root)

    package_artifact.build_package(private=False, dist_dir=dist)
    with tarfile.open(dist / package_artifact.PUBLIC_ARCHIVE, "r:gz") as archive:
        member = archive.extractfile(f"{package_artifact.PACKAGE_NAME}/analysis/fold/v2_fold.json")
        assert member is not None
        packaged = member.read().decode("utf-8")

    assert LEGACY_PROJECT_NAME not in packaged
    assert "/" + "Users/" not in packaged
    assert "/root/" + LEGACY_PROJECT_NAME not in packaged


def test_public_package_scrubs_reviewer_only_reference_text(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()

    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS, *PUBLIC_HIDDEN_ASSETS):
        write_fixture_file(root, rel)
    readme = root / "artifact/README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        "Public package excludes " + HIDDEN_ORACLE_PREFIX + " and " + HIDDEN_REFSOL_PREFIX + " assets.\n",
        encoding="utf-8",
    )
    fold_json = root / "analysis/fold/v2_fold.json"
    fold_json.write_text(
        json.dumps(
            {
                "oracle": HIDDEN_ORACLE_PREFIX + "example/s3_test.py",
                "refsol": HIDDEN_REFSOL_PREFIX + "example/solution.py",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(package_artifact, "ROOT", root)

    package_artifact.build_package(private=False, dist_dir=dist)
    with tarfile.open(dist / package_artifact.PUBLIC_ARCHIVE, "r:gz") as archive:
        readme_member = archive.extractfile(f"{package_artifact.PACKAGE_NAME}/artifact/README.md")
        fold_member = archive.extractfile(f"{package_artifact.PACKAGE_NAME}/analysis/fold/v2_fold.json")
        assert readme_member is not None
        assert fold_member is not None
        packaged_text = readme_member.read().decode("utf-8") + fold_member.read().decode("utf-8")

    assert HIDDEN_ORACLE_PREFIX not in packaged_text
    assert HIDDEN_REFSOL_PREFIX not in packaged_text
    assert "<REVIEWER_ONLY_ORACLES>/" in packaged_text
    assert "<REVIEWER_ONLY_REFSOL>/" in packaged_text


def test_private_package_preserves_reviewer_only_reference_text(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "repo"
    dist = tmp_path / "dist"
    root.mkdir()

    for rel in (*V2_CANONICAL_OUTPUTS, *V2_INPUTS, *V2_SCRIPTS, *PACKAGE_PROOFS, *PUBLIC_HIDDEN_ASSETS):
        write_fixture_file(root, rel)
    readme = root / "artifact/README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        "Private package includes " + HIDDEN_ORACLE_PREFIX + " and " + HIDDEN_REFSOL_PREFIX + " assets.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(package_artifact, "ROOT", root)

    package_artifact.build_package(private=True, dist_dir=dist)
    with tarfile.open(dist / package_artifact.PRIVATE_ARCHIVE, "r:gz") as archive:
        readme_member = archive.extractfile(f"{package_artifact.PACKAGE_NAME}/artifact/README.md")
        assert readme_member is not None
        packaged = readme_member.read().decode("utf-8")

    assert HIDDEN_ORACLE_PREFIX in packaged
    assert HIDDEN_REFSOL_PREFIX in packaged


def test_artifact_runbook_separates_unpacked_and_publisher_only_gates() -> None:
    runbook = (package_artifact.ROOT / "artifact/README.md").read_text(encoding="utf-8")
    verify_block = runbook.split("## Verify the package", 1)[1].split(
        "## Rebuild the package", 1
    )[0]
    rebuild_block = runbook.split("## Rebuild the package", 1)[1].split(
        "## Publisher-only gates", 1
    )[0]

    assert verify_block.index("scripts/validate_submission_package.py") < verify_block.index(
        "scripts/run_smoke.py"
    )
    assert "integrity checks intentionally run before the smoke" in verify_block
    assert "scripts/check_v2_artifact_freshness.py" not in rebuild_block
    assert "scripts/check_paper_public_evidence.py" not in rebuild_block
    assert "scripts/check_release_coherence.py" not in rebuild_block
    assert "freshness against raw result/log roots" in runbook
    assert "requires `pdftotext` from Poppler" in runbook
