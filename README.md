# DreamBench-SWE

This repository's tracked public tree contains the immutable DreamBench-SWE v2 Paper A
`v2.0.5` package: the benchmark harness, public fixtures, folded analyzer outputs,
publication figures, and reference-probe implementation. The current manuscript and its
separately preregistered external-systems successor study are published as additive
[`v2.1.0` release assets](https://github.com/iroiro147/dreambench-swe/releases/tag/v2.1.0),
without rewriting the `v2.0.5` tag or assets.

The paper's headline result is conservative: DreamBench-SWE is the benchmark artifact,
and the evaluated maintenance system is reported only as a reference probe. The v2
confirmatory fold is complete: 60 traps, 3 seeds, 1,890 total result files, 30/30
admitted v2 traps, and canonical analysis generated from `analysis/fold/`.

## Release Map

- [`v2.0.5`](https://github.com/iroiro147/dreambench-swe/releases/tag/v2.0.5)
  is the frozen original-v2 benchmark and Paper A evidence package.
- [`v2.1.0`](https://github.com/iroiro147/dreambench-swe/releases/tag/v2.1.0)
  adds the external-systems successor audit, its sanitized self-contained evidence
  artifact, the updated paper PDF, flat arXiv source, manifest, and detached checksum
  ledger. It preserves every `v2.0.5` asset unchanged.

Use the detached ledger shipped with each release to verify downloaded assets. The
successor artifact includes its own public verifier and selected regression tests; raw
hosted-model logs, credentials, private analyzer inputs, and hidden oracles are excluded.

## What Is Here

- `paper/figures/` - generated manuscript figure PDFs.
- `src/` - typed memory schema, append-preserving store, retrieval gate, maintenance
  operators, benchmark policies, and scoring utilities.
- `experiments/` - public task fixtures, sequence metadata, and validation helpers.
- `analysis/fold/` - canonical folded v1/v2 analyzer outputs, tables, figures, and
  validation manifests.
- `analysis/investigation-evidence/` - frozen preregistration, fold reports, and
  every concrete evidence file cited by the manuscript.
- `scripts/` - packaging, analyzer, table, figure, hygiene, and validation scripts.
- `artifact/README.md` - public artifact runbook.

Hidden oracle answers and reference solutions are not included in the public package.
Reviewer-only packages can be built locally with the `--private` packaging mode.

## Manuscript Source

The arXiv manuscript source is distributed through the arXiv upload package and
the GitHub release assets, not as tracked files in this public artifact tree. The
tracked `paper/` directory contains the generated v2 figure PDFs referenced by the
manuscript.

## Verify An Unpacked Public Artifact

The public tarball is self-contained for smoke tests, package validation,
public-tree auditing, table regeneration, and the selected public test suite.
From the unpacked artifact root, run:

```bash
python3 -m pip install -r requirements-artifact.txt
PYTHONPATH=src python3 scripts/validate_submission_package.py --package-dir . --mode public
python3 scripts/audit_public_tree.py --root .
PYTHONPATH=src python3 scripts/generate_tables_v2.py \
  --input analysis/fold/v2_fold.json \
  --output /tmp/dreambench-swe-v2-tables.tex
python3 scripts/run_smoke.py
```

The integrity checks intentionally run before the smoke command because the
smoke writes local outputs under `experiments/results/smoke/`. The complete
selected public test command is listed in `artifact/README.md`.

## Rebuild The Public Artifact

Install the verification dependency and reuse the release timestamp recorded in
the shipped manifest.  Set `DREAMBENCH_RELEASE_MANIFEST=MANIFEST.json` when
rebuilding from inside the unpacked artifact root.

```bash
python3 -m pip install -r requirements-artifact.txt
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

Use the reviewer-only mode only for controlled review distribution:

```bash
PYTHONPATH=src python3 scripts/package_artifact.py --private --dist-dir dist-private
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist-private --mode private
```

## Publisher-Only Release Gates

The following checks run only in the full source checkout. The freshness guard
requires the raw result and grid-log roots; the other checks require manuscript
or release surfaces. Those inputs are intentionally absent from the unpacked
public artifact. `check_release_coherence.py` requires `pdftotext` from Poppler
(`brew install poppler` on macOS or install `poppler-utils` on Debian or Ubuntu).

```bash
PYTHONPATH=src python3 scripts/check_v2_artifact_freshness.py
python3 scripts/check_arxiv_abstract.py --path paper/sections/01_abstract.tex
python3 scripts/check_paper_public_evidence.py
python3 scripts/check_release_coherence.py
```

## Canonical Numbers

All paper numbers must come from the canonical analyzer outputs under `analysis/fold/`.
Do not derive paper claims from partial logs, live progress counters, screenshots, or
manual spreadsheet edits. A live rerun with hosted models is a new experiment even when it
uses the same seeds.

Additional full-source-checkout checks:

```bash
python3 -m pip install -r requirements-artifact.txt
PYTHONPATH=src python3 scripts/check_paper_claim_hygiene.py
python3 scripts/audit_public_tree.py --archive dist/dreambench-swe-artifact.tar.gz
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir dist --mode public
PYTHONPATH=src python3 scripts/check_v2_artifact_freshness.py
python3 -m pytest -q
```

## License

Code, scripts, public benchmark fixtures, public analysis artifacts, and packaging
metadata are released under the Apache License 2.0; see `LICENSE`.

The manuscript text and figures are copyright Sarthak Singh. The arXiv-hosted version is
distributed under the license selected during arXiv submission.

## Community and maintenance

- Start with [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a substantial change.
- Use the issue templates for reproducibility bugs, methodology concerns, and trap proposals.
- Report restricted-artifact exposure and security problems through [SECURITY.md](SECURITY.md).
- Cite the benchmark using [CITATION.cff](CITATION.cff); the canonical paper identifier will be added after publication.
- Planned public improvements are tracked in [ROADMAP.md](ROADMAP.md).
- The repository's public CI selection is recorded in [public-tests.txt](public-tests.txt); publisher-only tests may require intentionally absent private inputs.
