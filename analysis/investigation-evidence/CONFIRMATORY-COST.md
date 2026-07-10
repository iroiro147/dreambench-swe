# Confirmatory cost frontier (total tokens per successful S3 task)

| Condition | TotalTokens | successes | tok/success | xB5 |
|---|---|---|---|---|
| B5 | 1,081,437 | 144 | 7,509 | 1.0x |
| DF-raw-only | 1,013,084 | 144 | 7,035 | 0.9x |
| DF (typed-only) | 47,254,959 | 156 | 302,916 | 40.3x |
| DF-hybrid | 54,736,476 | 157 | 348,639 | 46.4x |
| DF-strict-hybrid | 55,986,744 | 153 | 365,926 | 48.7x |

Source: metrics.TotalTokens summed across result dirs / counts.successes, confirmatory fold.
Key: DF-raw-only is Pareto-competitive with B5 (0.9x cost, 0.712 vs 0.727 success). The typed
pipeline's +0.076 trap-success over raw-only costs ~50x (348,639/7,035) more tokens. DreamForge does NOT occupy
a favorable success-per-cost frontier — the honest H10 finding.
