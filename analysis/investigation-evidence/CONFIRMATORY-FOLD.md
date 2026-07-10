# DreamBench-SWE Confirmatory Fold

- Results root: `<LOCAL_ROOT>/experiments/results/v2-confirmatory-20260706T074759Z`
- Result files discovered: 1890
- Dedup: newest record per (condition, seed, oracle_id) by results/<YYYYMMDDTHHMMSSZ>-SLICE-* timestamp.
- Validity gate: exclude records whose `error_type` contains `task_exception`, `isolation_unavailable`, `codex_exec_failed`.
- Outcome: S3 `pass_at_1`; S3 means `oracle_id` ends with `-s3` or `ordinal == 3`.
- Warmup: valid non-S3 records; grouped runs can therefore contribute ordinals beyond 1 and 2.
- Hygiene: metric means and count sums over unique result files that contributed at least one deduped record.

## Per-Condition S3 Pass Rates

| Condition | seed 1 S3 | seed 2 S3 | seed 3 S3 | pooled S3 | warmup | S3 n | warmup n | excluded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | 7/60 0.117 | 7/60 0.117 | 7/60 0.117 | 21/180 0.117 | 287/360 0.797 | 180 | 360 | 0 |
| B1 | 7/60 0.117 | 7/60 0.117 | 7/60 0.117 | 21/180 0.117 | 290/360 0.806 | 180 | 360 | 0 |
| B2 | 6/60 0.100 | 7/60 0.117 | 7/60 0.117 | 20/180 0.111 | 285/360 0.792 | 180 | 360 | 0 |
| B3 | 22/60 0.367 | 23/60 0.383 | 22/60 0.367 | 67/180 0.372 | 290/360 0.806 | 180 | 360 | 0 |
| B4 | 7/60 0.117 | 7/60 0.117 | 7/60 0.117 | 21/180 0.117 | 288/360 0.800 | 180 | 360 | 0 |
| B5 | 30/60 0.500 | 29/60 0.483 | 30/60 0.500 | 89/180 0.494 | 292/360 0.811 | 180 | 360 | 0 |
| B6 | 7/60 0.117 | 7/60 0.117 | 7/60 0.117 | 21/180 0.117 | 286/360 0.794 | 180 | 360 | 0 |
| B7 | 7/60 0.117 | 5/60 0.083 | 7/60 0.117 | 19/180 0.106 | 281/360 0.781 | 180 | 360 | 0 |
| DF | 31/60 0.517 | 27/60 0.450 | 22/60 0.367 | 80/180 0.444 | 292/360 0.811 | 180 | 360 | 0 |
| DF-strict | 12/60 0.200 | 14/60 0.233 | 16/60 0.267 | 42/180 0.233 | 287/360 0.797 | 180 | 360 | 0 |
| DF-hybrid | 31/60 0.517 | 32/60 0.533 | 32/60 0.533 | 95/180 0.528 | 293/360 0.814 | 180 | 360 | 0 |
| DF-raw-only | 30/60 0.500 | 26/60 0.433 | 28/60 0.467 | 84/180 0.467 | 290/360 0.806 | 180 | 360 | 0 |
| DF-strict-hybrid | 28/60 0.467 | 26/60 0.433 | 24/60 0.400 | 78/180 0.433 | 291/360 0.808 | 180 | 360 | 0 |
| A0 | 7/60 0.117 | 6/60 0.100 | 7/60 0.117 | 20/180 0.111 | 290/360 0.806 | 180 | 360 | 0 |
| A2 | 30/60 0.500 | 30/60 0.500 | 28/60 0.467 | 88/180 0.489 | 294/360 0.817 | 180 | 360 | 0 |
| A4 | 27/60 0.450 | 25/60 0.417 | 25/60 0.417 | 77/180 0.428 | 294/360 0.817 | 180 | 360 | 0 |
| A5 | 28/60 0.467 | 24/60 0.400 | 27/60 0.450 | 79/180 0.439 | 292/360 0.811 | 180 | 360 | 0 |
| A6 | 25/60 0.417 | 22/60 0.367 | 25/60 0.417 | 72/180 0.400 | 289/360 0.803 | 180 | 360 | 0 |
| A11 | 25/60 0.417 | 23/60 0.383 | 22/60 0.367 | 70/180 0.389 | 294/360 0.817 | 180 | 360 | 0 |
| B5-MEM0 | 7/60 0.117 | 7/60 0.117 | 7/60 0.117 | 21/180 0.117 | 288/360 0.800 | 180 | 360 | 0 |
| B5-MEM0-LIT | 7/60 0.117 | 6/60 0.100 | 7/60 0.117 | 20/180 0.111 | 292/360 0.811 | 180 | 360 | 0 |

## Validity-Gate Exclusions

| Condition | Excluded | Reasons |
| --- | --- | --- |
| B0 | 0 | - |
| B1 | 0 | - |
| B2 | 0 | - |
| B3 | 0 | - |
| B4 | 0 | - |
| B5 | 0 | - |
| B6 | 0 | - |
| B7 | 0 | - |
| DF | 0 | - |
| DF-strict | 0 | - |
| DF-hybrid | 0 | - |
| DF-raw-only | 0 | - |
| DF-strict-hybrid | 0 | - |
| A0 | 0 | - |
| A2 | 0 | - |
| A4 | 0 | - |
| A5 | 0 | - |
| A6 | 0 | - |
| A11 | 0 | - |
| B5-MEM0 | 0 | - |
| B5-MEM0-LIT | 0 | - |

## Ablation Ladder

| Metric | B5 | DF (typed-only) | DF-raw-only | DF-hybrid |
| --- | --- | --- | --- | --- |
| pooled S3 | 89/180 0.494 | 80/180 0.444 | 84/180 0.467 | 95/180 0.528 |
| per-seed S3 | s1=30/60 0.500, s2=29/60 0.483, s3=30/60 0.500 | s1=31/60 0.517, s2=27/60 0.450, s3=22/60 0.367 | s1=30/60 0.500, s2=26/60 0.433, s3=28/60 0.467 | s1=31/60 0.517, s2=32/60 0.533, s3=32/60 0.533 |

## Exact McNemar Family

| Comparison | paired n | b first wins | c second wins | discordant n | exact p | Holm p | Holm 0.05 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DF-hybrid vs B5 | 180 | 21 | 15 | 36 | 0.4050 | 0.8150 | no |
| DF-hybrid vs DF | 180 | 33 | 18 | 51 | 0.0489 | 0.2444 | no |
| DF-raw-only vs B5 | 180 | 7 | 12 | 19 | 0.3593 | 0.8150 | no |
| DF vs B5 | 180 | 22 | 31 | 53 | 0.2717 | 0.8150 | no |
| DF vs B3 | 180 | 32 | 19 | 51 | 0.0919 | 0.3677 | no |

## Supplemental McNemar Comparisons

Supplemental comparisons are not part of the pre-registered Holm-corrected McNemar family and do not change its adjusted p-values.

| Comparison | paired n | b first wins | c second wins | discordant n | exact p |
| --- | --- | --- | --- | --- | --- |
| DF-hybrid vs B5-MEM0 | 180 | 83 | 9 | 92 | 3.92e-16 |
| DF-hybrid vs B5-MEM0-LIT | 180 | 84 | 9 | 93 | 2.17e-16 |

## Hygiene Panel

| Condition | dirs | UsefulMemoryPrecision | RepeatedErrorRate | RegressionAfterUpdate | ScopeAccuracy | ContradictionRepairAccuracy | HumanFeedbackUseAccuracy | TransferScore | HarmfulMemoryRate | AdmTok/task | contaminated | retrieved_memories | sleep_writes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | 90 | NA | 0.258 | 0.200 | 0.478 | 0.071 | 0.000 | 0.000 | 0.000 | 1.0 | 2 | 0 | 0 |
| B1 | 90 | 0.400 | 0.258 | 0.189 | 0.544 | 0.071 | 0.133 | 0.000 | 0.000 | 118.1 | 1 | 522 | 0 |
| B2 | 90 | 0.486 | 0.258 | 0.194 | 0.547 | 0.071 | 0.139 | 0.139 | 0.000 | 119.6 | 0 | 522 | 540 |
| B3 | 90 | 0.556 | 0.242 | 0.161 | 0.700 | 0.405 | 0.389 | 0.389 | 0.000 | 116.9 | 1 | 519 | 540 |
| B4 | 90 | 0.469 | 0.267 | 0.211 | 0.531 | 0.071 | 0.106 | 0.106 | 0.000 | 112.7 | 0 | 515 | 540 |
| B5 | 90 | 0.556 | 0.225 | 0.128 | 0.758 | 0.595 | 0.500 | 0.500 | 0.000 | 130.3 | 2 | 519 | 540 |
| B6 | 90 | 0.433 | 0.250 | 0.189 | 0.528 | 0.071 | 0.100 | 0.100 | 0.000 | 96.6 | 0 | 522 | 540 |
| B7 | 90 | 0.872 | 0.258 | 0.189 | 0.544 | 0.063 | 0.133 | 0.133 | 0.000 | 81.0 | 1 | 360 | 540 |
| DF | 90 | 0.228 | 0.225 | 0.122 | 0.706 | 0.444 | 0.433 | 0.433 | 0.000 | 207.9 | 2 | 1409 | 3668 |
| DF-strict | 90 | 0.332 | 0.217 | 0.172 | 0.609 | 0.230 | 0.256 | 0.256 | 0.000 | 135.3 | 2 | 889 | 4028 |
| DF-hybrid | 90 | 0.264 | 0.142 | 0.106 | 0.757 | 0.579 | 0.522 | 0.522 | 0.000 | 216.6 | 2 | 1267 | 4561 |
| DF-raw-only | 90 | 0.506 | 0.200 | 0.150 | 0.740 | 0.587 | 0.494 | 0.494 | 0.000 | 125.1 | 1 | 529 | 540 |
| DF-strict-hybrid | 90 | 0.372 | 0.192 | 0.133 | 0.718 | 0.484 | 0.433 | 0.433 | 0.000 | 161.4 | 0 | 896 | 4874 |
| A0 | 90 | 0.451 | 0.250 | 0.194 | 0.553 | 0.071 | 0.150 | 0.150 | 0.000 | 230.9 | 0 | 528 | 540 |
| A2 | 90 | 0.215 | 0.192 | 0.106 | 0.732 | 0.516 | 0.494 | 0.494 | 0.000 | 236.1 | 1 | 1418 | 1668 |
| A4 | 90 | 0.238 | 0.192 | 0.139 | 0.696 | 0.429 | 0.422 | 0.422 | 0.000 | 176.9 | 1 | 1256 | 1998 |
| A5 | 90 | 0.182 | 0.183 | 0.122 | 0.731 | 0.437 | 0.483 | 0.483 | 0.000 | 345.8 | 1 | 1989 | 3672 |
| A6 | 90 | 0.173 | 0.192 | 0.128 | 0.702 | 0.373 | 0.422 | 0.422 | 0.000 | 397.1 | 2 | 2397 | 3664 |
| A11 | 90 | 0.236 | 0.200 | 0.111 | 0.688 | 0.389 | 0.406 | 0.406 | 0.000 | 215.9 | 0 | 1465 | 5429 |
| B5-MEM0 | 90 | NA | 0.250 | 0.217 | 0.478 | 0.071 | 0.000 | 0.000 | 0.000 | 1.0 | 1 | 0 | 0 |
| B5-MEM0-LIT | 90 | NA | 0.242 | 0.217 | 0.478 | 0.063 | 0.000 | 0.000 | 0.000 | 1.0 | 1 | 0 | 0 |

## Capsule + CR Check

| Condition | S3 n | raw-evidence capsule | CONTRADICTION item |
| --- | --- | --- | --- |
| DF-hybrid | 180 | 147/180 0.817 | 0/180 0.000 |
| DF-raw-only | 180 | 175/180 0.972 | 0/180 0.000 |

## Falsifier Checks

| Status | Check | Detail |
| --- | --- | --- |
| PASS | B0 pooled S3 <= 0.40 preferred / <= 0.50 maximum | observed=0.117 (21/180); preferred_holds=true |
| FAIL | DF-strict-hybrid pooled S3 >= 0.60 and no more than 0.12 below DF-hybrid | observed=0.433; DF-hybrid=0.528; gap=0.094 |
| FAIL | DF-hybrid UsefulMemoryPrecision >= 0.45 and no more than 0.15 below B5 | DF-hybrid=0.264; B5=0.556; gap=0.292 |
| PASS | DF-hybrid seed spread (max-min per-seed S3) <= 0.12 preferred / <= 0.16 maximum | observed=0.017 from rates=0.517, 0.533, 0.533; preferred_holds=true |
| FAIL | DF-hybrid RepeatedErrorRate <= B5 and <= 0.15; RegressionAfterUpdate <= 0.08 | DF-hybrid RepeatedErrorRate=0.142; B5 RepeatedErrorRate=0.225; RegressionAfterUpdate=0.106 |
