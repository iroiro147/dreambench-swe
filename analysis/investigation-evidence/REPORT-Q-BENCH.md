# Q-BENCH Public Evidence Summary

Status: frozen interpretive evidence for the historical 22-trap v1 fold.

This report records the public, manuscript-relevant conclusions of the Q-BENCH
trap audit. It deliberately excludes raw prompts, hidden scoring material,
reference patches, tool transcripts, local paths, credentials, and agent
runtime metadata. It is an interpretive audit of the frozen trap designs, not a
new analyzer and not a source of v2 paper numbers.

## Evidence snapshot

The audit used the following checked-in inputs. Their SHA-256 digests pin the
version interpreted here.

| Input | SHA-256 |
|---|---|
| `experiments/env/sequences.jsonl` | `e67176617f63145ee2d5e70320b847910004bd5fcfbf26816923c0e787e30845` |
| `src/benchmarks/baselines.py` | `9b07068d1c6ec9953c13fbe955aa833f29d5581cdc93130837bff2233d4098ae` |
| `analysis/investigation-evidence/per_trap_matrix_and_leakage.json` | `8959bbeccd8f17ce17d376fa834edbe36e15a1f74a8643fa609aed7536a90f4e` |
| `analysis/investigation-evidence/PREREGISTRATION.md` | `e1819a8c930a3595727e27d11c4e50349c6dcc0059a3473cb6966996f412ccaf` |

The outcome matrix contains the per-seed B5 and reference-probe typed-only
outcomes used below. Hidden scoring assets are intentionally absent from the
public package.

## Taxonomy boundary

The paper's frozen `recall-verbatim` label is an evidence-location taxonomy. A
trap receives that label when an oracle-critical non-inferable token, format,
or rule appears literally in prior injected event text retained by B5. The
label does **not** mean that the answer code or a complete patch is present.

A stricter semantic copyability review reached two compatible conclusions:

1. Zero traps are pure copy-and-stop tasks; all 22 require repository-specific
   implementation.
2. Prior event text exposes contract-relevant information that B5 can preserve
   for all 22 traps, although the four generated-file/workflow traps require
   additional synthesis or application beyond that text.

The paper therefore retains the preregistered 18/4 evidence-location split and
states its limitation explicitly. A finer judgment-coded semantic split is not
reported as a paper number because it was not frozen as a programmatic label
artifact.

## Per-trap public audit

`B5 S3` and `probe S3` count successful S3 cells across seeds 1, 2, and 3. The
probe column is the historical typed-only reference probe. Descriptions are
redacted to preserve hidden literals while retaining the classification basis.

| Sequence | Paper class | Public classification basis | B5 S3 | Probe S3 |
|---|---|---|---:|---:|
| `config-convention-sections` | Recall-verbatim | Section-list record shape and a redacted schema marker appear in prior event text. | 3/3 | 3/3 |
| `config-freeze-provenance` | Recall-verbatim | The required provenance method and redacted freeze tag appear in prior event text. | 3/3 | 2/3 |
| `config-reviewer-coerce-tag` | Recall-verbatim | The bad-boolean rejection format and a redacted reviewer code appear in prior event text. | 0/3 | 1/3 |
| `config-reviewer-dupkey` | Recall-verbatim | The duplicate-key line-numbered message format appears in prior event text. | 3/3 | 3/3 |
| `config-reviewer-strict-csv` | Recall-verbatim | The strict CSV width message format appears in prior event text. | 3/3 | 3/3 |
| `config-stale-merge` | Recall-verbatim | The deep-merge contract and a redacted provenance token appear in prior event text. | 3/3 | 2/3 |
| `config-stale-schema-id` | Recall-verbatim | The required schema-provenance method and redacted identifier appear in prior event text. | 3/3 | 2/3 |
| `expr-arity-contract` | Recall-verbatim | The arity-error record format appears in prior event text. | 3/3 | 2/3 |
| `expr-convention-opnaming` | Synthesis/apply | An operator-family rule must be applied through the generated-help workflow. | 0/3 | 0/3 |
| `expr-floordiv-category` | Synthesis/apply | A category rule must be applied through the generated-help workflow. | 0/3 | 3/3 |
| `expr-generated-category` | Synthesis/apply | A different category rule must be applied through the generated-help workflow. | 0/3 | 0/3 |
| `expr-generated-precgroup` | Synthesis/apply | A redacted precedence group must be applied through the generated-help workflow. | 0/3 | 1/3 |
| `expr-reviewer-divzero` | Recall-verbatim | A divide-by-zero message shape and signature appear in prior event text. | 3/3 | 1/3 |
| `expr-stale-registry-stability` | Recall-verbatim | A redacted registry-stability value appears in prior event text. | 0/3 | 0/3 |
| `todo-archive-bucket` | Recall-verbatim | A redacted archive-bucket value appears in prior event text. | 3/3 | 3/3 |
| `todo-convention-aggregate` | Recall-verbatim | The aggregate output contract and a redacted batch marker appear in prior event text. | 3/3 | 3/3 |
| `todo-convention-summary` | Recall-verbatim | The summary output contract and a redacted tier token appear in prior event text. | 3/3 | 2/3 |
| `todo-dedupe-keeplowest` | Recall-verbatim | Keep-lowest-id, case-folding, and monotonic-id rules appear across prior events. | 3/3 | 1/3 |
| `todo-flaky-duetiebreak` | Recall-verbatim | The deterministic due-date comparator appears in prior event text. | 3/3 | 3/3 |
| `todo-flaky-monotonic-ids` | Recall-verbatim | The monotonic-id invariant appears in prior event text. | 3/3 | 3/3 |
| `todo-reviewer-export-dialect` | Recall-verbatim | A redacted export marker and CSV dialect appear in prior event text. | 3/3 | 3/3 |
| `todo-reviewer-idformat` | Recall-verbatim | A redacted review-only identifier format appears in prior event text. | 3/3 | 3/3 |

## Aggregate interpretation

Under the paper taxonomy:

- Recall-verbatim: 18 traps, 54 cells; B5 passes 48/54 and the typed-only
  reference probe passes 40/53 after one validity exclusion.
- Synthesis/apply: 4 traps, 12 cells; B5 passes 0/12 and the typed-only
  reference probe passes 4/12.

These are descriptive v1 strata, not a separately powered comparison. B5's
strength is consistent with exact event retention being directly useful in
most v1 traps. It is not evidence that implementation is unnecessary, and B5
still fails several rows despite receiving the relevant event text.

The correct paper-level conclusion is therefore bounded: the v1 benchmark
strongly rewards faithful retention of prior contracts, while the four-trap
synthesis/apply slice is too small for a confirmatory system claim. No trap was
redesigned after observing these outcomes, and the v2 fold reports its own
preregistered null and construct-validity failures without using this audit to
repair them.

## Reproduction boundary

The row outcomes can be checked directly against
`per_trap_matrix_and_leakage.json`. The 18/4 labels and redacted rationales are
also reproduced in `paper/sections/appendix_verbatim.tex`. Re-running hosted
models is not required to verify these recorded counts and would constitute a
new experiment rather than an exact replay.
