# DreamBench-SWE Real Experiment Protocol

This file is the runbook for the real experiment after the author chooses the model and compute budget. The current repo has a working synthetic smoke harness, but no real model experiment has been run.

## Non-Negotiable Rule

No result number enters `paper/sections/07_results.tex` unless it is reproduced from a frozen real-run log under `experiments/results/<run_id>/`. Smoke outputs under `experiments/results/smoke/` are not paper evidence.

## Inputs To Freeze Before Launch

- `run_id`: stable identifier for the run.
- Model config: wake model, judge model, provider endpoints, versions, context limits, temperatures, decoding params.
- Budget config: wake-token budget, read-memory-token budget, tool-call budget, wall-clock limits, sleep-token budget, judge budget, total-cost ceiling.
- Seeds: one matched seed schedule reused across all conditions.
- Tasks: `experiments/data/tasks/manifest.json` plus the referenced task files, with development/test split fixed before scoring.
- Conditions: exact B0-B7, DF, and selected ablations.
- Adapter: the concrete coding-agent adapter plugged into `src/agents/coding_agent_adapter.py`.
- Baseline versions: Mem0, subtask-memory implementation, task-tracker/beads implementation, vector store, embedding model, and all unsupported features.

## Conditions

All conditions use the same wake-phase base model, model version, prompts visible to the wake agent, task order, seeds, repository checkout rule, verification commands, tool budget, wake-token budget, read-token budget, and stopping criteria unless the condition definition explicitly forbids memory.

| ID | Name | Memory policy |
|---|---|---|
| `B0` | No external memory | Current task prompt and repository state only; no prior external memory read or write. |
| `B1` | Raw episodic retrieval | Retrieve raw trajectory chunks or raw episode summaries; no derived memory lifecycle. |
| `B2` | Vector RAG over traces | Embedding retrieval over prior traces; no typed schema, repair, supersession, or stale suppression. |
| `B3` | Reflection-only memory | Store and retrieve free-form verbal lessons after each session. |
| `B4` | Untyped summary memory | Store compressed natural-language summaries under the same storage/read budget as typed memory. |
| `B5` | B-Mem0 | Stock Mem0-style online add/update/delete/no-op memory where available; if not exact, name the substitute and do not call it Mem0. |
| `B6` | Subtask memory | Structurally aligned subtask-level memory for SWE-agent trajectories, or the closest pinned reproducible implementation. |
| `B7` | Task-tracker memory | Structured persistent task/decision/open-issue memory, preferably beads-style if selected, without the reference-probe replay and hygiene pipeline. |
| `DF` | Reference-probe typed-only | Typed consolidation, contradiction repair, causal failure extraction, counterfactual replay, stale suppression, provenance gating, and retrieval gating. |

Required ablations for a constrained MVP: `A0` episodic-only, `A2` no contradiction repair, `A4` no counterfactual replay, `A5` no stale suppression/forgetting, `A6` no retrieval gate, and `A11` forced consolidation. Full matrix adds `A1`, `A3`, `A7`, `A8`, `A9`, `A10`, `A12`, and `A13` from `docs/09_metrics_and_ablations.md`.

## Task Loader

The multi-session task fixtures live in `experiments/data/tasks/`. The loader is `src/benchmarks/task_loader.py`.

Current fixture set:

- `manifest.json` lists 15 sequences and 60 tasks.
- Sequence types: `convention-learning`, `generated-files`, `stale-architecture`, `reviewer-preference`, `flaky-test`.
- Each sequence has 4 sessions.

Regenerate fixtures only before the run is frozen:

```bash
python3 src/benchmarks/task_loader.py
```

After freezing, do not regenerate or edit fixtures. Add split and seed metadata to the frozen run config, not to ad hoc notes.

## Agent Adapter Plug-In

The real wake agent plugs into `src/agents/coding_agent_adapter.py`. That file is currently empty and must expose a stable adapter before the experiment starts.

Minimum adapter contract:

```text
run_task(
    task,
    repo_checkout,
    visible_prompt,
    admitted_memories,
    model_config,
    budget,
    seed,
) -> trajectory_result
```

The adapter must return or write:

- raw trajectory with task id, session id, condition id, model id, seed, budget, repo commit, tool calls, tool outputs, diffs, memory reads, memory writes, cost, latency, and final outcome
- final patch or no-op marker
- verification command outputs
- model/tool token counts split into wake, sleep, and judge when applicable

The adapter must not receive oracle fields such as `expected_behavior`, `oracle_check`, hidden memory labels, or table targets. Those are scorer-only data.

## Real Runner

Implement `scripts/run_experiment.py` to load the manifest, instantiate each condition, call the adapter, run read/wake/sleep phases, and write logs to `experiments/results/<run_id>/`.

Expected command shape:

```bash
python3 scripts/run_experiment.py \
  --run-id <run_id> \
  --manifest experiments/data/tasks/manifest.json \
  --config experiments/configs/<frozen_config>.json \
  --conditions B0,B1,B2,B3,B4,B5,B6,B7,DF,A0,A2,A4,A5,A6,A11 \
  --split test
```

Required output files:

```text
experiments/results/<run_id>/config.json
experiments/results/<run_id>/conditions.json
experiments/results/<run_id>/task_records.jsonl
experiments/results/<run_id>/trajectories/<condition>/<sequence>/<task>.json
experiments/results/<run_id>/memory_snapshots/<condition>/<sequence>/<session>.json
experiments/results/<run_id>/retrieval_decisions.jsonl
experiments/results/<run_id>/sleep_decisions.jsonl
experiments/results/<run_id>/judge_labels.jsonl
experiments/results/<run_id>/metrics_by_condition.json
experiments/results/<run_id>/metrics_by_sequence_type.json
experiments/results/<run_id>/audit_items.jsonl
```

## Metrics

Compute the metrics from `docs/09_metrics_and_ablations.md`:

- Task metrics: `TaskSuccess`, `Pass@1`
- Memory-hygiene metrics: `RepeatedErrorRate`, `StaleMemoryActivationRate`, `HarmfulMemoryRate`, `UsefulMemoryPrecision`, `ProvenanceCompleteness`, `ScopeAccuracy`, `ContradictionRepairAccuracy`, `TransferScore`, `RegressionAfterUpdate`, `MemoryBloat`
- Cost/latency metrics: `TotalTokens`, `TotalLatency`, `CostPerSuccessfulTask`, `SleepCostShare`

Subjective labels must use the same BinEval-style binary question set, judge model, temperature, calibration examples, and aggregation rule across all conditions. Keep a blinded human-audit subset when budget permits.

## Tables 1-4

Implement `scripts/analyze_results.py` to aggregate raw logs into metric JSON, then implement `scripts/generate_tables.py` to emit the LaTeX tables.

Expected command shape:

```bash
python3 scripts/analyze_results.py --run-id <run_id>
python3 scripts/generate_tables.py --run-id <run_id>
```

Expected table outputs:

- Table 1: `paper/tables/table1_main_results.tex` from rows `B0`-`B7` and `DF`; columns include task, hygiene, and cost metrics.
- Table 2: `paper/tables/table2_ablation_results.tex` from `DF` plus ablations; columns isolate operation effects.
- Table 3: `paper/tables/table3_sequence_breakdown.tex` grouped by the five sequence types; includes the primary task metric, primary hygiene metric, and cost metric for each.
- Table 4: `paper/tables/table4_error_repair_audit.tex` from audit items; rows cover contradiction, staleness, harmful retrieval, repeated error, human-feedback overapplication, and replay-derived memory cases.

Every table must include at least one task metric, one memory-hygiene metric, and one cost or latency metric. Never paste table values by hand.

## Invalid-Comparison Controls

- Matched base model: same wake model, version, endpoint, context limit, temperature, and decoding params within a comparison table.
- Matched budgets: same wake-token, read-memory-token, tool-call, retry, wall-clock, and stopping budgets.
- Matched steps: same task order, repository checkout rule, setup commands, verification commands, and sandbox policy.
- Matched seeds: same seed schedule for all conditions.
- Isolated memory: one namespace per `run_id`, model, seed, condition, and repository.
- Same judge protocol: same binary questions, judge model, temperature, examples, and human-audit sampling rule.
- No hidden tuning: tune prompts, thresholds, weights, and sleep schedule only on the development split.
- Cost-matched episodic control: run `B1`/`A0` under both the same wake budget and the same total budget as `DF`; any sleep tokens used by `DF` must be matched by an episodic/raw-history or prompt-regeneration spend in the control frontier.
- Baseline fidelity: pin version, commit, embedding model, storage backend, update schedule, and unsupported features for `B5`, `B6`, and `B7`.
- Oracle separation: the wake agent and retrieval path never see hidden oracle labels, expected behavior text, or scoring labels.

## Run Checklist

1. Confirm the paper direction and fallback rule.
2. Choose model(s), judge model, budget, seed count, and human-audit size.
3. Pin every baseline implementation and record unsupported features.
4. Freeze tasks, split, repository commits, verification commands, prompts, labels, and budget config.
5. Implement and test `src/agents/coding_agent_adapter.py` and `src/benchmarks/swe_task_interface.py`.
6. Implement `scripts/run_experiment.py`, `scripts/analyze_results.py`, and `scripts/generate_tables.py`.
7. Run stdlib tests and the synthetic smoke harness.
8. Run one development split dry run; tune only on development data.
9. Lock configs and run the held-out matrix.
10. Run judge labeling and human audit.
11. Generate Tables 1-4 and audit artifacts from logs.
12. Replace paper placeholders only with generated table/figure outputs.
13. Archive the run config, logs, table outputs, and exact paper commit used for reporting.
