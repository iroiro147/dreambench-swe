# Cross-Model Hygiene Rescore

- Generated: `2026-07-04T01:55:32.660117+00:00`
- Results root: `experiments/results`
- Sequence records: `experiments/env/sequences.jsonl`
- Cross-model judge: `glm-latest`
- Pass@1 untouched: `true`
- Judge calls: `132` ok=`132` abstain=`0`

This rescore uses the same stored container records. It does not rerun agents and does not recompute executable Pass@1.

## Survival Check

- Task-coupled family strict win survives: `false`
- 4/5 independent-family best-or-tied verdict survives: `false`
- Best-or-tied family count: `0`

## Per-Condition Cross-Model Hygiene

| Condition | Records | Abstentions | ContradictionRepairAccuracy | HumanFeedbackUseAccuracy | TransferScore | ScopeAccuracy | UsefulMemoryPrecision | RepeatedErrorRate | RegressionAfterUpdate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B5 | 66 | 0 | 0.727 | 0.682 | 0.773 | 0.841 | 0.977 | 0.136 | 0.136 |
| DF-HYBRID | 66 | 0 | 0.909 | 0.818 | 0.864 | 0.909 | 0.758 | 0.091 | 0.045 |

## DF Family Verdicts

| Family | Metrics | Verdict |
|---|---|---|
| precision | UsefulMemoryPrecision | not_computable |
| regression | RegressionAfterUpdate | not_computable |
| repeated | RepeatedErrorRate | not_computable |
| retrieval-avoidance | StaleMemoryActivationRate, HarmfulMemoryRate | not_computable |
| task-coupled | ContradictionRepairAccuracy, HumanFeedbackUseAccuracy, TransferScore, ScopeAccuracy | not_computable |
