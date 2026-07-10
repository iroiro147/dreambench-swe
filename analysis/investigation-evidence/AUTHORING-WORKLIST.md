# DreamBench-SWE v2 Trap-Authoring Worklist

Scope: net-new skeletons needed to reach the frozen DreamBench-SWE v2 construct quota after carrying forward the 22 v1 traps and the 8 synth-pilot traps.

Inputs read for this worklist:

- `analysis/investigation-evidence/PREREGISTRATION-V2.md`
- `analysis/investigation-evidence/FABLE-CONSTRUCTS.md`
- `paper/sections/appendix_verbatim.tex`
- `experiments/env/sequences_synth.jsonl`
- `docs/trap_skeleton_spec.md`
- `analysis/investigation-evidence/TRAP-PIPELINE-PLAN.md` Part B

Bias rule: this worklist uses construct/family/source-shape information only. New authors may know the aggregate v1 and synth-pilot facts disclosed in `PREREGISTRATION-V2.md`, but must not consult per-condition v1 outcome data while drafting individual v2 traps.

## Mapping Rule

- The 18 v1 traps audited as recall-verbatim map to C1. This includes all v1 `flaky-test` rows because `PREREGISTRATION-V2.md` explicitly says v1 flaky-test traps audited as recall-verbatim must be labeled C1 unless redesigned and revalidated.
- The 4 v1 synthesis/apply generated-help traps map to C8 because the load-bearing behavior is procedural/source-of-truth discipline: update the registry/generator, regenerate generated help, and avoid artifact-only edits.
- The 8 synth-pilot traps are mapped by their primary insufficiency mechanism:
  - C7 when the S3 answer is composed from rule + parameter/runtime data split across events.
  - C3 when supersession/stale suppression is primary.
  - C4 when a superseded fact must propagate to a derived later location.
  - C6 when non-temporal authority/provenance conflict is primary.

## Existing Trap Construct Map

| source | seq_id | repo | family | construct | mapping rationale |
|---|---|---|---|---|---|
| v1 | config-convention-sections | configly | convention-learning | C1 | Recall-verbatim appendix row: section-list/schema marker appears in prior event text. |
| v1 | config-freeze-provenance | configly | stale-architecture | C1 | Recall-verbatim appendix row: exact `freeze_tag()` token is stated in event text. |
| v1 | config-reviewer-coerce-tag | configly | reviewer-preference | C1 | Recall-verbatim appendix row: exact bad-bool rejection format is stated in event text. |
| v1 | config-reviewer-dupkey | configly | reviewer-preference | C1 | Recall-verbatim appendix row: duplicate-key line-numbered format is stated in event text. |
| v1 | config-reviewer-strict-csv | configly | reviewer-preference | C1 | Recall-verbatim appendix row: strict CSV width message format is stated in event text. |
| v1 | config-stale-merge | configly | stale-architecture | C1 | Recall-verbatim appendix row: deep-merge source token is stated in event text. |
| v1 | config-stale-schema-id | configly | stale-architecture | C1 | Recall-verbatim appendix row: exact `schema_id()` token is stated in event text. |
| v1 | expr-arity-contract | exprmini | reviewer-preference | C1 | Recall-verbatim appendix row: exact arity error format is stated in event text. |
| v1 | expr-reviewer-divzero | exprmini | reviewer-preference | C1 | Recall-verbatim appendix row: exact divzero message/signature are stated in event text. |
| v1 | expr-stale-registry-stability | exprmini | stale-architecture | C1 | Recall-verbatim appendix row: registry stability token is stated in event text. |
| v1 | todo-archive-bucket | todolite | convention-learning | C1 | Recall-verbatim appendix row: archive bucket token is stated in event text. |
| v1 | todo-convention-aggregate | todolite | convention-learning | C1 | Recall-verbatim appendix row: exact `STATS` contract and marker are stated in event text. |
| v1 | todo-convention-summary | todolite | convention-learning | C1 | Recall-verbatim appendix row: exact `TOP` summary contract and tier token are stated in event text. |
| v1 | todo-dedupe-keeplowest | todolite | flaky-test | C1 | Recall-verbatim appendix row plus prereg rule that v1 flaky-test traps audited RV remain C1. |
| v1 | todo-flaky-duetiebreak | todolite | flaky-test | C1 | Recall-verbatim appendix row plus prereg rule that v1 flaky-test traps audited RV remain C1. |
| v1 | todo-flaky-monotonic-ids | todolite | flaky-test | C1 | Recall-verbatim appendix row plus prereg rule that v1 flaky-test traps audited RV remain C1. |
| v1 | todo-reviewer-export-dialect | todolite | reviewer-preference | C1 | Recall-verbatim appendix row: export dialect marker and CSV dialect are stated in event text. |
| v1 | todo-reviewer-idformat | todolite | reviewer-preference | C1 | Recall-verbatim appendix row: review-only id format is stated in event text. |
| v1 | expr-convention-opnaming | exprmini | convention-learning | C8 | Synthesis/apply appendix row; generated operator-help workflow is load-bearing. |
| v1 | expr-floordiv-category | exprmini | generated-files | C8 | Synthesis/apply appendix row; generated-help source-of-truth workflow is load-bearing. |
| v1 | expr-generated-category | exprmini | generated-files | C8 | Synthesis/apply appendix row; generated-help source-of-truth workflow is load-bearing. |
| v1 | expr-generated-precgroup | exprmini | generated-files | C8 | Synthesis/apply appendix row; generated-help source-of-truth workflow is load-bearing. |
| synth | synth-config-schema-audit-code | configly | convention-learning / composition-channel | C7 | S3 record composes envelope/prefix from one event with missing-count formula/suffix and runtime counts from another. |
| synth | synth-config-csv-width-delta | configly | reviewer-preference / apply-derived-rule | C7 | S3 error output derives prefix + physical-line semantics + width delta/polarity formula; no single event contains the decisive literals. |
| synth | synth-config-dupsec-code | configly | reviewer-preference / provenance-conflict | C6 | S3 must resolve class-specific suffix/provenance instead of inventing or reusing a conflicting class suffix. |
| synth | synth-expr-bitwise-help | exprmini | generated-files / apply-derived-rule | C7 | S3 combines family token/name pattern with computed mask formula and runtime operator metadata. |
| synth | synth-expr-precedence-derive | exprmini | stale-architecture / update-propagation | C4 | Superseded anchor precedence must propagate to a derived `>>` location and repair the anchor consistently. |
| synth | synth-expr-help-epoch | exprmini | generated-files / staleness-supersession | C3 | S3 must suppress stale epoch while preserving template/format/live-count rule through generated help. |
| synth | synth-todo-overdue-handoff | todolite | reviewer-preference / composition-channel | C7 | S3 composes DUE prefix, fixed-anchor rule, RID format, anchor date, lane formula, and runtime due dates. |
| synth | synth-todo-report-channel | todolite | reviewer-preference / staleness-supersession | C3 | S3 must suppress stale quarter code and compose the active channel with runtime task fields. |

## Net-New Quota

| construct | target quota | existing mapped count | net-new skeletons |
|---|---:|---:|---:|
| C1 | 18 | 18 | 0 |
| C2 | 6 | 0 | 6 |
| C3 | 7 | 2 | 5 |
| C4 | 1 | 1 | 0 |
| C5 | 6 | 0 | 6 |
| C6 | 5 | 1 | 4 |
| C7 | 7 | 4 | 3 |
| C8 | 4 | 4 | 0 |
| C9 | 4 | 0 | 4 |
| C10 | 2 | 0 | 2 |
| **Total** | **60** | **30** | **30** |

Fixture-repo load in the net-new worklist: `configly=10`, `exprmini=10`, `todolite=10`.

Clean-start S3 count in the net-new worklist: `13` rows have `clean_start_s3=true`, satisfying the preregistered `>=10` floor.

## Author Batches and File Ownership

Each author batch owns only skeleton files under its own directory. No batch may edit shared aggregate files, candidate JSONL files, or another batch's directory.

| author_batch | owned path | skeleton count |
|---|---|---:|
| batch-a | `experiments/env/skeletons/batch-a/` | 5 |
| batch-b | `experiments/env/skeletons/batch-b/` | 5 |
| batch-c | `experiments/env/skeletons/batch-c/` | 5 |
| batch-d | `experiments/env/skeletons/batch-d/` | 5 |
| batch-e | `experiments/env/skeletons/batch-e/` | 5 |
| batch-f | `experiments/env/skeletons/batch-f/` | 5 |

Shared files are integrator-only after gate pass. Authors do not write `experiments/env/sequences_synth.jsonl`, `experiments/env/sequences.jsonl`, `tests/`, shared harness scripts, existing oracles/refsols, or fixture repo seeds.

## Net-New Skeleton Worklist

### batch-a

| skeleton_id | construct | family | fixture repo | clean_start_s3 | design intent | CSPRNG placeholder-class hints |
|---|---|---|---|---:|---|---|
| v2-c2-config-retrieval-interference-csv-prefix-01 | C2 | reviewer-preference / retrieval-interference | configly | false | Strict CSV validation has 9 same-vocabulary injected events about `parse_csv`, line numbers, widths, and prefixes; exactly 1 event carries the load-bearing prefix/format and 8 are distractors, while the read budget should admit about 2 events. The oracle requires selecting the relevant CSV event and rejects nearby INI/section-prefix distractors. | `SECRET_V2_C2_CONFIG_CSV_PREFIX: marker-row C2CSV-{{5A}}-{{3D}}`; `SECRET_V2_C2_CONFIG_CSV_DISTRACTOR_*: marker-row C2CFG-D{{3D}}-{{4U}}` |
| v2-c2-expr-retrieval-interference-operator-family-01 | C2 | generated-files / retrieval-interference | exprmini | true | Generated operator help has 9 injected events sharing `operators.py`, `operator_help`, registry, family, and arity language; exactly 1 event names the active family marker and 8 are plausible family/operator distractors. The oracle requires the active family in generated help and fails a wrong retrieved family even if the generator workflow is otherwise correct. | `SECRET_V2_C2_EXPR_FAMILY_ACTIVE: marker-row FAM-{{5A}}-{{3D}}`; `SECRET_V2_C2_EXPR_FAMILY_DISTRACTOR_*: marker-row FAM-D{{3D}}-{{4U}}` |
| v2-c2-todo-retrieval-interference-review-lane-01 | C2 | reviewer-preference / retrieval-interference | todolite | false | Review-lane CLI output has 9 injected reviewer handoff notes with the same `review`, `lane`, `RID`, and task-output vocabulary; exactly 1 note carries the active lane marker and 8 are same-surface distractors. The S3 prompt mentions reviewer handoff but not the marker, forcing precise retrieval rather than hoarding. | `SECRET_V2_C2_TODO_LANE_ACTIVE: marker-row LANE-{{5A}}-{{3D}}`; `SECRET_V2_C2_TODO_LANE_DISTRACTOR_*: marker-row LANE-D{{3D}}-{{4U}}` |
| v2-c2-config-retrieval-interference-schema-marker-02 | C2 | convention-learning / retrieval-interference | configly | true | Schema-audit helper events inject 10 config/schema/convention memories, exactly 1 containing the active schema marker and 9 using the same normalized-path vocabulary as distractors. The hidden oracle checks the chosen marker and a runtime path count, so retention is easy but selecting the one relevant memory under budget is the construct. | `SECRET_V2_C2_CONFIG_SCHEMA_ACTIVE: marker-row SCHEMA-{{5A}}-{{3D}}`; `SECRET_V2_C2_CONFIG_SCHEMA_DISTRACTOR_*: marker-row SCHEMA-D{{3D}}-{{4U}}` |
| v2-c2-expr-retrieval-interference-help-footer-02 | C2 | generated-files / retrieval-interference | exprmini | false | Operator-help footer work injects 8 footer/version/epoch events with shared `SUMMARY`, generated-help, and registry-count language; exactly 1 event binds the active footer code, and the rest are decoy footer formats. The oracle requires the active event and generator regeneration while wrong footer codes are trap-tripped. | `SECRET_V2_C2_EXPR_FOOTER_ACTIVE: marker-row SUM-{{5A}}-{{3D}}`; `SECRET_V2_C2_EXPR_FOOTER_DISTRACTOR_*: marker-row SUM-D{{3D}}-{{4U}}` |

### batch-b

| skeleton_id | construct | family | fixture repo | clean_start_s3 | design intent | CSPRNG placeholder-class hints |
|---|---|---|---|---:|---|---|
| v2-c2-todo-retrieval-interference-export-marker-02 | C2 | reviewer-preference / retrieval-interference | todolite | false | Export dialect has 9 injected events sharing CSV/export/reviewer wording; exactly 1 event contains the active export marker/dialect and 8 are plausible but wrong dialect memories. The oracle requires the selected marker plus runtime rows, and fails any distractor marker or ordinary TASK pipe output. | `SECRET_V2_C2_TODO_EXPORT_ACTIVE: marker-row EXPORT-{{5A}}-{{3D}}`; `SECRET_V2_C2_TODO_EXPORT_DISTRACTOR_*: marker-row EXPORT-D{{3D}}-{{4U}}` |
| v2-c3-config-supersede-schema-envelope-01 | C3 | stale-architecture / staleness-supersession | configly | true | S1 repeats a stale schema-audit envelope 3+ times with high lexical overlap to the S3 prompt; S2 supersedes only one component in a short reviewer note. The oracle requires the new envelope and explicitly fails any diff/output containing the old marker, making stale suppression load-bearing. | `SECRET_V2_C3_CONFIG_SCHEMA_STALE: marker-row OLDENV-{{5A}}-{{3D}}`; `SECRET_V2_C3_CONFIG_SCHEMA_ACTIVE: marker-row NEWENV-{{5A}}-{{3D}}` |
| v2-c3-expr-supersede-epoch-anti-recency-01 | C3 | generated-files / staleness-supersession | exprmini | false | S1 repeats stale operator-help epoch language; S2 supersedes it once, and a newer S2-late event is an irrelevant operator refactor so a newest-event heuristic selects the wrong memory. The oracle requires the superseded active epoch through the generator and fails stale epoch text in either source or generated artifact. | `SECRET_V2_C3_EXPR_EPOCH_STALE: marker-row EPOCHOLD-{{5A}}-{{3D}}`; `SECRET_V2_C3_EXPR_EPOCH_ACTIVE: marker-row EPOCHNEW-{{5A}}-{{3D}}` |
| v2-c3-todo-supersede-channel-anti-recency-01 | C3 | reviewer-preference / staleness-supersession | todolite | true | S1 makes an old report channel salient in multiple notes; S2 replaces it once, then the newest injected event concerns unrelated search output and mentions the old noun vocabulary without the active channel. The S3 report oracle requires the active channel and omission-checks the stale channel, defeating pure recency. | `SECRET_V2_C3_TODO_CHANNEL_STALE: marker-row CHOLD-{{5A}}-{{3D}}`; `SECRET_V2_C3_TODO_CHANNEL_ACTIVE: marker-row CHNEW-{{5A}}-{{3D}}` |
| v2-c3-config-supersede-error-suffix-02 | C3 | reviewer-preference / staleness-supersession | configly | false | A stale strict-parse suffix is repeated in S1 examples and is lexically closer to S3's duplicate-field task than the active replacement; S2 supersedes only the suffix. The oracle requires preserving the reusable template while replacing only the stale component, and fails the old suffix anywhere in the error contract. | `SECRET_V2_C3_CONFIG_SUFFIX_STALE: marker-row SUFOLD-{{5A}}-{{3D}}`; `SECRET_V2_C3_CONFIG_SUFFIX_ACTIVE: marker-row SUFNEW-{{5A}}-{{3D}}` |

### batch-c

| skeleton_id | construct | family | fixture repo | clean_start_s3 | design intent | CSPRNG placeholder-class hints |
|---|---|---|---|---:|---|---|
| v2-c3-expr-supersede-family-token-02 | C3 | generated-files / staleness-supersession | exprmini | false | An old operator-family token is repeated in S1 and appears in a newest irrelevant cleanup note, while the actual supersession appears once in the second-newest event. The oracle checks generated help for the active token and fails old-token use, satisfying the anti-recency subset. | `SECRET_V2_C3_EXPR_FAMILY_STALE: marker-row FOLD-{{5A}}-{{3D}}`; `SECRET_V2_C3_EXPR_FAMILY_ACTIVE: marker-row FNEW-{{5A}}-{{3D}}` |
| v2-c5-config-scope-csv-vs-ini-01 | C5 | reviewer-preference / scope-discipline | configly | true | Reviewer feedback says a special quoting marker applies only to `parse_csv` strict errors, while S3 asks for an INI strict-mode rejection where the repo default must remain unquoted. The oracle includes an in-scope CSV check and an out-of-scope INI omission check, so indiscriminate memory use is harmful. | `SECRET_V2_C5_CONFIG_CSV_MARKER: marker-row CSVONLY-{{5A}}-{{3D}}`; `SECRET_V2_C5_CONFIG_INI_SENTINEL: marker-row INIDEF-{{5A}}-{{3D}}` |
| v2-c5-expr-scope-generated-help-vs-evaluator-01 | C5 | generated-files / scope-discipline | exprmini | false | A reviewer preference for a debug marker applies only to generated operator help lines, not evaluator error strings. S3 touches evaluator behavior and a small generated-help sibling check; the oracle fails if the help-only marker is overgeneralized into runtime evaluator output. | `SECRET_V2_C5_EXPR_HELP_MARKER: marker-row HELPONLY-{{5A}}-{{3D}}`; `SECRET_V2_C5_EXPR_EVAL_SENTINEL: marker-row EVALDEF-{{5A}}-{{3D}}` |
| v2-c5-todo-scope-export-vs-list-01 | C5 | reviewer-preference / scope-discipline | todolite | true | A bulk-export CSV dialect applies only to `export`, while S3 adds `list --format compact` where normal pipe records must remain intact. The oracle checks that export still uses the special marker in scope and list does not import the export marker/dialect out of scope. | `SECRET_V2_C5_TODO_EXPORT_MARKER: marker-row EXPONLY-{{5A}}-{{3D}}`; `SECRET_V2_C5_TODO_LIST_SENTINEL: marker-row LISTDEF-{{5A}}-{{3D}}` |
| v2-c5-config-scope-schema-vs-runtime-02 | C5 | convention-learning / scope-discipline | configly | true | A schema audit namespace prefix applies only to persisted schema manifests, not runtime `Config.get_path()` results. S3 asks for a runtime helper plus a manifest side check; applying the namespace everywhere fails the out-of-scope runtime assertions. | `SECRET_V2_C5_CONFIG_SCHEMA_PREFIX: marker-row MANONLY-{{5A}}-{{3D}}`; `SECRET_V2_C5_CONFIG_RUNTIME_SENTINEL: marker-row RUNDEF-{{5A}}-{{3D}}` |

### batch-d

| skeleton_id | construct | family | fixture repo | clean_start_s3 | design intent | CSPRNG placeholder-class hints |
|---|---|---|---|---:|---|---|
| v2-c5-expr-scope-bitwise-vs-arithmetic-02 | C5 | convention-learning / scope-discipline | exprmini | true | A family-token rule applies to bitwise operators only; S3 adds an arithmetic operator whose generated help must not get the bitwise token, plus an in-scope bitwise assertion. The oracle fails overgeneralized family tagging on arithmetic output while preserving in-scope use. | `SECRET_V2_C5_EXPR_BITWISE_TOKEN: marker-row BITONLY-{{5A}}-{{3D}}`; `SECRET_V2_C5_EXPR_ARITH_SENTINEL: marker-row ARITHDEF-{{5A}}-{{3D}}` |
| v2-c5-todo-scope-review-vs-normal-02 | C5 | reviewer-preference / scope-discipline | todolite | false | Reviewer-only id formatting applies only to `review`/handoff commands, while S3 modifies normal `done` and `list` behavior. The oracle requires reviewer id formatting in the scoped handoff path and fails any migration of ordinary CLI ids to reviewer ids. | `SECRET_V2_C5_TODO_REVIEW_ID: id-format RID-{{5A}}-{{3D}}`; `SECRET_V2_C5_TODO_NORMAL_SENTINEL: marker-row NORM-{{5A}}-{{3D}}` |
| v2-c6-config-authority-doc-vs-reviewer-01 | C6 | reviewer-preference / provenance-conflict | configly | false | S1 docs and S2 reviewer note specify conflicting strict-error codes in the same effective time frame; a third event states that module-local docs override reviewer notes for parser internals. The S3 task is in the docs-over-reviewer module, so the oracle requires the doc code and fails recency-based reviewer selection. | `SECRET_V2_C6_CONFIG_DOC_CODE: marker-row DOCCFG-{{5A}}-{{3D}}`; `SECRET_V2_C6_CONFIG_REVIEW_CODE: marker-row REVCFG-{{5A}}-{{3D}}`; `SECRET_V2_C6_CONFIG_PRECEDENCE: marker-row PREC-{{5A}}-{{3D}}` |
| v2-c6-expr-authority-registry-vs-doc-01 | C6 | stale-architecture / provenance-conflict | exprmini | true | Operator docs and registry comment conflict on a precedence-band token, and a separate authority rule says the registry is canonical for generated help while docs are explanatory. The oracle requires registry-derived behavior and fails arbitrary/recency choice of the doc token. | `SECRET_V2_C6_EXPR_DOC_TOKEN: marker-row DOCOPE-{{5A}}-{{3D}}`; `SECRET_V2_C6_EXPR_REG_TOKEN: marker-row REGOPE-{{5A}}-{{3D}}`; `SECRET_V2_C6_EXPR_AUTHORITY: marker-row AUTH-{{5A}}-{{3D}}` |
| v2-c6-todo-authority-conventions-vs-reviewer-01 | C6 | reviewer-preference / provenance-conflict | todolite | false | `CONVENTIONS.md` and a reviewer note conflict on priority report shape, and a third event says reviewer notes override conventions only for handoff commands, not default reports. S3 adds a default report, so the oracle requires convention behavior and fails unscoped reviewer precedence. | `SECRET_V2_C6_TODO_CONVENTION_TOKEN: marker-row CONVTD-{{5A}}-{{3D}}`; `SECRET_V2_C6_TODO_REVIEW_TOKEN: marker-row REVTD-{{5A}}-{{3D}}`; `SECRET_V2_C6_TODO_AUTHORITY: marker-row AUTH-{{5A}}-{{3D}}` |

### batch-e

| skeleton_id | construct | family | fixture repo | clean_start_s3 | design intent | CSPRNG placeholder-class hints |
|---|---|---|---|---:|---|---|
| v2-c6-expr-authority-two-reviewers-02 | C6 | reviewer-preference / provenance-conflict | exprmini | false | Two reviewer events conflict on generated-help field order without temporal resolution, and a separate provenance rule says release-manager notes override drive-by review notes for generator output. The oracle requires applying the precedence rule, not newest/oldest choice, and omission-checks the losing order. | `SECRET_V2_C6_EXPR_REVIEWER_A: marker-row RA-{{5A}}-{{3D}}`; `SECRET_V2_C6_EXPR_REVIEWER_B: marker-row RB-{{5A}}-{{3D}}`; `SECRET_V2_C6_EXPR_PRECEDENCE: marker-row RELMGR-{{5A}}-{{3D}}` |
| v2-c7-config-compose-provenance-code-01 | C7 | convention-learning / composition-channel | configly | true | One event gives a provenance envelope and another gives an audit-code stem plus a runtime checksum rule; S3 must compose the final manifest line from both memories and current config keys. No single event contains the decisive S3 line as a contiguous literal. | `SECRET_V2_C7_CONFIG_ENVELOPE: marker-row PROV-{{5A}}-{{3D}}`; `SECRET_V2_C7_CONFIG_CODE_STEM: marker-row PCODE-{{5A}}-{{3D}}`; `SECRET_V2_C7_CONFIG_KEY: config key cfg_{{4L}}{{4L}}` |
| v2-c7-expr-compose-mask-window-01 | C7 | generated-files / apply-derived-rule | exprmini | false | One event defines a generated-help window formula and another supplies the operator-family parameter; S3 adds a new operator whose mask/window line must be derived from both plus runtime precedence/arity. The composed literal appears nowhere in any single event. | `SECRET_V2_C7_EXPR_WINDOW_STEM: marker-row WIN-{{5A}}-{{3D}}`; `SECRET_V2_C7_EXPR_FAMILY_PARAM: marker-row PAR-{{5A}}-{{3D}}` |
| v2-c7-todo-compose-priority-lane-01 | C7 | reviewer-preference / composition-channel | todolite | false | One event gives a priority-report envelope and another gives the quarter/team lane rule; S3 must compose per-task output from both memories and runtime priority/id/title data. The oracle fails if either event alone is copied without composition. | `SECRET_V2_C7_TODO_ENVELOPE: marker-row PRI-{{5A}}-{{3D}}`; `SECRET_V2_C7_TODO_LANE_STEM: marker-row LNSTEM-{{5A}}-{{3D}}` |
| v2-c9-config-spurious-parse-date-01 | C9 | flaky-test / spurious-lesson | configly | true | S1 includes a one-off environment-dependent failure plus a wrong injected causal story that date parsing is broken and should be routed around; S2 shows the same date parse path passing without code change. S3 naturally uses date parsing, and the oracle fails workaround/avoidance patterns while passing straightforward parser use. | `SECRET_V2_C9_CONFIG_FALSE_CAUSE: marker-row FALSE-{{5A}}-{{3D}}`; `SECRET_V2_C9_CONFIG_DISCONFIRM: marker-row DISC-{{5A}}-{{3D}}` |

### batch-f

| skeleton_id | construct | family | fixture repo | clean_start_s3 | design intent | CSPRNG placeholder-class hints |
|---|---|---|---|---:|---|---|
| v2-c9-expr-spurious-float-divide-01 | C9 | flaky-test / spurious-lesson | exprmini | false | S1 records a transient floating-division failure and a false lesson to avoid the normal evaluator path; S2 demonstrates the evaluator path passing unchanged. S3 adds adjacent arithmetic behavior, and the oracle fails defensive bypasses while passing direct use of the normal evaluator. | `SECRET_V2_C9_EXPR_FALSE_CAUSE: marker-row FALSE-{{5A}}-{{3D}}`; `SECRET_V2_C9_EXPR_DISCONFIRM: marker-row DISC-{{5A}}-{{3D}}` |
| v2-c9-todo-spurious-due-parser-01 | C9 | flaky-test / spurious-lesson | todolite | true | S1 records an order-dependent due-date failure and a wrong memory that due parsing should be avoided; S2 repeats the due-date path successfully with no code change. S3 asks for due-date search/filtering, and the oracle fails string-workaround logic that acts on the disconfirmed lesson. | `SECRET_V2_C9_TODO_FALSE_CAUSE: marker-row FALSE-{{5A}}-{{3D}}`; `SECRET_V2_C9_TODO_DISCONFIRM: marker-row DISC-{{5A}}-{{3D}}` |
| v2-c9-todo-spurious-archive-order-02 | C9 | flaky-test / spurious-lesson | todolite | false | S1 asserts a false causal story that archive ordering is nondeterministic and should be stabilized with sleeps/retries; S2 shows deterministic archive order without changes. S3 modifies archive listing, and the oracle fails retry/sleep/randomization workarounds while passing the simple deterministic implementation. | `SECRET_V2_C9_TODO_ARCHIVE_FALSE: marker-row FALSE-{{5A}}-{{3D}}`; `SECRET_V2_C9_TODO_ARCHIVE_DISCONFIRM: marker-row DISC-{{5A}}-{{3D}}` |
| v2-c10-config-abstain-no-prior-token-01 | C10 | convention-learning / abstention-probe | configly | true | S3 prompt is written in the same rhythm as token-memory traps, but no prior event bears on the requested config helper. The oracle passes a normal repo-consistent implementation and fails fabricated benchmark-style markers, repo-foreign conventions, or imported unrelated memory tokens. | `SECRET_V2_C10_CONFIG_FORBIDDEN_PATTERN: marker-row FORBID-{{5A}}-{{3D}}`; `SECRET_V2_C10_CONFIG_CANARY_PREFIX: marker-row CANARY-{{5A}}-{{3D}}` |
| v2-c10-todo-abstain-no-review-memory-01 | C10 | reviewer-preference / abstention-probe | todolite | false | S3 asks for a mundane todo command that resembles reviewer-handoff tasks, but prior events are irrelevant to that command and contain no applicable token. The oracle fails hallucinated RID/export/lane-style markers and passes only the simple visible-repo convention. | `SECRET_V2_C10_TODO_FORBIDDEN_PATTERN: marker-row FORBID-{{5A}}-{{3D}}`; `SECRET_V2_C10_TODO_CANARY_PREFIX: marker-row CANARY-{{5A}}-{{3D}}` |

## Batch Pipeline

Every batch follows the same pipeline:

```text
skeleton
-> inject_secrets
-> batch_validate --dry
-> live gate on VPS
-> admit
```

Concrete operating rules:

1. Authors draft skeleton bundles only under their owned `experiments/env/skeletons/<batch>/` directory. Skeletons contain placeholders, never final non-inferable tokens.
2. Run `python3 scripts/inject_secrets.py <skeleton-dir> <finalized-output-dir>` so Python `secrets` generates all non-inferable markers, ids, and oracle literals. The generated `secret_provenance.json` records format classes and hashes, not generated values.
3. Run batch dry validation before live work. The batch report must include schema/manifest checks, no-single-event or construct-specific gate output, refsol/oracle checks, hiddenness checks, and clean-start metadata.
4. Run live gate per sequence on the VPS, not as one monolithic 30-trap live job. Each candidate must pass `experiments/validate_trap.py --live` before an integrator can admit it.
5. The integrator admits only live-gated candidates into the aggregate sequence file and freeze manifest. Rejected candidates retain first failing gate and repair/discard status.
6. No author may inspect per-condition v1 outcome data, per-trap DF/B5 outcome matrices, early v2 memory-bearing S3 outcomes, construct slices, or condition-specific pilot outcomes while drafting. If deduplication requires maintainer access to old per-trap matrices, that access is logged and not shared with authors.
