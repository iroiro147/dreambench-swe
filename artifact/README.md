# DreamBench-SWE Artifact

This package is the public artifact bundle for the DreamBench-SWE v2 Paper A
release `v2.0.5`. It is not a raw repository dump.

## What ships

- Source code under `src/`.
- Focused analysis, table, smoke, and live-rerun provenance scripts under `scripts/`.
- `ops/launch_confirmatory.sh` and `ops/launch_synth.sh` as live-rerun provenance only. Running them creates a new hosted-model experiment, not an exact replay.
- Public task fixtures and sequence metadata needed by the public smoke/tests.
- Frozen Paper A v2 analysis summaries:
  `analysis/fold/v2_fold.json`, `analysis/fold/v2_confirmatory_clustered.json`,
  `analysis/fold/v2_hygiene_oracle.json`, `analysis/fold/v2_cost_frontier.json`,
  `analysis/fold/admission_funnel.*`, `analysis/fold/v2_tables.tex`, and the
  completion proof.  Every concrete public-evidence file cited by the
  manuscript is an explicit required package member.
  The `v2_hygiene_oracle.json` filename refers to public hygiene-scorer output;
  it is not the hidden scoring oracle material excluded from this package.
- Generated Paper A v2 manuscript figures under `paper/figures/v2_*.pdf`.
- Public-compatible tests under `tests/`.

## What does not ship in the public package

- `.git` history, local agent instructions, logs, bins, state files, handoff material, and credentials.
- Hidden scoring assets under `<REVIEWER_ONLY_ORACLES>/` and `<REVIEWER_ONLY_REFSOL>/`.
- Full raw hosted-model result directories. The public package can regenerate tables and sensitivity summaries from the frozen folded JSON; it cannot replay the paper fold from raw result records.

Reviewer-only packages built with `python3 scripts/package_artifact.py --private` additionally include `<REVIEWER_ONLY_ORACLES>/` and `<REVIEWER_ONLY_REFSOL>/`, still scrubbed of local paths and secrets.

## Verify the package

From inside the unpacked artifact root:

```bash
python3 -m pip install -r requirements-artifact.txt
PYTHONPATH=src python3 scripts/validate_submission_package.py --package-dir . --mode public
python3 scripts/audit_public_tree.py --root .
PYTHONPATH=src python3 scripts/generate_tables_v2.py --input analysis/fold/v2_fold.json --output /tmp/dreambench-swe-v2-tables.tex
python3 -m pytest -q \
  tests/test_memory_schema.py \
  tests/test_memory_store.py \
  tests/test_retrieval.py \
  tests/test_consolidation.py \
  tests/test_contradiction_repair.py \
  tests/test_task_loader.py \
  tests/test_run_smoke.py \
  tests/test_stats_analysis.py \
  tests/test_analyze_confirmatory.py \
  tests/test_cost_analysis.py \
  tests/test_barrier2_rescore.py \
  tests/test_contamination_scan.py \
  tests/test_cross_model_rescore.py \
  tests/test_mem0_policy.py \
  tests/test_analyze_confirmatory_v2.py \
  tests/test_admission_funnel.py \
  tests/test_hygiene_oracle.py \
  tests/test_cost_frontier.py \
  tests/test_generate_tables_v2.py \
  tests/test_make_v2_figures.py
python3 scripts/run_smoke.py
```

The integrity checks intentionally run before the smoke command because the
smoke writes local outputs under `experiments/results/smoke/`. Use a fresh
extraction to repeat manifest validation after running the smoke.

The repository `make test` target is intentionally broader than the public artifact because it includes tests that require hidden oracle/reference-solution files and fixture repository history. Use the selected public test command above for the public tarball.

## Rebuild the package

From the source repository or the unpacked artifact root, reuse the timestamp
in the release manifest so the manifest and tarball are byte-reproducible. Set
`DREAMBENCH_RELEASE_MANIFEST=MANIFEST.json` when rebuilding from inside the
unpacked artifact root.

```bash
RELEASE_MANIFEST="${DREAMBENCH_RELEASE_MANIFEST:-dist/MANIFEST.json}"
export SOURCE_DATE_EPOCH="$(python3 -c 'import datetime,json,sys; value=json.load(open(sys.argv[1]))["generated_at_utc"]; print(int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()))' "$RELEASE_MANIFEST")"
PYTHONPATH=src python3 scripts/package_artifact.py --dist-dir dist
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist --mode public
python3 scripts/audit_public_tree.py --archive dist/dreambench-swe-artifact.tar.gz
```

This writes:

- `dist/dreambench-swe-artifact.tar.gz`
- `dist/MANIFEST.json`
- `dist/CHECKSUMS.sha256`

Use `python3 scripts/package_artifact.py --private` only for reviewer-only distribution:

```bash
PYTHONPATH=src python3 scripts/package_artifact.py --private --dist-dir dist-private
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist-private --mode private
```

## Publisher-only gates

The full source checkout also checks freshness against raw result/log roots,
manuscript evidence references, and coherence across the artifact, paper PDF,
arXiv source archive, manifest, and detached release ledger. Those inputs and
publisher surfaces are intentionally absent from the unpacked public artifact,
so these are not artifact-verification commands. Run them only after all
release assets have been built:

```bash
PYTHONPATH=src python3 scripts/check_v2_artifact_freshness.py
python3 scripts/check_arxiv_abstract.py --path paper/sections/01_abstract.tex
python3 scripts/check_paper_public_evidence.py
python3 scripts/check_release_coherence.py
```

The coherence check requires `pdftotext` from Poppler (`brew install poppler`
on macOS or install `poppler-utils` on Debian or Ubuntu).
