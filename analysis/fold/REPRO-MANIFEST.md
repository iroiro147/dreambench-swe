# REPRODUCTION MANIFEST

Generated at UTC: `2026-07-03T04:20:37Z`

Scope: seed-1 DreamForge/DreamBench-SWE folded paper artifacts under `analysis/fold/`.

## Pinned Facts

| fact | value | source |
|---|---|---|
| Wake model | `gpt-5.5` | `src/experiments/run_bench.py` default `--model`; gate reports store `model: gpt-5.5` |
| Judge CLI selector | `codex-gpt-5.5` | `logs/DAY_LOG.md` and `logs/grid/*-s1-g*.log` commands |
| Judge model inside CodexJudgeClient | `gpt-5.5` | `src/experiments/run_bench.py` maps `codex-gpt-5.5` to `gpt-5.5`; DF result `judge.model` is `gpt-5.5` |
| Codex CLI | `@openai/codex@0.142.0` | `scripts/Dockerfile.codex-agent` |
| Docker server version | `29.4.0` from `analysis/fold/CANARY-PROOF.txt`; current `docker version --format '{{.Server.Version}}'` is denied in this shell | canary proof plus command run |
| OrbStack access | Docker context is `orbstack` from `docker version --format '{{json .}}'` client output; server is inaccessible in this shell | command run |
| Agent image digest | `sha256:200a3c89ccc5538d6bdb33ed23599bb4c74c89bef58b0132fd606830596e13f2` | captured from unsandboxed Bash 2026-07-03 (codex shell is Docker-socket-denied) |
| Node base digest | `node@sha256:813a7480f28fdadac1f7f5c824bcdad435b5bc1322a5968bbbdef8d058f9dff4` | captured from unsandboxed Bash 2026-07-03 (codex shell is Docker-socket-denied) |
| Run seed actually used | `1` | result manifests and `logs/grid/*-s1-g*.log` commands |
| `run_bench` default seed | `1729` | `src/experiments/run_bench.py:46`; overridden by `run_grid --seeds 1` |

Docker command outputs from this shell:

```text
$ docker image inspect dreambench-codex-agent:latest --format '{{.Id}}'
permission denied while trying to connect to the docker API at unix://<USER_HOME>/.orbstack/run/docker.sock

$ docker image inspect node:22-slim --format '{{index .RepoDigests 0}}'
permission denied while trying to connect to the docker API at unix://<USER_HOME>/.orbstack/run/docker.sock

$ docker version --format '{{.Server.Version}}'
permission denied while trying to connect to the docker API at unix://<USER_HOME>/.orbstack/run/docker.sock
```

## Reproduction Commands

Build the Codex container image:

```bash
docker build -t dreambench-codex-agent:latest -f scripts/Dockerfile.codex-agent .
```

Run the seed-1 grid. This is the final full-condition command recorded in `logs/DAY_LOG.md`; individual `run_bench` child commands are recorded in `logs/grid/*-s1-g*.log`.

```bash
PYTHONPATH=src python3 scripts/run_grid.py \
  --conditions B0,B1,B2,B3,B4,B5,B6,B7,DF,A0,A2,A4,A5,A6,A11 \
  --seeds 1 \
  --group-size 2 \
  --max-parallel 6 \
  --judge-model codex-gpt-5.5 \
  --sequence-records experiments/env/sequences.jsonl
```

Fold the result records:

```bash
python3 scripts/barrier2_rescore.py
```

Run sequence-level statistics:

```bash
python3 scripts/stats_analysis.py
```

Run cost and hygiene-independence analysis:

```bash
python3 scripts/cost_analysis.py
```

Emit provenance:

```bash
python3 scripts/emit_provenance.py
```

No-write verification fold used for this manifest:

```bash
BARRIER2_NO_WRITE=1 python3 scripts/barrier2_rescore.py
```

## Clean Paper-Reproduction Test Command

This command is intended to pass on a clean checkout without archived historical result directories. Fixture-dependent historical contamination tests now skip only when their copied fixture files are absent.

```bash
python3 -m pytest \
  tests/test_barrier2_rescore.py \
  tests/test_contamination_scan.py \
  tests/test_codex_isolation.py \
  tests/test_codex_reaping.py \
  tests/test_run_bench.py \
  tests/test_cost_analysis.py \
  -q -rs
```

Verification in this shell:

```text
31 passed, 3 skipped in 35.17s
SKIPPED [1] tests/test_codex_isolation.py:182: isolation_unavailable: docker daemon unavailable: permission denied while trying to connect to the docker API at unix://<USER_HOME>/.orbstack/run/docker.sock
SKIPPED [1] tests/test_codex_isolation.py:229: set DREAMBENCH_LIVE_CODEX_ACCEPTANCE=1 for live Codex acceptance
SKIPPED [1] tests/test_codex_isolation.py:294: set DREAMBENCH_LIVE_CODEX_ACCEPTANCE=1 for live Codex acceptance
```

## Private Hidden-Material Layout

The scorer secret material is:

```text
<REVIEWER_ONLY_ORACLES>/
<REVIEWER_ONLY_REFSOL>/
experiments/env/sequences.jsonl
```

Under the container isolation protocol, that material is absent from the agent container. Wake containers mount only the task worktree at `/work:rw` and `~/.codex` at `/codex-home:ro`; judge containers mount only `~/.codex` at `/codex-home:ro` and run from `/tmp`.

The hidden material still lives inside the repository tree, so the reproduction guarantee depends on the container filesystem boundary plus the hardened contamination scan. Moving scorer secrets outside the repository remains the recommended long-term reproducibility hardening step.
