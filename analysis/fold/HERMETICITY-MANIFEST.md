# HERMETICITY MANIFEST

Generated at UTC: `2026-07-03T04:20:37Z`

Scope: filesystem hermeticity for the DreamBench-SWE wake agent and Codex sleep judge used by the seed-1 fold. This document is derived from `src/agents/llm_agents.py`, `scripts/Dockerfile.codex-agent`, `src/experiments/codex_judge_client.py`, `analysis/fold/CANARY-PROOF.txt`, and the no-write `scripts/barrier2_rescore.py` verification run.

## Container Invocation

The real Codex CLI path is wrapped by `_codex_container_invocation(...)` in `src/agents/llm_agents.py`. The command template is:

```text
docker run --rm -i \
  --name dreambench-<purpose>-<pid>-<uuid12> \
  --network bridge \
  --user <uid>:<gid> \
  -e CODEX_HOME=/codexhome \
  -e HOME=/codexhome \
  -e NO_COLOR=1 \
  -v <host ~/.codex>:/codex-home:ro \
  [-v <task worktree>:/work:rw -w /work | -w /tmp] \
  dreambench-codex-agent:latest \
  codex exec ...
```

For wake-agent runs, `worktree` is the materialized task worktree and the only benchmark write mount is:

```text
<task worktree>:/work:rw
```

For Codex sleep-judge runs, `src/experiments/codex_judge_client.py` calls `_codex_container_invocation(..., worktree=None, purpose="judge")`, so no task worktree is mounted and Docker receives:

```text
-w /tmp
```

The fixed mount set is therefore:

```text
<host ~/.codex>:/codex-home:ro
<task worktree>:/work:rw       # wake only
```

The hidden scorer material is not in that mount set. `experiments/env/oracles`, `experiments/env/refsol`, the sequence-record files, the harness repository root, and host home directories are not mounted into the wake or judge containers, so those paths are physically absent from the container filesystem unless future code changes add a mount.

## Boundary Scope

This is filesystem hermeticity with respect to benchmark material. It is not network isolation.

- Docker network mode is `bridge`.
- Network access is required for the Codex CLI model API call.
- The task prompt says "Do not use network calls", but the OS-level boundary does not block all network egress.
- `~/.codex` is mounted read-only at `/codex-home:ro`.
- The Docker entrypoint copies `auth.json`, `config.toml`, `cloud-config-bundle-cache.json`, `version.json`, and `models_cache.json` from `/codex-home` into writable `CODEX_HOME=/codexhome` for the Codex CLI.

The precise claim is: hermetic means filesystem-hermetic with respect to hidden benchmark material, not network-isolated and not independent of Codex CLI auth/config state.

## Image

`scripts/Dockerfile.codex-agent` builds:

```text
FROM node:22-slim
apt-get install --no-install-recommends ca-certificates git
npm install -g @openai/codex@0.142.0
```

Pinned digests (captured from an unsandboxed Bash shell on 2026-07-03; the codex analysis shell is seatbelted from the Docker socket, so these were filled in by the orchestrator):

```text
$ docker image inspect dreambench-codex-agent:latest --format '{{.Id}}'
sha256:200a3c89ccc5538d6bdb33ed23599bb4c74c89bef58b0132fd606830596e13f2

$ docker image inspect node:22-slim --format '{{index .RepoDigests 0}}'
node@sha256:813a7480f28fdadac1f7f5c824bcdad435b5bc1322a5968bbbdef8d058f9dff4

$ docker version --format '{{.Server.Version}}'
29.4.0
```

- Agent image built: `2026-07-02T13:36:31+05:30`.
- Codex CLI pinned in image: `@openai/codex@0.142.0`.
- Docker / OrbStack server: `29.4.0` (matches the canary artifact's `docker=29.4.0` from `Fri Jul 3 09:26:31 IST 2026`).

Reproducibility recommendation: pin the base image as `node:22-slim@sha256:813a7480f28fdadac1f7f5c824bcdad435b5bc1322a5968bbbdef8d058f9dff4` in a future Dockerfile revision. This manifest does not change the Dockerfile itself, because changing it would invalidate the current records' provenance; the digest is recorded here instead.

## Positive Proof

The run-linked proof artifact is `analysis/fold/CANARY-PROOF.txt`.

It records this live acceptance subset:

```text
tests/test_codex_isolation.py::test_live_docker_container_cannot_see_host_repo_canary PASSED
tests/test_codex_isolation.py::test_live_codex_wake_blocks_canary_captures_diff_and_scores PASSED
tests/test_codex_isolation.py::test_live_codex_judge_returns_json_and_blocks_canary PASSED
3 passed in 97.41s
docker=29.4.0 date=Fri Jul  3 09:26:31 IST 2026
```

The hardened no-write BARRIER-2 rescore was run in this shell:

```text
$ BARRIER2_NO_WRITE=1 python3 scripts/barrier2_rescore.py
=== VALIDITY STATUS: SUSPECT_NON_CLEAN contaminated_count=0 ===
=== HERMETICITY PROOF: container_record_scan=enabled canary_proof_exists=true canary_proof_bytes=1456 path=analysis/fold/CANARY-PROOF.txt ===
```

The status is `SUSPECT_NON_CLEAN` because B6 and A11 are partial coverage rows, not because of contamination. The container scan was enabled and reported `contaminated_count=0`.

## Limitation

The hidden scoring assets still live in this repository tree:

```text
<REVIEWER_ONLY_ORACLES>/
<REVIEWER_ONLY_REFSOL>/
experiments/env/sequences.jsonl
experiments/env/sequences_confirmatory_v2.jsonl
```

The current guarantee rests on the container wall and the post-hoc contamination scan. Per the leak-vector recommendation, moving hidden scoring material out of the repository tree remains the stronger long-term layout.
