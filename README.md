# DreamBench-SWE

This repository contains the DreamBench-SWE v2 Paper A public submission package:
the benchmark harness, public fixtures, folded analyzer outputs, publication figures,
and reference-probe implementation.

The paper's headline result is conservative: DreamBench-SWE is the benchmark artifact,
and the evaluated maintenance system is reported only as a reference probe. The v2
confirmatory fold is complete: 60 traps, 3 seeds, 1,890 total result files, 30/30
admitted v2 traps, and canonical analysis generated from `analysis/fold/`.

## What Is Here

- `paper/figures/` - generated manuscript figure PDFs.
- `src/` - typed memory schema, append-preserving store, retrieval gate, maintenance
  operators, benchmark policies, and scoring utilities.
- `experiments/` - public task fixtures, sequence metadata, and validation helpers.
- `analysis/fold/` - canonical folded v1/v2 analyzer outputs, tables, figures, and
  validation manifests.
- `analysis/investigation-evidence/` - frozen preregistration, fold reports, and
  evidence notes cited by the paper.
- `scripts/` - packaging, analyzer, table, figure, hygiene, and validation scripts.
- `artifact/README.md` - public artifact runbook.

Hidden oracle answers and reference solutions are not included in the public package.
Reviewer-only packages can be built locally with the `--private` packaging mode.

## Manuscript Source

The arXiv manuscript source is distributed through the arXiv upload package and
the GitHub release assets, not as tracked files in this public artifact tree. The
tracked `paper/` directory contains the generated v2 figure PDFs referenced by the
manuscript.

## Rebuild The Public Artifact

```bash
PYTHONPATH=src python3 scripts/package_artifact.py --dist-dir dist
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist --mode public
PYTHONPATH=src python3 scripts/check_v2_artifact_freshness.py
```

This writes:

- `dist/dreambench-swe-artifact.tar.gz`
- `dist/MANIFEST.json`
- `dist/CHECKSUMS.sha256`

Use the reviewer-only mode only for controlled review distribution:

```bash
PYTHONPATH=src python3 scripts/package_artifact.py --private --dist-dir dist-private
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist-private --mode private
```

## Canonical Numbers

All paper numbers must come from the canonical analyzer outputs under `analysis/fold/`.
Do not derive paper claims from partial logs, live progress counters, screenshots, or
manual spreadsheet edits. A live rerun with hosted models is a new experiment even when it
uses the same seeds.

Useful checks:

```bash
PYTHONPATH=src python3 scripts/check_paper_claim_hygiene.py
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist --mode public
PYTHONPATH=src python3 scripts/check_v2_artifact_freshness.py
python3 -m pytest -q
```

## License

Code, scripts, public benchmark fixtures, public analysis artifacts, and packaging
metadata are released under the Apache License 2.0; see `LICENSE`.

The manuscript text and figures are copyright Sarthak Singh. The arXiv-hosted version is
distributed under the license selected during arXiv submission.
