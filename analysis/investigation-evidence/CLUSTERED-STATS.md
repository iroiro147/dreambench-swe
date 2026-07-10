# Clustered Statistical Sensitivity

- Sources read: `analysis/fold/confirmatory.json` and `analysis/investigation-evidence/CONFIRMATORY-FOLD.md`.
- The published pooled McNemar family in `CONFIRMATORY-FOLD.md` is cross-checked below from `confirmatory.json`.
- Family: DF-hybrid vs B5, DF-hybrid vs DF, DF-raw-only vs B5, DF vs B5, DF vs B3.
- Outcome: S3 `pass_at_1`, paired by `(seed, oracle_id/trap)`.
- `b` means the first condition passes and the second fails; `c` means the second condition passes and the first fails.
- `DF` is the typed-only DreamForge condition in the confirmatory fold.
- Missingness: `DF` is missing `seed3:expr-stale-registry-stability` after the validity gate.

## Test Definitions

- Per-seed McNemar: exact two-sided McNemar within each seed; Holm adjustment is applied separately within each seed's five-comparison family.
- Trap-level majority collapse: require all three seed outcomes per condition for a trap, majority-vote each condition, then run exact two-sided McNemar over complete trap pairs; Holm adjustment is across the five collapsed comparisons.
- Trap-clustered sign/permutation test: for each trap, compute `d_t = #first-only seeds - #second-only seeds` over available paired seeds, then enumerate all `2^22` trap label swaps by exact sign-flip dynamic programming; the two-sided statistic is `abs(sum_t d_t)`. Holm adjustment is across the five clustered comparisons.

## Published Pooled McNemar Cross-Check

| Comparison | paired n | b | c | exact p | Holm p | Holm 0.05 | missing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DF-hybrid vs B5 | 66 | 12 | 8 | 0.503444671631 | 1 | no | - |
| DF-hybrid vs DF | 65 | 14 | 7 | 0.189247131348 | 0.756988525391 | no | second missing: seed3:expr-stale-registry-stability |
| DF-raw-only vs B5 | 66 | 0 | 1 | 1 | 1 | no | - |
| DF vs B5 | 65 | 5 | 9 | 0.423950195312 | 1 | no | first missing: seed3:expr-stale-registry-stability |
| DF vs B3 | 65 | 24 | 3 | 4.92334365845e-05 | 0.000246167182922 | yes | first missing: seed3:expr-stale-registry-stability |

## Per-Seed Exact McNemar

| Comparison | seed | paired n | b | c | exact p | seed-family Holm p | Holm 0.05 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DF-hybrid vs B5 | 1 | 22 | 3 | 3 | 1 | 1 | no |
| DF-hybrid vs B5 | 2 | 22 | 4 | 3 | 1 | 1 | no |
| DF-hybrid vs B5 | 3 | 22 | 5 | 2 | 0.453125 | 1 | no |
| DF-hybrid vs DF | 1 | 22 | 5 | 2 | 0.453125 | 1 | no |
| DF-hybrid vs DF | 2 | 22 | 5 | 2 | 0.453125 | 1 | no |
| DF-hybrid vs DF | 3 | 21 | 4 | 3 | 1 | 1 | no |
| DF-raw-only vs B5 | 1 | 22 | 0 | 1 | 1 | 1 | no |
| DF-raw-only vs B5 | 2 | 22 | 0 | 0 | 1 | 1 | no |
| DF-raw-only vs B5 | 3 | 22 | 0 | 0 | 1 | 1 | no |
| DF vs B5 | 1 | 22 | 1 | 4 | 0.375 | 1 | no |
| DF vs B5 | 2 | 22 | 1 | 3 | 0.625 | 1 | no |
| DF vs B5 | 3 | 21 | 3 | 2 | 1 | 1 | no |
| DF vs B3 | 1 | 22 | 6 | 1 | 0.125 | 0.625 | no |
| DF vs B3 | 2 | 22 | 9 | 2 | 0.0654296875 | 0.3271484375 | no |
| DF vs B3 | 3 | 21 | 9 | 0 | 0.00390625 | 0.01953125 | yes |

## Trap-Level Majority Collapse

| Comparison | trap n | b | c | exact p | Holm p | Holm 0.05 | excluded traps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DF-hybrid vs B5 | 22 | 4 | 1 | 0.375 | 1 | no | - |
| DF-hybrid vs DF | 21 | 4 | 1 | 0.375 | 1 | no | expr-stale-registry-stability |
| DF-raw-only vs B5 | 22 | 0 | 0 | 1 | 1 | no | - |
| DF vs B5 | 21 | 1 | 2 | 1 | 1 | no | expr-stale-registry-stability |
| DF vs B3 | 21 | 8 | 1 | 0.0390625 | 0.1953125 | no | expr-stale-registry-stability |

## Trap-Clustered Exact Sign/Permutation Test

| Comparison | clusters | paired cells | b | c | observed b-c | cluster score counts | paired seeds/cluster | clustered p | Holm p | Holm 0.05 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DF-hybrid vs B5 | 22 | 66 | 12 | 8 | 4 | -2:1, -1:6, 0:10, 1:1, 2:1, 3:3 | 3:22 | 0.6552734375 | 1 | no |
| DF-hybrid vs DF | 22 | 65 | 14 | 7 | 7 | -1:4, 0:11, 1:4, 2:2, 3:1 | 2:1, 3:21 | 0.2412109375 | 0.96484375 | no |
| DF-raw-only vs B5 | 22 | 66 | 0 | 1 | -1 | -1:1, 0:21 | 3:22 | 1 | 1 | no |
| DF vs B5 | 22 | 65 | 5 | 9 | -4 | -2:2, -1:5, 0:12, 1:2, 3:1 | 2:1, 3:21 | 0.560546875 | 1 | no |
| DF vs B3 | 22 | 65 | 24 | 3 | 21 | -2:1, -1:1, 0:8, 1:4, 2:4, 3:4 | 2:1, 3:21 | 0.00732421875 | 0.03662109375 | yes |

## Verdict Table

| Comparison | pooled Holm | all per-seed Holm | per-seed detail | trap-collapse Holm | clustered Holm | conclusion vs pooled |
| --- | --- | --- | --- | --- | --- | --- |
| DF-hybrid vs B5 | no | no | s1=no, s2=no, s3=no | no | no | unchanged |
| DF-hybrid vs DF | no | no | s1=no, s2=no, s3=no | no | no | unchanged |
| DF-raw-only vs B5 | no | no | s1=no, s2=no, s3=no | no | no | unchanged |
| DF vs B5 | no | no | s1=no, s2=no, s3=no | no | no | unchanged |
| DF vs B3 | yes | no | s1=no, s2=no, s3=yes | no | yes | changes in per-seed-all, trap-collapse |

## Plain Verdict

- `DF` typed-only vs `B3`: pooled Holm significance does not fully survive clustering sensitivity. It is significant in the original pooled test (Holm p=0.000246167182922) and in the exact trap-clustered sign/permutation test (Holm p=0.03662109375), but it is not significant in all three per-seed tests (seed Holm p values: s1=0.625, s2=0.3271484375, s3=0.01953125) and it is not significant after complete 3-seed trap-majority collapse (Holm p=0.1953125; raw collapse p=0.0390625).
- `DF-hybrid` vs `B5`: no significance appears under any variant. Pooled Holm p=1, per-seed Holm p values are s1=1, s2=1, s3=1, trap-collapse Holm p=1, and clustered Holm p=1.
- No comparison that was non-significant in the pooled Holm family becomes significant after these clustered/seed sensitivity checks.
