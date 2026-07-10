# MEM0 Row Fold Report

Date: 2026-07-04

Scope: Fold the final real-Mem0 B5-MEM0 result into the DreamForge paper and correct A11 to the authoritative fold.

Sources read first:

- `analysis/investigation-evidence/CONFIRMATORY-FOLD.md`
- `analysis/investigation-evidence/REFOLD-PREP-REPORT.md`
- `analysis/investigation-evidence/MEM0-TOKEN-CORRUPTION.md`

Note on diff scope: the local worktree already contained unrelated dirty files under `handoff/`, `logs/`, `experiments/`, `sources/`, and `paper/main.pdf`. The complete diff below is the complete task diff for the manuscript files edited for this fold.

## Git Diff

Command:

```bash
git diff -- paper/sections/01_abstract.tex paper/sections/06_experiments.tex paper/sections/07_results.tex paper/sections/08_analysis.tex paper/sections/09_limitations.tex
```

Output:

```diff
diff --git a/paper/sections/01_abstract.tex b/paper/sections/01_abstract.tex
index 06d4b56..0f1e971 100644
--- a/paper/sections/01_abstract.tex
+++ b/paper/sections/01_abstract.tex
@@ -36,6 +36,11 @@ typed pipeline may add beyond the verbatim backup but is not established as a si
 increment.  The robust structured-memory result is \sys{} typed-only vs.\ the
 vector-retrieval baseline B3: exact McNemar
 $b{=}24$, $c{=}3$, $p{=}4.9{\times}10^{-5}$, Holm $p{=}0.0002$.
+The pinned live-Mem0 baseline B5-MEM0 reaches $6/66=0.091$ because Mem0's
+LLM extraction does not preserve exact non-inferable tokens; the supplemental exact
+McNemar comparison against \sys{} hybrid is $b{=}46$, $c{=}0$,
+$p{=}2.84\mathrm{e}{-}14$, reinforcing that verbatim retention is what the traps
+reward.
 
 Three of five pre-registered falsifiers failed.  Adding raw-evidence retention worsened
 \texttt{RegressionAfterUpdate} from 0.045 (\sys{} typed-only) to 0.091 (\sys{} hybrid)---a
diff --git a/paper/sections/06_experiments.tex b/paper/sections/06_experiments.tex
index f14f277..37029eb 100644
--- a/paper/sections/06_experiments.tex
+++ b/paper/sections/06_experiments.tex
@@ -35,10 +35,9 @@ Each template spans three sessions and includes oracle labels for useful, stale,
 The primary comparison conditions are B0 no external memory, B1 raw episodic retrieval, B2 vector retrieval over traces, B3 reflection-only memory, B4 untyped summary memory, B5 (verbatim event-memory; an offline instance-memory substitute, not stock Mem0), B6 subtask memory, B7 task-tracker memory, and DF \sys{} full.
 
 B5 requires clarification because it anchors the headline comparison. B5 is a verbatim event-memory policy and offline instance-memory substitute, Mem0-inspired but \emph{not} stock Mem0. B5 writes exactly one deterministic memory per trajectory and does not implement Mem0's online add/update/delete/no-op extraction decisions or Mem0$^g$'s graph memory and obsolete-triple handling (\texttt{src/benchmarks/baselines.py} documents B0--B7 as synthetic harness implementations, not claims of fidelity to third-party systems). Consequently DF~$>$~B5 is not a claim of beating a tuned Mem0. B6 likewise is a subtask-granularity substitute inspired by structurally aligned subtask-level memory \citep{shen2026structurally}, not a fidelity reimplementation of that published system. Consequently B6's result is not a claim about Shen et al.'s method.
-%% MEM0-ROW: pending real-Mem0 fold
-A live-Mem0 condition (B5-MEM0) is defined in the harness but was \emph{not run} for the reported rerun: without the Mem0 package, service, and key it degrades to a read-empty/write-noop policy, so we report it as unavailable rather than as a negative result for real Mem0/Mem0$^g$.
+A live-Mem0 condition (B5-MEM0) is evaluated using the pinned hosted-Mem0 baseline after the namespace and payload fixes described in \texttt{analysis/investigation-evidence/MEM0-FIX-REPORT.md}; it reaches pooled S3 pass@1 $6/66=0.091$ at S3 $n{=}66$ with 0 validity-gate exclusions. We report this as the pinned hosted-Mem0 baseline result while continuing not to claim coverage of tuned Mem0$^g$ or all possible Mem0 configurations.
 
-All synthetic B0--B7 conditions completed with 66 S3 records and 0 exclusions. DF typed-only and A11 each have one missing S3 record ($n{=}65$); DF-strict has 11 exclusions and is treated as incomplete. The reported ablations start from \sys{} typed-only: A0 episodic-only, A2 no contradiction repair, A4 no counterfactual replay, A5 no stale suppression or forgetting, A6 no retrieval gate, and A11 forced consolidation. Other planned ablations remain outside the reported table.
+All synthetic B0--B7 conditions completed with 66 S3 records and 0 exclusions. DF typed-only has one missing S3 record ($n{=}65$); A11 is complete ($n{=}66$, pooled $49/66=0.742$); DF-strict has 11 exclusions and is treated as incomplete. The reported ablations start from \sys{} typed-only: A0 episodic-only, A2 no contradiction repair, A4 no counterfactual replay, A5 no stale suppression or forgetting, A6 no retrieval gate, and A11 forced consolidation. Other planned ablations remain outside the reported table.
 
 \subsection{Controls}
 
diff --git a/paper/sections/07_results.tex b/paper/sections/07_results.tex
index 3f24055..298dfd6 100644
--- a/paper/sections/07_results.tex
+++ b/paper/sections/07_results.tex
@@ -30,8 +30,16 @@ event (Section~\ref{sec:benchmark}).  B5 is therefore strong by benchmark design
 comparisons against B5 measure verbatim-token retention fidelity rather than
 cross-session synthesis.  DF-strict has 11 validity-gate exclusions and is not comparable
 as a complete condition.  DF-strict-hybrid completed with 66 S3 records and 0 exclusions.
-B5-MEM0 produced 0 S3 records in the confirmatory fold (10 \texttt{task\_exception}
-exclusions) and is omitted from task-success comparisons.
+B5-MEM0 (live Mem0) completed with 66 S3 records and 0 validity-gate exclusions,
+achieving pooled S3 pass@1 $6/66=0.091$; the supplemental exact McNemar comparison
+against DF-hybrid is paired $n{=}66$, $b{=}46$, $c{=}0$, $p{=}2.84\mathrm{e}{-}14$.
+This supplemental comparison is not part of the pre-registered Holm-corrected McNemar
+family and does not change the one significant pre-registered task comparison.
+The mechanism is informative: real Mem0 retrieves the relevant memory at S3
+(\texttt{memory\_context=6}), but its LLM-based fact extraction does not preserve the
+exact non-inferable token.  For example, it rewrites the ASCII hyphen (U+002D) in the
+reviewer marker \texttt{EXPORT-vQ7M2-L9Z} to a Unicode non-breaking hyphen (U+2011), so
+the agent reproduces a byte-wrong marker and the hidden oracle rejects it.
 
 \begin{table}[t]
 \centering
@@ -46,6 +54,7 @@ B2 Vector traces       & 66 & 0  & 0.045 & 0.818 \\
 B3 Reflection-only     & 66 & 0  & 0.348 & 0.818 \\
 B4 Untyped summary     & 66 & 0  & 0.045 & 0.818 \\
 B5 Instance-mem.\ sub. & 66 & 0  & \textbf{0.727} & 0.818 \\
+B5-MEM0 Live Mem0      & 66 & 0  & 0.091 & 0.818 \\
 B6 Subtask memory      & 66 & 0  & 0.045 & 0.818 \\
 B7 Task tracker        & 66 & 0  & 0.045 & 0.818 \\
 \midrule
@@ -59,8 +68,8 @@ DF-strict-hybrid                 & 66 & 0  & 0.727 & 0.894 \\
 \caption{Confirmatory fold S3 pass@1 (22 sequences $\times$ 3 seeds).
   ``Excl.''\ counts validity-gate exclusions.  DF-strict has 11 exclusions and is not
   comparable as a complete condition; DF-strict-hybrid completed with 66 S3 records and
-  0 exclusions.  B5-MEM0 yielded no S3 records and is omitted.  Warmup is the pre-trap
-  warm-up pass rate.
+  0 exclusions.  B5-MEM0 is included as the live-Mem0 baseline (66 S3 records,
+  0 exclusions, pass@1 0.091).  Warmup is the pre-trap warm-up pass rate.
   Source: \texttt{CONFIRMATORY-FOLD.md}, Per-Condition S3 Pass Rates.}
 \label{tab:confirmatoryBaselines}
 \end{table}
@@ -114,6 +123,9 @@ from raw-only to hybrid: $0.788 - 0.712 = +0.076$.  This lift establishes that t
 nominal lead is not hollow---typed consolidation adds beyond the verbatim backup---but
 it is itself not statistically significant (exact McNemar $b{=}14$, $c{=}7$, $p{=}0.189$;
 Holm $p{=}0.757$).
+B5-MEM0 is not a mechanism ablation rung, but as the live-Mem0 external comparator it
+achieves $6/66=0.091$; the supplemental exact McNemar comparison against \sys{} hybrid is
+paired $n{=}66$, $b{=}46$, $c{=}0$, $p{=}2.84\mathrm{e}{-}14$.
 
 \subsection{Statistical Comparisons}
 \label{sec:results-mcnemar}
@@ -241,7 +253,7 @@ A2 & No contradiction repair & 18/22 (0.818) & 14/22 (0.636) & 17/22 (0.773) & 4
 A4 & No counterfactual replay & 13/22 (0.591) & 15/22 (0.682) & 13/22 (0.591) & 41/66 (0.621) \\
 A5 & No stale suppression & 10/22 (0.455) & 17/22 (0.773) & 16/22 (0.727) & 43/66 (0.652) \\
 A6 & No retrieval gate & 13/22 (0.591) & 13/22 (0.591) & 12/22 (0.545) & 38/66 (0.576) \\
-A11 & Forced global consolidation & 16/22 (0.727) & 17/22 (0.773) & 16/21 (0.762) & 49/65 (0.754) \\
+A11 & Forced global consolidation & 16/22 (0.727) & 17/22 (0.773) & 16/22 (0.727) & 49/66 (0.742) \\
 \bottomrule
 \end{tabular}
 }%
@@ -259,7 +271,8 @@ typed-only pipeline (0.677): the repair operator, while valuable for consistency
 contradiction-metadata memories that pollute retrieval.  This is the same mechanism that
 motivates R2 (excluding contradiction records from retrieval while retaining repair for
 maintenance), and DF-hybrid (0.788) realizes both benefits.  Forced global consolidation
-(A11, 0.754; $n{=}65$) also nominally helps trap success relative to the typed-only pipeline.
+(A11, $49/66=0.742$; $n{=}66$) is complete and also nominally helps trap success relative
+to the typed-only pipeline.
 
 \subsection{Raw-Evidence Verification}
 \label{sec:results-capsule}
@@ -272,10 +285,6 @@ written by design.  No \texttt{CONTRADICTION}-type record appeared in retrieval
 condition, confirming that R2 (exclusion of contradiction records from retrieval) is active
 throughout.
 
-%% [B5-MEM0 real-Mem0 row --- pending]
-%% B5-MEM0 produced 0 S3 records in the confirmatory fold (10 task_exception exclusions).
-%% Real-Mem0 comparison deferred to a subsequent fold.
-
 \begin{table}[t]
 \centering
 \small
diff --git a/paper/sections/08_analysis.tex b/paper/sections/08_analysis.tex
index 1256137..e324d4c 100644
--- a/paper/sections/08_analysis.tex
+++ b/paper/sections/08_analysis.tex
@@ -3,7 +3,7 @@
 The analysis is organized around the hypotheses rather than around whichever aggregate is most favorable. The confirmatory fold supports exactly one statistically significant task comparison: \sys{} typed-only beats B3 reflection-only/vector retrieval ($44/65=0.677$ vs.\ $23/66=0.348$; exact McNemar $b{=}24$, $c{=}3$, Holm $p{=}0.0002$). The headline \sys{} hybrid comparison against B5 is a nominal, non-significant lead ($52/66=0.788$ vs.\ $48/66=0.727$; $b{=}12$, $c{=}8$, Holm $p{=}1.0$). H10 is addressed by the cost limitation in Section~\ref{sec:limitations}: \sys{} hybrid spends $348{,}639$ total tokens per successful task versus $7{,}509$ for B5 ($46.4{\times}$), and raw-only is Pareto-competitive with B5 at $7{,}035$ tokens per success and $47/66=0.712$ S3, so H10 is not supported in its stronger form.
 
 \paragraph{Operation attribution.}
-The operation-ablation table tests which typed-pipeline components matter on S3. The clearest positive attribution is A0: removing typed consolidation and maintenance drops S3 from DF typed-only $44/65=0.677$ to $3/66=0.045$. The retrieval gate is the most load-bearing component: A6 no retrieval gate reaches only $38/66=0.576$. Removing counterfactual replay (A4, $41/66=0.621$) or stale suppression (A5, $43/66=0.652$) also lowers S3 relative to typed-only. By contrast, A2 no contradiction repair rises to $49/66=0.742$, showing that repair improves consistency but can pollute retrieval when contradiction records are admitted. A11 forced consolidation is $49/65=0.754$, a nominal task-success gain with one missing S3 record rather than a completed 66-record win.
+The operation-ablation table tests which typed-pipeline components matter on S3. The clearest positive attribution is A0: removing typed consolidation and maintenance drops S3 from DF typed-only $44/65=0.677$ to $3/66=0.045$. The retrieval gate is the most load-bearing component: A6 no retrieval gate reaches only $38/66=0.576$. Removing counterfactual replay (A4, $41/66=0.621$) or stale suppression (A5, $43/66=0.652$) also lowers S3 relative to typed-only. By contrast, A2 no contradiction repair rises to $49/66=0.742$, showing that repair improves consistency but can pollute retrieval when contradiction records are admitted. A11 forced consolidation is complete at $49/66=0.742$, a nominal task-success gain over typed-only.
 
 \paragraph{Memory lifecycle audits.}
 No separate human-audit trace is claimed in this version. The reported lifecycle evidence is record-backed and diagnostic: raw-evidence capsules are present for 66/66 raw-only S3 records and 50/66 hybrid S3 records; \texttt{CONTRADICTION} records appear in retrieval for 0/66 raw-only and 0/66 hybrid records; and the hygiene panel exposes the retention-vs-supersession tension through \texttt{RegressionAfterUpdate} (0.091 for hybrid vs.\ 0.045 for typed-only and 0.136 for B5/raw-only). This keeps subjective memory labels secondary to executable S3 outcomes.
diff --git a/paper/sections/09_limitations.tex b/paper/sections/09_limitations.tex
index c3bb239..0bf2da2 100644
--- a/paper/sections/09_limitations.tex
+++ b/paper/sections/09_limitations.tex
@@ -55,10 +55,11 @@ cross-model judge.
 The reported numbers are from the pre-registered three-seed confirmatory fold.  Complete
 conditions use $n{=}66$ trials (22 sequences $\times$ 3 seeds); DF-strict has 11
 validity-gate exclusions and is not comparable as a complete condition; DF-strict-hybrid
-completed with 66 S3 records and 0 exclusions; and B5-MEM0 produced 0 S3 records
-(10 \texttt{task\_exception} exclusions).  Pipeline component ablations are now reported in
-Section~\ref{sec:results-ablations}; A11 has one missing S3 record ($n{=}65$, pooled
-$49/65=0.754$).
+completed with 66 S3 records and 0 exclusions; and B5-MEM0 completed with 66 S3 records
+and 0 validity-gate exclusions, achieving pooled S3 pass@1 $6/66=0.091$.  Pipeline
+component ablations are now reported in Section~\ref{sec:results-ablations}; A11 is
+complete ($n{=}66$, pooled $49/66=0.742$), while DF typed-only has one missing S3 record
+($n{=}65$).
 \sys{} hybrid fails $14/66$ total S3 trials across all seeds (pooled pass@1 0.788); the
 per-condition exclusion table is in \texttt{CONFIRMATORY-FOLD.md}.
 
@@ -167,9 +168,16 @@ validity gates.  The guarantee is \emph{not} network
 isolation: the container has a default network bridge for the hosted model call, so
 external state is reachable in principle.  The guarantee rests entirely on the container
 wall and would be reintroduced as a risk by a future debug mount or non-container code
-path.  B5-MEM0 (the real-Mem0 condition) is defined in the harness but produced no S3
-records in the confirmatory fold; we report it as unavailable rather than as evidence about
-real Mem0.
+path.  B5-MEM0 (the real-Mem0 condition) is now reported as an evaluated hosted-Mem0
+baseline: pooled S3 pass@1 $6/66=0.091$ at S3 $n{=}66$, with supplemental exact McNemar
+vs.\ DF-hybrid paired $n{=}66$, $b{=}46$, $c{=}0$, $p{=}2.84\mathrm{e}{-}14$.  This
+remains a pinned hosted-Mem0 baseline, not a tuned Mem0$^g$ claim.
+The failure mechanism is verbatim-vs-semantic memory: Mem0 retrieves the relevant memory at
+S3 (\texttt{memory\_context=6}), but its LLM-based fact extraction can rewrite exact
+non-inferable tokens, such as changing the ASCII hyphen (U+002D) in
+\texttt{EXPORT-vQ7M2-L9Z} to a Unicode non-breaking hyphen (U+2011).  The agent then
+reproduces a byte-wrong marker and the hidden oracle rejects it, illustrating why B5's
+verbatim event-memory and \sys{} raw-evidence capsules preserve exact tokens.
 
 \bench{} is controlled by design.  Curated repositories and injected pathologies make
 memory failures auditable, but they may underrepresent the scale, dependency structure,
@@ -192,9 +200,10 @@ would require a separate adapter and answers a different question.
 Baselines may be weaker than their best possible tuned implementation.  B5 writes one
 deterministic memory per trajectory and does not implement Mem0's online
 add/update/delete/no-op extraction or graph memory, so \sys{}~$>$~B5 is not a claim of
-beating a tuned Mem0.  If an exact baseline implementation is unavailable, the paper names
-the substitute, lists unsupported features, and avoids implying that it fully represents
-the original system.
+beating a tuned Mem0.  When an exact baseline implementation is available and run, the
+paper reports that pinned implementation directly; when only substitutes or untuned variants
+are available, it names the substitute or configuration, lists unsupported features, and
+avoids implying full coverage of the original system.
 
 Finally, the terms \emph{sleep} and \emph{dream} are labels for offline computation and
 replay artifacts.  The paper does not claim biological equivalence, consciousness, or
```

## Verification

### Compile

Command:

```bash
cd paper && tectonic main.tex
```

Result: RC=101. Tectonic failed before TeX compilation began.

Error lines:

```text
thread 'reqwest-internal-sync-runtime' (2128510) panicked at <USER_HOME>/Library/Caches/Homebrew/cargo_cache/registry/src/index.crates.io-1949cf8c6b5b557f/system-configuration-0.6.1/src/dynamic_store.rs:154:1:
Attempted to create a NULL object.
thread 'main' (2128503) panicked at <USER_HOME>/Library/Caches/Homebrew/cargo_cache/registry/src/index.crates.io-1949cf8c6b5b557f/reqwest-0.12.20/src/blocking/client.rs:1397:5:
event loop thread panicked
```

Additional attempts:

- `tectonic -C main.tex`: RC=101, same panic.
- `tectonic --untrusted -C main.tex`: RC=101, same panic.
- `tectonic -X compile main.tex`: RC=101, same panic.
- `HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= NO_PROXY='*' http_proxy= https_proxy= all_proxy= no_proxy='*' tectonic main.tex`: RC=101, same panic.

This is a Tectonic/Homebrew runtime failure in the reqwest/SystemConfiguration path, not a TeX diagnostic. I could not produce the required RC=0 compile in this environment.

### Undefined References

Command:

```bash
cd paper && tectonic --keep-logs main.tex 2>&1 | grep -iE "undefined|citation.*undefined"
```

Result: RC=1 from `grep`; no matching lines were emitted. Caveat: the upstream `tectonic --keep-logs main.tex` command still exits RC=101 with the same runtime panic before compilation.

Output:

```text

```

### Slop / Leftover Grep

Command:

```bash
grep -rnE "MEM0-ROW|pending real-Mem0|not run|unavailable|0 S3 records|RESULTS PENDING|realmem0" paper/sections/
```

Result: RC=0 with three hits, all unrelated to Mem0 availability. The hits are the generic validity-gate `isolation_unavailable` token and generic container-isolation prose.

Output:

```text
paper/sections/07_results.tex:10:\texttt{codex\_exec\_failed}, or \texttt{isolation\_unavailable}; excluded counts are
paper/sections/06_experiments.tex:46:Two disclosures affect all conditions equally but bound external validity. First, the wake-phase agent container is \emph{edit-only}: the Codex agent image installs \texttt{git} and the Codex CLI but not Python or pytest (\texttt{scripts/Dockerfile.codex-agent}), so the wake agent cannot run the repository's Python tests inside its own container; the oracle scores the agent's produced diff externally. This makes the wake agents weaker than a full self-testing SWE agent and may interact with which memories matter, though it applies identically to every condition. Second, sleep cost is reported as a cost frontier in Section~\ref{sec:limitations}, not omitted.
paper/sections/06_experiments.tex:61:Hermeticity is part of the experimental method. We use ``hermetic'' in a precise, \emph{filesystem-hermetic} sense: each wake agent runs inside an isolated OrbStack Linux container with only the current task worktree mounted read-write and a read-only copied Codex home mounted for the model call. The benchmark repository, hidden oracles, reference solutions, sequence records, analysis files, and logs are physically absent from the container filesystem. Sleep judges also run in isolated containers with Codex authentication mounted, no benchmark repository mounted, and no access to task worktrees or oracle trees. The container path is fail-closed: if isolation is unavailable, the run records an isolation failure rather than falling back to an unjailed agent. We do \emph{not} claim network isolation: the container has a default network bridge (required for the hosted model call) and receives a read-only copied Codex home, so external network state is reachable in principle even though the task prompt instructs the agent not to make network calls. The guarantee we make is that the scored benchmark material is outside the container filesystem wall; hidden oracles, reference solutions, and sequence files still live in the host repository tree, so the guarantee rests on that container wall and would be reintroduced as a risk by a future debug mount or non-container path (\texttt{analysis/fold/HERMETICITY-MANIFEST.md} records the exact mount set and network policy for the reported run).
```

### A11 Stale-Number Grep

Command:

```bash
grep -rnE "49/65|0\.754|n=65" paper/sections/
```

Result: RC=1; no hits.

Output:

```text

```

## Checklist

- [x] 1. Applied the REFOLD-PREP B5-MEM0 sentence edits in `06_experiments.tex`, `07_results.tex`, and `09_limitations.tex` with literal final numbers and no `\realmem0*` macros.
- [x] 2. Added B5-MEM0 to `tab:confirmatoryBaselines` immediately after B5 with `n=66`, `0` exclusions, S3 `0.091`, warmup `0.818`; did not bold it; did not add it to `tab:ladder`.
- [x] 3. Added the Mem0 mechanism: relevant memory retrieved at S3 (`memory_context=6`), LLM extraction rewrites exact non-inferable tokens such as `EXPORT-vQ7M2-L9Z` ASCII hyphens (U+002D) to non-breaking hyphens (U+2011), producing byte-wrong oracle failures; framed as verbatim-vs-semantic memory and pinned hosted-Mem0, not tuned Mem0^g.
- [x] 4. Reported supplemental exact McNemar DF-hybrid vs B5-MEM0 as paired `n=66`, `b=46`, `c=0`, `p=2.84e-14`; explicitly outside the pre-registered Holm family and not a tuned Mem0^g claim.
- [x] 5. Corrected A11 everywhere found in the requested sections to `16/22`, `17/22`, `16/22`, pooled `49/66=0.742`, `n=66`, `0` exclusions; retained DF typed-only as the condition with one missing S3 record (`44/65=0.677`).
- [x] 6. Added the abstract sentence for pinned live-Mem0 B5-MEM0, its token-preservation mechanism, and supplemental McNemar result without changing the pre-registered significance claim.

## Integrity Notes

- B5-MEM0 is always framed as a pinned hosted-Mem0 baseline, not tuned Mem0 or Mem0^g.
- The DF-hybrid vs B5-MEM0 comparison is always framed as supplemental/exploratory and outside the pre-registered Holm-corrected family.
- The one significant pre-registered task comparison remains \sys{} typed-only vs B3.
- No other condition numbers were intentionally changed.
- Verification is incomplete only because the installed Tectonic binary panics before TeX compilation starts; stale-text greps pass with the unrelated isolation caveat above.
