I'll read the four grounding files before rendering judgment.
All four grounding documents read. Rendering judgment.

---

# DreamBench-SWE v2 Construct Architecture — Measurement-Theory Judgment

Grounding: `appendix_verbatim.tex` (per-trap audit), `06_experiments.tex` (families, condition contract, isolation), `CONFIRMATORY-FOLD.md` (per-condition/per-seed outcomes), `MOCKREVIEW-neurips.md` (W1–W3, Q3–Q5, Accept-items 1–2, 8).

Empirical anchors I rely on throughout:

- B5 = 48/66 pooled, **0/12 on the four synthesis/apply traps**; DF 4/12 on those traps (floordiv 3/3, precgroup 1/3).
- **Three dead traps** at 0/6 across both flagship conditions: `expr-convention-opnaming`, `expr-generated-category`, `expr-stale-registry-stability`. ~7 ceiling traps at 3/3 + 3/3 (no discrimination in either direction).
- All three "flaky-test" traps in the audit are classified recall-verbatim (comparator/rule stated in event text) — the family name does not match the measured construct.
- All 7 reviewer-preference traps are recall-verbatim — they test recall of the preference *format*, not scoped *application* of feedback.
- All stale-architecture traps that discriminate are B5-solvable (3/3) — meaning the "stale" traps never actually require suppression of the stale item.
- HarmfulMemoryRate = 0.000 for **every** condition — no trap in v1 makes stored memory *hurt*. This is a design gap, not a hygiene win.
- CRA = HFUA = TransferScore identically per condition (judge artifact) — contradiction handling is currently measured by nothing trustworthy.

---

## 1. The construct inventory

Ten distinct memory-hygiene constructs. For each: definition, what a *pure* trap looks like in the SWE setting, and current coverage. Confidence tags are on the claim that the construct is (a) distinct and (b) currently covered/uncovered as stated.

### C1. Verbatim retention (recall fidelity)
**Definition:** preserve a non-inferable exact token/format/rule from one prior event and reproduce it when relevant.
**Pure trap:** S1 injects an arbitrary token (e.g., `FRZ-7Q2`); S3's hidden oracle requires it; nothing else in the sequence interferes. Storage of the single event is necessary *and sufficient*.
**Current coverage:** 18/22 traps. Massively overrepresented. **Confidence: high** — this is exactly what the appendix audit certifies.

### C2. Retrieval precision under interference
**Definition:** find the one relevant stored fact among many stored facts, within a fixed read-token budget. Retention is trivial; *selection* is the skill.
**Pure trap:** identical to C1 except the sequence injects ≥8 events sharing surface vocabulary with the S3 prompt (same file names, same subsystem nouns), of which exactly one contains the oracle token, and the read budget admits ~2 events' worth of memory. A system that stores everything deterministically and injects by budget order (B5's policy) hits the right event at roughly the base rate; a system with retrieval gating hits it reliably.
**Current coverage: zero.** v1 sequences have so few events that B5's one-memory-per-trajectory always fits in budget — UsefulMemoryPrecision is measured only as a judge-scored diagnostic, never as a task outcome. **Confidence: high.**

### C3. Staleness detection & supersession
**Definition:** when a fact is superseded by a later event, use the new fact and *do not* use the old one — even when the old one is more salient.
**Pure trap:** S1 states rule/token X repeatedly (3+ mentions, high lexical overlap with the S3 prompt). S2 supersedes it with Y exactly once, indirectly (commit message, a different file, a one-line reviewer note). Oracle requires Y AND fails any diff containing X. Critically: a "prefer newest event" heuristic must also fail on some traps in this battery (e.g., the newest event is an irrelevant refactor; the supersession is second-newest), or the construct collapses into recency.
**Current coverage:** nominally 4 stale-architecture traps, but the discriminating ones are B5 3/3 — the stale token is never an *attractive live distractor*, so no suppression is ever exercised. The one trap where suppression might have mattered (`expr-stale-registry-stability`) is dead at 0/6. **Effective coverage: ~zero.** **Confidence: high.**

### C4. Update propagation / regression avoidance
**Definition:** after a fact changes, *derived* usages must change consistently — the agent must propagate an update to a second location it previously touched, not just recall the new value.
**Pure trap:** S1 establishes token X used in two places; S2 renames X→Y in place 1 only and states the rename rule; S3's task touches place 2 and the oracle requires Y there, failing any output leaving X. Distinct from C3: the superseding event does not contain the answer's *location*, only the rule.
**Current coverage: zero** (RegressionAfterUpdate exists only as a stdout-scanning heuristic). **Confidence: medium-high** on distinctness from C3 — it is C3 plus application, and I keep it as a small probe, not a headline battery, for exactly that reason.

### C5. Scope discipline (overgeneralization resistance)
**Definition:** apply human feedback within its stated scope and *refuse* to apply it outside. The failure mode is memory *over*-use.
**Pure trap:** S2 reviewer feedback: "in `export.py`, always quote CSV fields" (with a non-inferable dialect token). S3 task is in `report.py`, where the hidden oracle *fails* quoted output and passes the repo-default style; a sibling check in the same trap (or a paired trap) verifies the preference IS applied when the task is in `export.py`. Verbatim storage is necessary to pass the in-scope side and actively harmful on the out-of-scope side unless the memory carries scope metadata.
**Current coverage:** zero at the trap level. All 7 reviewer-preference traps reward recalling the preference; none punishes overgeneralizing it. ScopeAccuracy is judge-scored only. **Confidence: high** — and this is the single most valuable construct to add, because it is the one where B5's architecture (store raw event, always inject) is *anti-correlated* with success.

### C6. Contradiction handling (authority/precedence, non-temporal)
**Definition:** two sources conflict without temporal ordering resolving it (reviewer vs. doc, two reviewers, doc vs. code comment); correct behavior follows a precedence rule established elsewhere, or surfaces the conflict rather than silently picking one.
**Pure trap:** S1 doc says format A (token `A-x7`); S2 a reviewer says format B (token `B-q9`) *in the same session-time frame*, plus a separately-injected precedence rule ("reviewer feedback overrides docs for module M / docs override for module N"). S3 task in module N; oracle requires A. Storing both verbatim gives you a coin flip; the precedence rule is the load-bearing memory and it's in a third event.
**Current coverage: zero** trustworthy — CRA is a collinear judge artifact. **Confidence: high.**

### C7. Cross-session synthesis (multi-event composition)
**Definition:** the required answer is a *function of* ≥2 events from different sessions; no contiguous span of any single stored event contains it.
**Pure trap:** S1 establishes a naming/derivation rule ("precedence groups are named `pg<level>-<registry-suffix>`"); S2 establishes the parameter (`registry suffix = 91vq` for this repo); S3 oracle requires the composed token `pg3-91vq`, which appears verbatim *nowhere*. Both parts must be retrieved and composed.
**Current coverage:** 4 synthesis/apply traps — the right idea, but 2 of 4 are dead (0/6 both systems) and the slice is n=4, exactly the underpowered pilot the mock review calls out (W2, Q5). The v1 versions also entangle synthesis with a heavy *workflow* requirement (run the generator), which confounds composition with procedure execution. **Confidence: high** on the coverage assessment; **medium-high** that decoupling composition from workflow fixes the floor.

### C8. Procedural / source-of-truth memory
**Definition:** remember *how things are done here* — specifically that certain files are generated artifacts and the source of truth is elsewhere; edit the generator, regenerate, don't hand-edit the artifact.
**Pure trap:** S1/S2 establish the generation workflow with a marker in the generated header; S3 asks for a change whose naive implementation is a hand-edit of the generated file; oracle checks both the generated output AND that the generator source changed (fails artifact-only diffs). Declarative recall of the marker is insufficient; the workflow must be executed.
**Current coverage:** the generated-files family (3–4 traps) genuinely targets this, and it's where DF showed its only clean B5-separation (floordiv 3/3 vs 0/3). Keep and expand; fix the two dead ones. **Confidence: high.**

### C9. Spurious-lesson rejection (selective forgetting of noise)
**Definition:** refuse to consolidate an unsupported causal lesson from nondeterministic evidence; behave as if the flaky failure taught nothing.
**Pure trap:** S1 has a test fail once for an environment-dependent reason (timestamp, ordering); an injected event *asserts a wrong causal story* ("`parse_dates` is broken, route around it"). S2 shows the test passing with no code change. S3 task naturally uses `parse_dates`; oracle fails any workaround/avoidance pattern and passes straightforward use. Systems that stored and act on the spurious lesson fail; systems with no memory at all *pass* — this is the construct where B0 > (bad memory), which is measurement gold.
**Current coverage: zero.** The three "flaky-test" traps are, per the audit, verbatim recall of a comparator/rule. The family label is currently a misnomer. **Confidence: high.**

### C10. Abstention / confabulation resistance
**Definition:** recognize that no prior evidence bears on the current task and do not fabricate benchmark-style tokens or import irrelevant memories.
**Pure trap:** S3 prompt is templated to *look like* a trap session (same phrasing rhythm as token traps) but no prior event contains any relevant token; oracle passes a fresh, sensible solution and fails outputs matching the benchmark's token grammar (`[A-Z]{3}-[0-9A-Z]{4}`-style strings) or repo-foreign conventions. Punishes "always retrieve and spray" policies.
**Current coverage: zero** — this is *why* HarmfulMemoryRate floors at 0.000 for every condition: no trap can elicit harmful memory use. **Confidence: high.**

**Deliberately excluded:** memory *provenance* as a standalone trap construct (auditability is a system property, better tested by artifact inspection than by executable oracles — low confidence it can be operationalized without judge dependence), and long-horizon capacity (>3 sessions) — a real construct but a v3 axis change, not a v2 rebalance; changing session count and taxonomy simultaneously destroys comparability with v1.

---

## 2. The v2 50-trap allocation

Principle: a construct gets **n≥6 traps if and only if the paper will make a per-construct claim about it** — specifically, the constructs that map onto DreamForge's named operators, because those are the claims a D&B reviewer will demand power for. With 3 seeds, n=6 traps = 18 cells; per-construct Wilson CIs are still wide (±~0.20), but trap-level paired sign tests across 6 traps become minimally meaningful, and the *cross-construct profile* (10 points per condition) becomes the real deliverable. Constructs at n=2 are declared exploratory probes in the pre-registration and never appear in claim sentences.

| # | Construct | n | Rationale | Maps to system operator | Carry-over from v1 |
|---|---|---|---|---|---|
| C1 | Verbatim retention | 6 | Anchor battery. Retained for v1 continuity and as the B5-calibration reference: B5 should score ~1.0 here, defining the ceiling every other construct is measured against. | (none — baseline skill) | 6 best-discriminating v1 RV traps (drop ceiling 3/3-3/3 traps; keep e.g. `config-reviewer-coerce-tag`, `expr-reviewer-divzero`, `todo-dedupe-keeplowest`) |
| C2 | Retrieval precision under interference | 6 | Per-construct claim needed: this is where retrieval gating (A6 ablation) should separate. Also directly converts UsefulMemoryPrecision from judge diagnostic to executable outcome. | Retrieval gating | 0 (new; can wrap v1 token traps in distractor-event shells) |
| C3 | Staleness/supersession | 7 | **Headline battery.** Stale suppression + contradiction repair are the system's flagship operators (A2/A5 ablations); v1 never actually exercised them. Largest allocation. | Stale suppression, contradiction repair | 1 (`config-stale-merge` redesigned with the stale token made a live distractor) |
| C4 | Update propagation | 2 | Probe. Plausibly C3+application; keep small until v2 data shows it decorrelates from C3. | Contradiction repair | 0 |
| C5 | Scope discipline | 6 | Per-construct claim needed: the reviewer-preference family's *stated purpose* ("scoped use of human feedback, avoidance of overgeneralization") is finally measured. Paired in-scope/out-of-scope oracle structure. | Retrieval gating + typed scope metadata | 2 reviewer traps refitted with out-of-scope oracle arms |
| C6 | Contradiction handling | 5 | Just under claim threshold — deliberate. CRA's judge-artifact history means the first executable contradiction battery should be reported descriptively; promote to n≥6 in v2.1 if it discriminates. | Contradiction repair | 0 |
| C7 | Cross-session synthesis | 7 | **Headline battery.** The mock review's Accept-item 2 verbatim: "a powered synthesis/apply subset where exact verbatim storage is insufficient." This is where hybrid's only real B5 separation lived (9/12-vs-0/12 flavor); it must be powered. | Typed consolidation | 2 (`expr-floordiv-category` kept; `expr-generated-precgroup` recalibrated) |
| C8 | Procedural/source-of-truth | 5 | Proven discriminator in v1; expand modestly, fix the two dead traps via the difficulty gate (§3, R6). | Typed consolidation (procedural type) | 3–4 generated-file traps, 2 repaired |
| C9 | Spurious-lesson rejection | 4 | The only construct where *no memory beats bad memory* — indispensable for discriminant validity of the whole suite, but hard to author well; 4 is realistic for one wave. | Counterfactual replay (A4) | 0 (v1 "flaky" traps reassigned to C1 where they belong) |
| C10 | Abstention/confabulation | 2 | Probe. Cheap to author, high diagnostic value (un-floors HarmfulMemoryRate), but pass rates may ceiling for all conditions — keep small until proven to discriminate. | Retrieval hard gate | 0 |
| | **Total** | **50** | | | ~12 carried, ~38 new |

Resulting shape: verbatim-necessary-and-sufficient falls from 18/22 (82%) to 6/50 (12%); verbatim-necessary-but-insufficient becomes 42/50; and 12/50 traps (C5 out-of-scope arms, C9, C10) actively *punish* indiscriminate memory use — a category with zero members in v1. **Confidence in this allocation as the right shape: high. Confidence in exact integers: medium** — the load-bearing decisions are (i) C3 and C7 at n=7, (ii) C6 deliberately below claim threshold, (iii) C1 cut to 6 not 0 (cutting to 0 would destroy the B5-ceiling calibration and v1 comparability).

One non-negotiable pairing rule: each construct battery spans **all three repo families** (config/expr/todo) — minimum 2 families per construct — so construct is not confounded with repository, which is attack A3's fuel.

---

## 3. Discriminant validity: design rules that kill "B5 ties everything" structurally

The v1 failure mode, stated precisely: a trap collapses into "did you store the token" whenever (a) the oracle-required content is a contiguous span of a single prior event, (b) all relevant events fit in the read budget simultaneously, and (c) in-context wake reasoning over injected raw events suffices. B5 satisfies (a)–(c) by construction. Every non-C1 trap must break at least one of the three, **verifiably, not by author intent.**

**R1 — Necessity floor (keep).** Every trap retains a non-inferable arbitrary token so B0 floors at ~0. This is v1's genuine achievement (B0/B1/B2/B4/B6/B7 all at 3/66) and is what makes these *memory* traps at all. Verbatim storage stays necessary everywhere.

**R2 — Insufficiency mechanism (declare per trap).** Every non-C1 trap declares, in its spec, *which* of (a)/(b)/(c) it breaks and *how*:
- C2 breaks (b): ≥8 injected events, budget admits ~2, exactly one is load-bearing, distractors share surface vocabulary with the S3 prompt.
- C3 breaks (c)-with-teeth: stale token has *higher* lexical salience than fresh token (more mentions, closer prompt overlap); supersession signal is indirect. At least 2 of the 7 traps must also defeat a prefer-newest-event heuristic.
- C5/C9/C10 break (c) by inverting it: the stored content is a *hazard*; the oracle fails outputs that use it (out of scope / spurious lesson / confabulated token).
- C7 breaks (a): the required token is composed — no contiguous span of any event contains it. Composition rule and parameter live in different sessions.
- C8 breaks (a)+(c): the oracle checks the *diff shape* (generator source changed), not just the output content.

**R3 — Pre-registered baseline-signature admission gate.** This is the rule that makes discriminant validity operational rather than rhetorical. Define a fixed diagnostic battery: **B0, B5, B5+recency** (B5 that prefers the newest event under budget pressure — a new, cheap, deterministic baseline you must build), and **B3**. Each construct pre-registers its expected signature, e.g.:
- C1: B5 ≈ 1.0.
- C2: B5 ≈ chance-of-right-event-under-budget; B5+recency same; gated retrieval ≫.
- C3: B5+recency > B5 > B0, with designed headroom above B5+recency on the anti-recency subset.
- C5 (out-of-scope arm), C9, C10: **B0 ≥ B5** — no-memory beats naive memory.
- C7: B5 ≈ 0; any condition that composes > 0.
A pilot trap is *admitted to its construct only if its observed baseline signature matches*. Traps that B5 solves get reclassified to C1 or revised — automatically, by pre-registered rule, before the confirmatory fold. This converts the appendix-verbatim audit from a post-hoc confession into a design-time gate. **This single rule is the answer to "B5 ties everything": in v2, B5's per-construct profile is a designed, published prediction (≈1.0, low, mid, ≤B0, ≈0, …), so B5 tying the *aggregate* is impossible unless the gate was violated — and the gate is auditable.**

**R4 — Dual-arm oracles (commission + omission).** Non-C1 oracles check presence of correct behavior AND absence of trap behavior (stale token in diff, out-of-scope style application, workaround pattern, confabulated token). Additionally, record a **three-way outcome per trap: pass / trap-tripped / other-failure**, where "trap-tripped" requires positive evidence of the construct-specific failure in the diff. Construct-level metrics are computed on trap-tripped rates, not raw failure — this separates "failed the construct" from "failed to code" (see attack A2 below).

**R5 — Empirical convergent/discriminant check on the outcome matrix.** After the fold, compute the condition × trap outcome matrix and report: within-construct inter-trap agreement vs. cross-construct agreement (a mini multitrait analysis over the ~20-condition battery). Pre-registered rule: any non-C1 trap whose outcome vector across conditions correlates ≥0.9 with the C1 battery mean is flagged as de-facto verbatim and excluded from per-construct claims. Construct labels become falsifiable.

**R6 — Difficulty calibration band.** Admission requires: B0 ≈ 0 (floor intact) AND an oracle-informed reference run passes 3/3 seeds (solvability certificate) AND at least one memory condition passes ≥1/3 seeds in pilot. v1 shipped three dead traps (0/6 on both flagship systems) and ~7 no-discrimination ceiling traps — roughly 45% of the benchmark carried no signal in the B5-vs-DF comparison. R6 is cheap and would have caught all of them. **Confidence: high.**

---

## 4. The three most likely NeurIPS D&B construct-validity attacks on v2, and the pre-emption for each

**A1 — "Your ten constructs are one construct wearing ten costumes: everything still reduces to storing and surfacing prior event text; a two-line B5 patch (store everything + prefer newest + light dedup) tops every battery."**
This is the v2 form of the B5 critique, and it is the most likely attack because v1 explicitly conceded its v1 form.
*Pre-emption:* the R3 admission gate plus the **published per-construct B5 and B5+recency profiles**. The paper shows, as a designed result, that B5 scores ≈1.0 on C1, ≤B0 on C5-out-of-scope/C9/C10, and ≈0 on C7 — i.e., the naive-storage policy has a *jagged* profile across constructs by construction, verified in pilot before the confirmatory fold. Crucially, you build B5+recency *yourself* and report it, so the reviewer's "two-line patch" is already a row in the table. The claim shifts from "our system beats B5" to "no single-policy memory strategy is flat across the construct profile" — which the benchmark then measures. **Confidence that this attack comes: very high. Confidence the pre-emption holds: high, conditional on the gate actually being enforced in pilot.**

**A2 — "Your non-verbatim constructs are confounded with difficulty: harder traps fail for generic coding reasons, and you're relabeling floor effects as construct separation."**
The v1 synthesis slice is the reviewer's exhibit A: 2 of 4 traps at 0/6 for *everyone* is not a construct measurement, it's a wall. Note also `config-reviewer-coerce-tag` (B5 0/3 despite the verbatim contract) — implementation difficulty already leaks into "recall" scores in v1.
*Pre-emption:* two design features working together. (i) R6's calibration band with the oracle-informed solvability certificate: every trap is demonstrated solvable by a reference agent that has the answer, so a memory-condition failure cannot be attributed to task impossibility. (ii) R4's three-way outcome: per-construct headline numbers are *trap-tripped rates among decision-reaching runs*, with "other-failure" reported separately — so a hard trap inflates other-failure, not the construct metric. Report per-construct other-failure rates in the main table; if they're flat across constructs, the difficulty confound is empirically closed. **Confidence attack comes: high. Confidence pre-emption holds: high** — this is the strongest of the three defenses because it's mechanical.

**A3 — "Your construct taxonomy is author-assigned and single-team: the ten labels are one designer's stylistic clusters over three toy repos, not constructs. Nothing shows two traps labeled 'supersession' measure the same thing."**
This is mock-review Q4 sharpened into a measurement claim, and D&B reviewers increasingly ask for it explicitly (label reliability).
*Pre-emption:* three features. (i) The R5 outcome-matrix analysis is the *convergent* evidence: within-construct trap outcomes cluster across the ~20-condition battery; cross-construct they don't — reported as a figure, which no memory benchmark I'm aware of currently provides (**confidence: medium** on the novelty claim, **high** on its persuasive force). (ii) Independent construct labeling: ≥2 annotators (at least one outside the authoring team) assign each trap to a construct from the written construct definitions, blind to the intended label; report agreement (Cohen's κ), pre-registering that traps with label disagreement are excluded from per-construct claims. (iii) The 2-families-per-construct authoring rule from §2, so construct ≠ repository and construct ≠ sequence template. If v2 can additionally source even 5–8 traps from a second author following the published trap-spec schema, do it — it converts "trap protocol" from claim to demonstrated artifact and directly feeds the release story. **Confidence attack comes: high. Confidence pre-emption holds: medium-high** — (ii) and (iii) are logistically cheap; (i) carries the real weight and depends on v2 traps genuinely decorrelating, which the R3 gate is designed to ensure.

---

## Bottom line

v1 measured one construct well (verbatim retention), one construct partially (procedural/source-of-truth), and zero of the constructs the system's operators are named after (supersession, contradiction repair, scope, spurious-lesson rejection). The v2 design above inverts that: 12% pure verbatim, two powered headline batteries (C3 supersession n=7, C7 synthesis n=7) aimed exactly where the mock review said the contribution must live, a designed anti-B5 wedge (12 traps where storage hurts), and — the structural centerpiece — the **baseline-signature admission gate (R3) plus three-way outcomes (R4) plus the outcome-matrix convergence check (R5)**, which together move discriminant validity from a post-hoc appendix confession to a pre-registered, auditable design property. If only one thing from this document survives contact with implementation, it should be R3: constructs defined by predicted baseline separation patterns, enforced at trap admission. That is the design-level kill of "B5 ties everything."
