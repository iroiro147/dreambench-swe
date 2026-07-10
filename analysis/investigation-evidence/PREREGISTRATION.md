# Pre-registration — DreamForge raw-evidence fix + confirmatory ablation-ladder fold

> Frozen 2026-07-03, BEFORE running the confirmatory fold. Authored by Opus 4.8 (Phase-7 authority) after a 6-pass reasoning panel (codex/GPT-5.5 ×2, cc2/Claude ×2, fable-5 ×1, + diagnostic). Governs how the DF-vs-B5 result may be claimed. Any deviation from this document after seeing post-fix numbers is p-hacking and is forbidden.

## 0. The significance ceiling (disclosed up front)
On the current n=66 design (22 sequences × 3 seeds), the DF-vs-B5 discordant structure is b=5 (DF+B5−), c=9 (DF−B5+). A PERFECT intervention (flip all 9 losses, keep all 5 wins, regress nowhere) yields b=5, c=0 → exact two-sided McNemar **p=0.0625 > 0.05**. Therefore **a statistically significant DF>B5 on S3 trap success is UNREACHABLE at n=66**, independent of any DF change. The confirmatory fold can at most establish a NOMINAL lead inside a formal tie. A significant comparison would require a pre-registered sample-size expansion (≥5 seeds → n≥110 and/or v1.1 synthesis traps), NOT more DF engineering. This is stated so the nominal result is never dressed up as more than it is.

## 1. The intervention (category-level, no per-trap/token logic)
- **R1** — pipeline-level verbatim raw-evidence retention (`consolidation.py:sleep_pipeline`): one EPISODIC memory per episode carrying `injected_memory_event.content` verbatim, utility 0.6 (InstancePolicy parity), through the normal gate. Gated by `enable_raw_evidence`. Justification: charter (`CLAUDE.md:27`) mandates raw trajectories as first-class evidence; the implementation had reduced the raw pointer to contentless.
- **R1b** — EPISODIC candidates are exempt from contradiction repair (`contradiction_repair.py:apply`). Enforces the charter's "never deletes evidence." Always-on.
- **R2** — DreamForge retrieval excludes `MemoryType.CONTRADICTION` (`baselines.py:DreamForgePolicy.read` + `_retrieve_without_gate` + `_retrieve_with_stale_allowed`) via existing `allowed_types` gate. Gated by `exclude_contradiction_from_read`. Justification: CR records are lifecycle metadata (zero task content); serving their bodies as agent context is a category error responsible for UMP=0.19 and the DF-strict inversion.
- **R3** — deterministic ordering of repair inputs (variance hygiene; moves no mean). Always-on.

REJECTED (out of bounds, pre-registered): guaranteed/reserved verbatim retrieval slot (E5 — that is B5-with-extra-steps); theta_admit grid search (burns statistical power); any trap redesign in this cycle.

## 2. Conditions (all DF-family share seeds/model/budget/scorer/task-order with the clean run)
Ablation ladder: **B5** | **DF** (typed-only = current, both new flags False) | **DF-raw-only** (consolidation off + R1 + R2) | **DF-hybrid** (full pipeline + R1+R1b+R2+R3). Plus **DF-strict-hybrid** (DF-strict params + R1+R2). Held ablations re-run clean on the leak-fixed harness: A0, A2, A4, A5, A6, A11. Real-Mem0: B5-MEM0 (low concurrency). B0–B7, B3, DF-typed-only, DF-strict clean numbers stand from the current fold (byte-unchanged code) but DF-typed-only + B5 are re-run for same-harness parity.

## 3. Analysis plan (fixed now)
Pooled S3 rate + per-seed rates (direct-read of stored final_passed; barrier2 seed-attribution bug is fixed or bypassed first — task #21). Exact McNemar: DF-hybrid vs B5, DF-hybrid vs DF, DF-raw-only vs B5, with Holm/BH correction over the family (per cf58c78). Hygiene panel: UsefulMemoryPrecision, RepeatedErrorRate, RegressionAfterUpdate, ScopeAccuracy, ContradictionRepairAccuracy, HumanFeedbackUseAccuracy, TransferScore. Retrieval volume + AdmTok/task. The **central table is the ablation ladder** (B5 / DF-typed-only / DF-raw-only / DF-hybrid) which attributes S3 performance to the verbatim channel vs the typed overlay.

## 4. Falsifiers (pre-committed; if any fails, the causal story is wrong and does not ship as-is)
- **DF-strict-hybrid must recover** from 0.339 to ≥0.55 (R2 removes the tombstone-selection inversion). If not → crowding-out misdiagnosed; re-examine before publishing.
- **UsefulMemoryPrecision** rises from 0.19 to ≥0.45.
- **Seed spread** narrows to ≤0.10.
- **B5 stays byte-identical** (0.727/0.727/0.727) — it shares no code path with R1/R2; any drift signals contamination.
- **RepeatedErrorRate ≤0.136 and RegressionAfterUpdate ≤0.045** must not regress.

## 5. Honesty boundary + mandatory disclosures
- On 18/22 traps the flipping component is a **B5-equivalent verbatim record**, not consolidation. The only permitted claim if DF-hybrid > B5: *"a hybrid retaining raw evidence alongside typed consolidation matches/edges verbatim retention alone"* — NEVER *"typed consolidation beats verbatim replay."*
- If DF-hybrid ≈ DF-raw-only on trap success → the typed pipeline adds nothing to that metric; the paper must say so and point to hygiene as consolidation's measured contribution.
- Disclose: B5 = injected-event verbatim retention (not trajectory replay = B1), strong by benchmark design (18/22 RV traps); R1/R2 are diagnosis-driven bug fixes designed before the confirmatory re-run, trap set untouched; the n=66 significance ceiling (§0); the seed variance; the full ablation ladder with attribution.
- Validity gates before any number is believed: `validate_trap.py --live` green; `gate.score(raw-evidence record) > theta` for all sequences; leakage scan (MEM0_FORBIDDEN markers) on new EPISODIC contents = 0; dedupe result dirs; per-seed B5 determinism check.

## 6. Predicted result (for the record, falsifiable)
DF-hybrid pooled S3 ≈ 0.758 (range 0.71–0.80). P(nominal lead over B5 0.727) ≈ 70%. P(significant at n=66) < 5%. P(the lead is attributable to the typed pipeline rather than the B5-equivalent backup) ≈ 35%. The paper's spine does NOT depend on any of these being high: DF ≫ B3 (24-3, p≪0.001), the hygiene wins, the procedural-transfer win (floordiv 3/3 vs 0/3), and the benchmark taxonomy stand regardless.
