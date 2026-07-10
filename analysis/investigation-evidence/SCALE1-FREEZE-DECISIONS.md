# SCALE-1 Phase-0 freeze decisions (orchestrator, binding)

Reconciling PREREGISTRATION-V2-DRAFT (codex) x FABLE-BULLETPROOF-BAR x FABLE-CONSTRUCTS x
TRAP-PIPELINE-PLAN x BACKBONE-FEASIBILITY. Where they conflict, these decisions govern.

D1. TRAP COUNT: 60 valid traps total (fable floor; codex's 50 rejected as under the
    anti-hoarding quota), = 22 v1 + 8 synth + ~30 new. 3 seeds -> 180 S3 cells/condition.
D2. COMPOSITION (by CONSTRUCT, not family — per FABLE-CONSTRUCTS): >=20 of 60 traps
    (1/3) from anti-hoarding constructs: suppression, retrieval-precision-under-
    interference, multi-hop supersession, provenance-conflict, paraphrase-retention,
    abstention/disconfirmation. Recall-verbatim capped at ~40% (24 traps incl. the
    v1 18). Every trap carries a CONSTRUCT label (C1..C10) + family label separately —
    fable caught that v1 family names do not match constructs (flaky-test = recall-verbatim).
D3. SECRETS: CSPRNG-injected by the harness into LLM-drafted skeletons; the authoring
    LLM never invents a token. By-construction inferability defense. Non-negotiable.
D4. PRIMARY ANALYSIS: trap-clustered exact sign/permutation test, family = the
    pre-specified comparisons in the draft; pooled McNemar secondary; per-construct
    strata reported descriptively (n small per stratum); publish-if-null.
D5. SECOND BACKBONE: GRID GLM-5.2 (glm-latest) — pinned open-weights, $0, cleanest
    adapter per BACKBONE-FEASIBILITY (OpenAI-compatible; wake!=judge verified clean).
    Gated by the 5-trap warmup pilot (<60% warmup -> fall back to Kimi K2.7, then a
    frontier peer; report any floor honestly). Judge stays codex-gpt-5.5 everywhere.
D6. AUTHOR x SOLVER: report the author-model x wake-model interaction across both
    backbones; human audit = 100% of construct labels + >=30% deep audit w/ agreement;
    admission-funnel disclosure; template cap: <=4 traps per skeleton.
D7. CLEAN-START SUBSET: >=10 of the new traps use clean-start S3 (no inherited S1/S2
    code state) to separate memory failure from inherited implementation failure.
D8. HARNESS PRE-FIXES (before any v2 run): (a) scan_completed_units must verify full
    per-sequence S1/S2/S3 coverage before skipping (latent partial-completion hole);
    (b) analyzer warmup definition = non-S3 (grouped-run undercount, in flight).
D9. SYNTH PILOT CITATION (corrected, true numbers, n=24): B0/B1/B5-MEM0 0.000, B3 0.125,
    DF-typed 0.167, B5 0.250, DF-hybrid 0.250, DF-raw-only 0.292; warmup 0.75-0.81.
    Cited as the pre-registered directional slice; never pooled.
D10. KILL CRITERIA (verbatim from FABLE-BULLETPROOF-BAR #5): >=3 new construct strata
    floored (<0.15 max-condition) -> arXiv measurement-study fallback; author x solver
    contamination -> re-author w/ separation; ladder reordering across backbones ->
    model-dependence analysis paper; instability at scale -> not top-venue. Never
    shrink back to the verbatim stratum for a lesser venue.
