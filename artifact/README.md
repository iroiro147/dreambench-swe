# DreamBench-SWE Artifact

This package is the public artifact bundle for the DreamBench-SWE v2 Paper A
submission. It is not a raw repository dump.

## What ships

- Source code under `src/`.
- Focused analysis, table, smoke, and live-rerun provenance scripts under `scripts/`.
- `ops/launch_confirmatory.sh` and `ops/launch_synth.sh` as live-rerun provenance only. Running them creates a new hosted-model experiment, not an exact replay.
- Public task fixtures and sequence metadata needed by the public smoke/tests.
- Frozen Paper A v2 analysis summaries:
  `analysis/fold/v2_fold.json`, `analysis/fold/v2_confirmatory_clustered.json`,
  `analysis/fold/v2_hygiene_oracle.json`, `analysis/fold/v2_cost_frontier.json`,
  `analysis/fold/admission_funnel.*`, and `analysis/fold/v2_tables.tex`.
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
python3 scripts/run_smoke.py
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
```

The repository `make test` target is intentionally broader than the public artifact because it includes tests that require hidden oracle/reference-solution files and fixture repository history. Use the selected public test command above for the public tarball.

## Rebuild the package

From the source repository:

```bash
PYTHONPATH=src python3 scripts/package_artifact.py --dist-dir dist
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist --mode public
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
