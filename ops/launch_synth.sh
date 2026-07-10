#!/usr/bin/env bash
# DreamBench-SWE-Synth slice launch (8 pre-registered synthesis traps). NO destructive
# /tmp guard: the leak is fixed at source; the old guard that deleted live
# continuation-state repos is deliberately absent.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DREAMBENCH_ROOT="${DREAMBENCH_ROOT:-${DREAMFORGE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
cd "$DREAMBENCH_ROOT"
mkdir -p logs/grid
set -a; . experiments/secrets.env 2>/dev/null || true; set +a
export DREAMBENCH_ROOT
export DREAMFORGE_ROOT="$DREAMBENCH_ROOT"
LOG="logs/grid/SYNTH-$(date -u +%Y%m%dT%H%M%SZ).log"
ln -sf "$(basename "$LOG")" logs/grid/SYNTH-latest.log
echo "[synth start $(date -u +%FT%TZ)]" | tee -a "$LOG"

run_cond () {
  local conds="$1" mp="$2" t=0 rc=1
  local max_attempts="${DREAMBENCH_MAX_ATTEMPTS:-100}"
  local retry_delay="${DREAMBENCH_RETRY_DELAY_SECONDS:-30}"
  while [ "$t" -lt "$max_attempts" ]; do t=$((t+1))
    PYTHONPATH=src python3 scripts/run_grid.py --conditions "$conds" --seeds 1,2,3 \
      --group-size 2 --max-parallel "$mp" --judge-model codex-gpt-5.5 \
      --sequence-records experiments/env/sequences_synth.jsonl >>"$LOG" 2>&1
    rc=$?; echo "[$conds rc=$rc try=$t $(date -u +%FT%TZ)]" | tee -a "$LOG"
    [ "$rc" -eq 0 ] && return 0
    if [ "$t" -lt "$max_attempts" ]; then
      echo "[resume ${retry_delay}s]" | tee -a "$LOG"; sleep "$retry_delay"
    fi
  done
  echo "[$conds RETRY_EXHAUSTED rc=$rc tries=$t $(date -u +%FT%TZ)]" | tee -a "$LOG"
  return "$rc"
}

# mp=16 hermetic synth batch: prereg B0/B1/B3/B5 plus the ablation ladder.
# VALID_CONDITIONS spells the prereg DF-typed-only arm as DF.
run_cond "B0,B1,B3,B5,DF,DF-raw-only,DF-hybrid" 16 || exit $?
# mp=4: real Mem0 baseline at low concurrency (async-indexing fairness).
run_cond "B5-MEM0" 4 || exit $?
echo "[SYNTH_COMPLETE $(date -u +%FT%TZ)]" | tee -a "$LOG"
