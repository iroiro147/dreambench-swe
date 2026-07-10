# Datasheet for DreamBench-SWE v2

Status: release datasheet for the frozen DreamBench-SWE v2 confirmatory fold.
This document follows the Datasheets for Datasets structure introduced by
Gebru et al. It describes the benchmark dataset and its governance, not the
experimental findings. Live v2 performance results are frozen in the canonical
analyzer artifacts under `analysis/fold/`; this datasheet points to those
artifacts rather than restating paper numbers.

Primary source documents for numeric claims in this datasheet:

- `analysis/investigation-evidence/PREREGISTRATION-V2.md`
- `analysis/investigation-evidence/AUTHORING-WORKLIST.md`
- `analysis/investigation-evidence/V2-ANALYSIS-20260708T065236Z.md`
- `analysis/fold/v2_fold.json`
- `analysis/fold/v2_confirmatory_clustered.json`
- `analysis/fold/admission_funnel.md`

## Motivation

### Why was the dataset created?

DreamBench-SWE v2 was created to evaluate memory hygiene in long-horizon
software-engineering agents. Its purpose is to test whether an agent can use
prior-session information when it is relevant, reject or suppress it when it is
stale, scoped, contradicted, disconfirmed, or irrelevant, and apply procedural
memory through the repository's intended source-of-truth path.

The dataset is a scaled confirmatory fold for the DreamBench-SWE paper. It
addresses known limitations of the earlier v1 fold: too few traps,
too much recall-verbatim structure, seed concentration, insufficient
anti-hoarding pressure, and dependence on one wake model. The v2 design is
frozen before v2 outcome inspection, and the preregistration states the binding
rule: no new v2 trap may be authored after looking at any v2 performance result.

### What problem does it study?

The dataset studies multi-session memory traps in software tasks. A trap is a
three-session sequence in which S1 and S2 provide prior code work and memory
events, while S3 is the scored task. The S3 task is designed so that success
requires the intended memory behavior and hidden oracle checks distinguish the
correct behavior from common failure modes such as verbatim hoarding, stale
activation, overgeneralization, invented benchmark markers, or ignoring a
source-of-truth workflow.

The primary measurement target is memory hygiene, not raw programming ability.
DreamBench-SWE v2 should not be treated as a general SWE-capability benchmark,
a broad production-readiness benchmark, or a general-purpose coding-agent
leaderboard.

### Who created the dataset?

The benchmark is part of the DreamBench-SWE confirmatory repository. The v2
protocol uses LLM-authored trap skeletons, CSPRNG secret injection, machine
validation, and human audit. Each final trap is required to record an
`author_model` in the manifest; authoring and admission reporting are recorded
in `analysis/fold/admission_funnel.md` and the canonical fold artifacts.

### Who funded or supported dataset creation?

The repository sources read for this datasheet do not specify a funding source.

## Composition

### What does one dataset instance contain?

The statistical cluster is one trap: a three-session software-engineering
sequence with S1, S2, and S3. S3 is the scored memory trap. A complete trap
record includes the session prompts/events, fixture repository identity,
construct label, family label, clean-start flag, hidden oracle identity or hash,
reference-solution identity or hash, CSPRNG secret provenance, authoring
metadata, and validation metadata.

For ordinary continuation traps, S2 starts from S1's scored production tree and
S3 starts from S2's scored production tree. For clean-start S3 traps, S3 starts
from the frozen clean base for the fixture repository while memory-bearing
conditions still receive admissible memory context from S1 and S2.

### How many instances are in the dataset?

The v2 target is 60 valid three-session traps. With the preregistered seed
schedule of seeds `1`, `2`, and `3`, a complete condition has:

```text
60 traps x 3 seeds = 180 S3 cells per complete condition
```

The target composition is:

```text
22 v1 traps + 8 synth-pilot traps + approximately 30 new traps = 60 traps
```

The authoring worklist specifies 30 net-new skeletons. Those 30 are split
evenly across the three fixture repositories:

| Fixture repository | Net-new skeleton count |
|---|---:|
| `configly` | 10 |
| `exprmini` | 10 |
| `todolite` | 10 |

The net-new worklist contains 13 rows with `clean_start_s3=true`, satisfying the
preregistered floor of at least 10 new clean-start S3 traps.

### What construct labels are present?

Every trap has a construct label `C1` through `C10`. The frozen target quota is:

| Construct | Construct name | Target traps | Role |
|---|---|---:|---|
| C1 | Verbatim retention | 18 | v1 continuity and B5 calibration anchor |
| C2 | Retrieval precision under interference | 6 | anti-hoarding, executable precision under distractors |
| C3 | Staleness detection and supersession | 7 | stale suppression and multi-hop supersession |
| C4 | Update propagation / regression avoidance | 1 | derived update propagation |
| C5 | Scope discipline | 6 | overgeneralization resistance |
| C6 | Contradiction handling / provenance conflict | 5 | authority and precedence under conflict |
| C7 | Cross-session synthesis / paraphrase-retention application | 7 | non-verbatim composition and paraphrase-retention control |
| C8 | Procedural / source-of-truth memory | 4 | generated-source workflow and procedural memory |
| C9 | Spurious-lesson rejection / disconfirmation | 4 | bad-memory rejection |
| C10 | Abstention / confabulation resistance | 2 | irrelevant-memory rejection |
| **Total** |  | **60** |  |

The frozen arithmetic is:

```text
C1 18 + C2 6 + C3 7 + C4 1 + C5 6 + C6 5 + C7 7 + C8 4 + C9 4 + C10 2 = 60
```

Anti-hoarding constructs are C2, C3, C5, C6, C9, and C10, plus any explicitly
labeled C7 paraphrase-retention trap where byte copying is insufficient. The
frozen target from C2, C3, C5, C6, C9, and C10 alone is:

```text
C2 6 + C3 7 + C5 6 + C6 5 + C9 4 + C10 2 = 30 anti-hoarding traps
```

This exceeds the binding floor of at least 20 anti-hoarding traps.

### What legacy family labels are present?

Every trap also retains a family label for continuity and descriptive analysis.
The legacy families are:

- `convention-learning`
- `generated-files`
- `stale-architecture`
- `reviewer-preference`
- `flaky-test`

Family labels are not construct labels. For example, a `reviewer-preference`
trap may be C1, C5, C6, C7, C9, or C10 depending on the oracle and event
structure. A `flaky-test` trap may be C1 or C9 depending on whether it rewards
recall or disconfirmation.

### What hidden assets are part of the dataset?

The benchmark includes hidden oracles and reference-solution material. These
assets are part of the controlled dataset even when they are not included in a
public package. Hidden oracles check S1, S2, and S3 behavior, and S3 oracles
include commission checks for required correct behavior and omission checks for
trap-specific wrong behavior.

The preregistration requires the public task surface not to leak `oracle_cmd`,
absolute oracle paths, hidden tests, hidden labels, expected pass/fail,
reference patches, future injected events, secret manifests, or benchmark
result artifacts. Reviewer-only packages may include hidden oracle and
reference-solution files, scrubbed of local paths and secrets.

### Does the dataset contain missing or pending fields?

The public release contains the frozen v2 sequence file, canonical analyzer
artifacts, preregistration, admission report, and submission package manifest.
Second-wake-model transfer remains scoped out of the reported primary fold, as
described in the paper and preregistration.

The expected manifest fields for every trap include:

- `seq_id`
- `construct_label`
- `family_label`
- `repo`
- `template_id`
- `author_model`
- `skeleton_id`
- `anti_hoarding`
- `clean_start_s3`
- `csprng_secret_manifest_hash`
- `oracle_hash`
- `refsol_hash`
- validation report path and hash

Required metadata for non-C1 traps includes `decisive_oracle_literals`,
`required_fact_ids`, `event_fact_map`, `insufficiency_mechanism`,
`single_event_complete_answer`, commission checks, and omission checks.

### Does the dataset contain confidential, offensive, or personal data?

The dataset is synthetic software-benchmark material. The source documents do
not indicate that it contains natural-person records, production user data, or
personal data. The non-inferable tokens used inside traps are generated secrets
for benchmark control, not human secrets.

The hidden scoring assets and CSPRNG-generated tokens are confidential as
benchmark materials. Some generated tokens intentionally appear in injected
public memory events because the event is supposed to reveal the token to the
agent. Secret provenance sidecars record format classes and hashes, not the
generated values.

### What are the known composition risks?

Known risks include:

- v2 is designed after v1, mock reviews, the 8-trap synth pilot, and a
  scale-freeze review, so the causal history must be disclosed;
- LLM-authored trap skeletons may encode model-family biases or repeated
  template structure;
- the fixture repositories are small synthetic repositories rather than
  production codebases;
- hidden-oracle checks can overemphasize executable surface behavior if not
  audited against construct intent;
- recall-verbatim traps from v1 remain in the dataset for continuity, though C1
  is capped at 18 target traps and may not exceed 24 traps or 40% of the final
  valid set;
- the dataset is not intended to estimate broad model generality, especially if
  second-backbone transfer is absent or negative.

## Collection Process

### How was the data generated?

The v2 process is governed by a frozen preregistration. New traps are authored
as skeletons under a fixed worklist. Skeletons contain placeholders for
non-inferable tokens, ids, marker strings, format literals, and oracle literals.
Authors do not choose final random-looking literals by hand.

The batch pipeline is:

```text
skeleton
-> inject_secrets
-> batch_validate --dry
-> live gate on VPS
-> admit
```

Each author batch owns only skeleton files under its own directory. Shared
aggregate files, candidate JSONL files, shared harness scripts, existing
oracles/refsols, and fixture repo seeds are integrator-only after gate pass.

### What role did LLMs play?

LLMs author skeletons, not final secret-bearing traps. The design deliberately
separates LLM-authored structure from CSPRNG-generated non-inferable values.
The authoring LLM may specify placeholder classes and trap mechanics, but it
must not invent the decisive secret token or write final hidden oracle literals
by hand.

Every admitted trap records `author_model`, and the scaled report must analyze
author-model by wake-model interaction. That analysis is a contamination and
construct-validity check, not a replacement for the primary clustered
comparison.

### How are non-inferable secrets generated?

CSPRNG secret injection is binding. The harness generates all secret tokens with
a cryptographic random source and substitutes them into injected memory events,
hidden oracle expectations, reference-solution material, decisive-literal
metadata, and any public prompt field where the event itself is supposed to
reveal the token.

The secret manifest records hashes, generator-code identity, and format
classes, but not generated values beyond values intentionally present in public
injected events. The validator rejects a candidate whose decisive
non-inferable secret was authored directly by an LLM or human instead of being
injected by the harness.

The injection spec states that there is deliberately no deterministic seed or
replay option. Regenerating a skeleton produces fresh secrets.

### How was blind-to-outcome authoring enforced?

The frozen preregistration states that no new v2 trap may be authored after
looking at v2 performance results. New authors may know aggregate v1 and
synth-pilot facts disclosed in the preregistration, but must not consult
per-condition v1 outcome data while drafting individual v2 traps.

Candidate trap hashes, sequence ids, construct labels, family labels, oracle
ids, CSPRNG secret manifests, authoring skeleton ids, template ids, and
validation reports are frozen before the first memory-bearing v2 run.

After final v2 trap freeze:

- sequence prompts are not edited;
- injected events are not edited;
- hidden oracle expectations are not edited;
- construct labels are not edited except to correct a manifest typo before any
  run;
- family labels are not edited except to correct a manifest typo before any
  run;
- candidates are not removed except for a pre-declared infrastructure
  invalidity affecting all conditions and documented before outcome aggregation.

### What validity gates control admission?

The v2 report must disclose the full admission funnel:

```text
N generated skeletons
-> N with CSPRNG secrets injected
-> N passing schema and manifest checks
-> N passing refsol/oracle checks
-> N passing B0/no-memory S3 failure
-> N passing leakage/canary checks
-> N passing no-single-event or construct-specific gates
-> N passing template-cap checks
-> N human-reviewed for construct labels
-> 60 admitted traps
```

For every rejected candidate, the manifest records the first failing gate and
whether the candidate was discarded, repaired before outcome inspection, or
reserved as a replacement candidate before freeze.

A new trap is admissible only if the live validator verdict has:

- `valid == true`;
- `b0_s3_failed == true`;
- `refsol_passes == true`;
- `oracle_hidden == true`;
- `csprng_secret_injected == true`;
- no public task leak of oracle paths, hidden tests, labels, expected outcomes,
  reference patches, future events, secret manifests, or result artifacts;
- the construct-specific gate passes;
- `clean_start_s3` is correctly represented in the manifest and validator.

For C7 traps, the no-single-event gate is required. For C3, C5, C6, C9, and
C10, the oracle must check both correct behavior and absence of the
construct-specific trap behavior.

### How does the no-single-event gate work?

Every non-C1 trap declares why verbatim replay is insufficient. The automated
gate checks that no normalized decisive oracle literal is a substring of any
single injected event's content, except for C1 or explicitly declared visible
input literals that are not sufficient by themselves. It also checks that no
single injected event covers all required fact ids for a non-C1 trap, that the
S3 prompt does not reveal the decisive answer, and that the public repository
state before S3 does not contain the decisive answer except in declared
apply/derive cases.

The preregistration explicitly rejects "make B5 fail" as a design target. B5
passing a non-C1 trap is an outcome, not an authoring defect.

### What are the fixture repositories?

The fixture repositories are `configly`, `exprmini`, and `todolite`. They are
small synthetic software surfaces used to host the memory traps. The net-new
worklist assigns 10 skeletons to each fixture repository.

### What collection limits are intentionally frozen?

The preregistration freezes the trap set, construct and family labels,
condition definitions, model ids, provider endpoints, decoding parameters,
context limits, budgets, seeds, judge prompts, scorer code hash, CSPRNG
generator code hash, secret manifest hashes, result root isolation, log root
isolation, Mem0 namespace prefix, and Mem0 fixture/cache root before the first
primary-fold run.

The primary wake-model fold uses the frozen 60 traps, seeds 1 through 3, the
same task order across conditions, isolated memory namespace by run id,
condition, seed, and sequence, and hidden oracles/reference solutions absent
from the wake-agent filesystem.

## Preprocessing, Cleaning, and Labeling

### What preprocessing was applied?

The main preprocessing step is skeleton finalization through CSPRNG secret
injection. The injector replaces placeholders with generated values, writes
finalized sequence records, finalized hidden oracles, finalized
reference-solution diffs, and `secret_provenance.json`.

The injector rejects:

- unused declared placeholders;
- placeholders without manifest format classes;
- remaining `{{...}}` tokens in finalized trap files;
- generated-value collisions within the same run;
- one placeholder bound to multiple `seq_id` values;
- generated secrets appearing outside allowed trap-local locations.

Candidate traps then pass schema, manifest, hiddenness, reference-solution,
B0-failure, leakage/canary, no-single-event or construct-specific, template-cap,
and live validation gates before admission.

### How are labels assigned?

Each trap has a construct label and a family label. Construct labels follow the
C1-C10 inventory above and are the governing allocation unit. Family labels are
legacy descriptive labels and cannot override construct quotas.

The worklist maps existing material as follows:

- 18 v1 traps audited as recall-verbatim map to C1;
- 4 v1 synthesis/apply generated-help traps map to C8;
- 8 synth-pilot traps are mapped by their primary insufficiency mechanism:
  C7 for composition across events, C3 for supersession/stale suppression, C4
  for update propagation, and C6 for provenance conflict.

The net-new worklist fills the remaining quota:

| Construct | Target quota | Existing mapped count | Net-new skeletons |
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

### What human audit is required?

Human audit is required before final trap freeze:

- 100% of admitted traps receive human review of construct labels;
- at least 30% of admitted traps receive deep audit for construct fit, realism,
  leakage risk, template duplication, and whether the stated insufficiency
  mechanism is load-bearing;
- raw agreement and Cohen's kappa are reported where applicable;
- disagreements are resolved before freeze;
- traps with unresolved construct-label disagreement may remain as benchmark
  traps but are excluded from per-construct claims.

Human audit does not replace machine validation. Refsol pass, oracle
hiddenness, B0 failure, CSPRNG secret generation, no-single-event checks,
continuation or clean-start status, and contamination checks remain machine
gates.

Human-audit and admission outcomes are recorded in
`analysis/fold/admission_funnel.md` and the adjacent analyzer artifacts.

### Were records removed or repaired?

Pre-freeze candidates may be rejected only for pre-declared validity-gate
failures. Rejected candidates must retain the first failing gate and whether
they were discarded, repaired before outcome inspection, or reserved as a
replacement before freeze. After outcome inspection, traps are not removed
because they are hard, easy, inconvenient, unfavorable, or embarrassing.

Final admission-funnel counts are recorded in
`analysis/fold/admission_funnel.md` and `analysis/fold/admission_funnel.json`.

### What quality checks remain pending?

The required v2 output artifacts include the frozen sequence manifest and hash,
construct/family manifest, CSPRNG generator code hash, CSPRNG secret manifest
hashes, hidden oracle hash manifest, reference-solution hash manifest,
validation reports for every new trap, no-single-event or construct-specific
reports for every non-C1 trap, clean-start subset manifest, human-audit report,
admission-funnel report, contamination/isolation manifest, and excluded-cell
report. Final release hashes are recorded in `dist/CHECKSUMS.sha256` and
`dist/RELEASE-CHECKSUMS.sha256`.

## Uses

### What is the intended use?

The intended use is controlled evaluation of memory-hygiene mechanisms in
multi-session software-engineering agents. It is designed to compare conditions
that differ in memory substrate, retention strategy, typed maintenance,
retrieval, and raw evidence handling, using the preregistered S3 executable
pass/fail outcome and supporting hygiene metrics.

The primary outcome is validity-gated S3 executable pass/fail for each
`(condition, seed, trap)` cell. Warmup rates, hygiene metrics, cost, construct
slices, family slices, clean-start slices, and authoring diagnostics are
reported but do not replace the primary S3 analysis.

### What claims can it support?

If supported by the frozen analyses, the v2 dataset may support:

- a benchmark-scale claim that the scaled trap set is more balanced than v1;
- a primary system claim only if P1, `DF-hybrid` vs `B5`, passes the clustered
  Holm-corrected test;
- typed-maintenance, raw-evidence, typed-vs-reflection, construct-specific,
  transfer, or clean-start claims only within their preregistered claim
  boundaries.

Construct slices are descriptive unless a future amendment freezes a separate
construct-level inferential family before any run. The second wake-model fold is
a transfer check and cannot be pooled with or used to rescue the primary fold.

### What are inappropriate uses?

The dataset should not be used to:

- claim broad production generality from three synthetic fixture repositories;
- claim general SWE capability;
- rank coding agents without accounting for the memory-specific construct
  design;
- present failure to reject as equivalence;
- pool the second wake-model fold with the primary fold;
- remove or redesign traps after results;
- claim B5 is stock or tuned Mem0;
- make cost-effectiveness claims if the cost falsifier fails;
- claim container-equivalent hermeticity for a host-side GRID wake fold.

It is also inappropriate to train on the hidden oracles, reference solutions,
secret manifests, or outcome artifacts and then report results as benchmark
evaluation.

### What are the expected users?

Expected users are researchers or maintainers evaluating long-horizon
software-agent memory systems, especially systems that maintain raw evidence,
typed memory, contradiction repair, staleness suppression, scoped feedback, or
retrieval policies across sessions.

The dataset is not intended for end-user product evaluation or general coding
skill certification.

### Where are result fields recorded?

Live result values are recorded in the canonical analyzer bundle:
`analysis/fold/v2_fold.json`, `analysis/fold/v2_confirmatory_clustered.json`,
`analysis/fold/v2_hygiene_oracle.json`, `analysis/fold/v2_cost_frontier.json`,
`analysis/fold/admission_funnel.json`, and the generated paper tables under
`analysis/fold/`. The second wake-model transfer is not part of the reported
primary fold.

## Distribution and Licensing

### How will the dataset be distributed?

The repository contains a release artifact packaging flow. The public
artifact is not a raw repository dump. It is expected to include source code,
focused analysis and table scripts, live-rerun provenance scripts, public task
fixtures and sequence metadata needed by public smoke/tests, frozen folded
analysis summaries, paper-facing confirmatory reports, and public-compatible
tests.

The public package does not ship:

- `.git` history;
- local agent instructions;
- logs, bins, state files, handoff material, or credentials;
- hidden scoring assets under `<REVIEWER_ONLY_ORACLES>/` and
  `<REVIEWER_ONLY_REFSOL>/`;
- full raw hosted-model result directories.

Reviewer-only packages built with the private packaging option may additionally
include `<REVIEWER_ONLY_ORACLES>/` and `<REVIEWER_ONLY_REFSOL>/`, scrubbed of
local paths and secrets.

### Are hidden assets distributed?

Hidden oracles and reference solutions are not public benchmark inputs. They
are withheld from public packages to preserve benchmark validity. Hash manifests
and validation reports should be distributed where possible so users can verify
that hidden assets existed and were frozen without exposing oracle content.

Reviewer-only distribution may include hidden scoring assets under controlled
conditions. Any such private package must remain scrubbed of credentials, local
paths, and non-public secrets.

### What license applies?

The public repository and artifact package include an Apache License 2.0
`LICENSE` file. Code, scripts, public benchmark fixtures, public analysis
artifacts, packaging metadata, and public documentation are released under that
license.

The manuscript text and figures are copyright Sarthak Singh and are distributed
under the license selected for the arXiv submission. Reviewer-only hidden oracle
and reference-solution materials are not part of the public package and remain
controlled benchmark-security material.

The public package does not grant access to withheld hidden scoring assets.
Any private reviewer-only distribution should state its own access terms and
remain scrubbed of credentials, local paths, and non-public secrets.

### Are there export, privacy, or security restrictions?

The dataset is synthetic, but benchmark-security restrictions apply. Hidden
oracles, reference solutions, secret manifests, raw hosted-model outputs, local
logs, credentials, and condition-specific memory namespaces must not be exposed
to evaluated agents.

The public artifact can regenerate tables and sensitivity summaries from frozen
folded JSON, but cannot replay the paper fold from raw result records. Live
rerun scripts create new hosted-model experiments rather than exact replays.

## Maintenance

### Who maintains the dataset?

The DreamBench-SWE maintainers are responsible for the repository,
preregistration, trap manifests, validation artifacts, packaging scripts, and
release process. A named individual maintainer is not specified in the source
documents read for this datasheet.

### How is versioning handled?

DreamBench-SWE v2 is governed by the frozen preregistration dated 2026-07-04.
Changes after that freeze require a disclosed amendment log. The v2 fold does
not rewrite the already reported v1 fold, and the synth pilot is cited only as a
pre-registered directional slice, not pooled into v2.

Historical evidence logs remain immutable. If a correction is needed, the
protocol requires writing a new evidence note rather than rewriting old logs.

### Can the dataset change after release?

For the confirmatory v2 fold, changes are tightly restricted. After final trap
freeze, sequence prompts, injected events, hidden oracle expectations,
construct labels, and family labels are not edited except for limited manifest
typo correction before any run. Candidates are not removed after outcome
inspection except for documented infrastructure invalidity affecting all
conditions.

Future versions may be created, but they should receive separate version
identifiers, preregistration or amendment records, and clear separation from v2
results.

### What maintenance checks are required?

Before running the primary fold, the go criteria require:

- frozen and hashed preregistration;
- no v2 trap authored after v2 performance-result inspection;
- 60 valid traps frozen or enough pre-freeze replacement candidates to reach
  60;
- construct quota satisfied;
- at least 20 anti-hoarding traps;
- C1 no more than 24 traps and no more than 40% of final valid set;
- at least 10 new clean-start S3 traps;
- CSPRNG secret injection for every new trap;
- no-single-event or construct-specific gate for every new non-C1 trap;
- live validation for every new trap;
- B0 headroom gate satisfied on validation/admission;
- 100% construct-label human audit;
- at least 30% deep human audit;
- template cap of no more than four admitted traps per skeleton;
- required harness pre-fixes complete;
- run manifest, condition definitions, model configs, seeds, budgets, scorer
  hashes, and CSPRNG secret manifest hashes frozen.

Abort or downgrade criteria include fewer than 60 valid traps without a
pre-freeze replacement path, unmet construct quotas, fewer than 20
anti-hoarding traps, C1 exceeding the cap, fewer than 10 new clean-start S3
traps, CSPRNG secret-injection violation, B0 final S3 pass rate above 0.50,
insufficient P1 paired-trap support, more than 5% P1 cell-level validity
exclusions per side, hidden oracle leakage, condition memory-namespace
cross-read, trap edits/removals after outcome inspection, or missing required
harness pre-fixes.

### How should users report issues?

Users should report:

- suspected hidden-oracle leakage;
- public task leaks of hidden paths, labels, expected outcomes, reference
  patches, future events, secret manifests, or result artifacts;
- construct-label disagreements;
- fixture or oracle bugs;
- packaging leaks of credentials, local paths, raw result directories, or hidden
  assets;
- failures of the public smoke/test commands included in the artifact package.

Issue reports should identify the dataset version, sequence id, fixture
repository, construct label, artifact package hash if available, and whether the
issue was observed before or after outcome inspection. Reports should not paste
private oracle contents or generated secret values into public channels.

### What is the long-term archival expectation?

The v2 release should preserve the frozen preregistration, frozen sequence
manifest, construct/family manifest, CSPRNG generator hash, secret manifest
hashes, hidden oracle hash manifest, reference-solution hash manifest,
validation reports, human-audit report, admission-funnel report, run manifest,
analysis outputs, contamination/isolation manifest, and excluded-cell report.

Public archives should retain enough material to reproduce public smoke tests
and regenerated tables from frozen folded summaries. Private archives should
retain hidden scoring assets under access control so claims can be audited
without leaking the benchmark to future evaluated agents.
