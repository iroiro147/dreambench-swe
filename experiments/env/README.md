# DreamBench-SWE Executable Pilot Environment

This directory contains a runnable pilot task environment for DreamBench-SWE. It replaces synthetic task descriptions with small real Python repositories, git refs, pytest oracles, and known-good reference patches.

## Layout

- `repos/exprmini` - tiny arithmetic-expression evaluator.
- `repos/configly` - small INI/CSV parser.
- `repos/todolite` - tiny task CLI.
- `tasks.jsonl` - one task per line. Each task has a sequence id/type, session index, repo, git `base_ref`, natural-language instruction, injected memory event, oracle command, expected outcome, reference patch, and taxonomy hint.
- `load_env.py` - stdlib loader API and self-check runner.
- `build_env.py` - deterministic fixture generator.

Each repo is a standalone git repository with a passing `seed` tag, source files, pytest tests, and `CONVENTIONS.md`. Task refs are lightweight git tags such as `tasks/expr-convention/s01/base`. The base refs intentionally fail their pytest oracle. Applying the task's `reference_patch` makes the oracle pass.

## Validate

From the project root:

```bash
python3 experiments/env/load_env.py
```

The self-check clones every `(repo, base_ref)` into a fresh temp directory, confirms `pytest -q` fails, applies `reference_patch`, and confirms `pytest -q` passes.

## Loader API

```python
from experiments.env.load_env import apply_patch, materialize, run_oracle

worktree = materialize("exprmini", "tasks/expr-convention/s01/base")
patch_result = apply_patch(worktree, task["reference_patch"])
oracle_result = run_oracle(worktree, "pytest -q")
```

All APIs use only the Python standard library and the local `git`/`pytest` executables. No network access is required.

## Task Types

The pilot covers five DreamBench-SWE memory stressors:

- `convention-learning` - later sessions rely on project conventions documented in `CONVENTIONS.md`.
- `generated-files` - source changes require generated artifact updates.
- `stale-architecture` - injected memory conflicts with the current architecture.
- `reviewer-preference` - human feedback must be applied without overgeneralizing.
- `flaky-test` - the correct fix is deterministic behavior, not retries or xfail markers.

## Scaling To SWE-bench

The same schema scales to a SWE-bench subset by replacing `repos/<name>` with pinned real project checkouts and replacing lightweight task tags with issue-specific base commits. `reference_patch` can be stored inline for tiny pilots or moved to patch files addressed by hash for larger corpora. The loader contract stays the same: materialize a base ref, run the oracle, apply an agent/reference patch, and rerun the oracle.

For larger repos, add per-task setup metadata only when necessary, keep oracle commands deterministic, and preserve oracle separation: wake agents should see the instruction and base checkout, not `reference_patch`, `expected_pass`, or taxonomy labels.
