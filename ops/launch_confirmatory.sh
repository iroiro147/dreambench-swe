#!/usr/bin/env bash
# Confirmatory ablation-ladder fold (post R1/R2/R3 fix). NO destructive /tmp guard:
# the leak is fixed at source (commit adb2eae — score_agent_diff + run_bench self-clean
# worktrees; validate_trap keeps worktrees only under cleanup_worktrees=False). The old
# guard that deleted live continuation-state repos is deliberately absent.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DREAMBENCH_ROOT="${DREAMBENCH_ROOT:-${DREAMFORGE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
cd "$DREAMBENCH_ROOT"
mkdir -p logs/grid
set -a; . experiments/secrets.env 2>/dev/null || true; set +a
export DREAMBENCH_ROOT
export DREAMFORGE_ROOT="$DREAMBENCH_ROOT"
LOG="logs/grid/CONFIRM-$(date -u +%Y%m%dT%H%M%SZ).log"
SEQUENCE_RECORDS="${DREAMBENCH_SEQUENCE_RECORDS:-experiments/env/sequences_confirmatory_v2.jsonl}"
ln -sf "$(basename "$LOG")" logs/grid/CONFIRM-latest.log
echo "[confirm start $(date -u +%FT%TZ)]" | tee -a "$LOG"

run_cond () {
  local conds="$1" mp="$2" t=0 rc=1
  local max_attempts="${DREAMBENCH_MAX_ATTEMPTS:-100}"
  local retry_delay="${DREAMBENCH_RETRY_DELAY_SECONDS:-30}"
  while [ "$t" -lt "$max_attempts" ]; do t=$((t+1))
    PYTHONPATH=src python3 scripts/run_grid.py --conditions "$conds" --seeds 1,2,3 \
      --group-size 2 --max-parallel "$mp" --judge-model codex-gpt-5.5 \
      --sequence-records "$SEQUENCE_RECORDS" >>"$LOG" 2>&1
    rc=$?; echo "[$conds rc=$rc try=$t $(date -u +%FT%TZ)]" | tee -a "$LOG"
    [ "$rc" -eq 0 ] && return 0
    if [ "$t" -lt "$max_attempts" ]; then
      echo "[resume ${retry_delay}s]" | tee -a "$LOG"; sleep "$retry_delay"
    fi
  done
  echo "[$conds RETRY_EXHAUSTED rc=$rc tries=$t $(date -u +%FT%TZ)]" | tee -a "$LOG"
  return "$rc"
}

# mp=16 hermetic batch: the ablation ladder + the falsifier + the held ablations.
run_cond "DF-hybrid,DF-raw-only,DF-strict-hybrid,DF,B5,A0,A2,A4,A5,A6,A11" 16 || exit $?
# mp=4: real Mem0 baseline at low concurrency (async-indexing fairness).
run_cond "B5-MEM0" 4 || exit $?
echo "[CONFIRM_COMPLETE $(date -u +%FT%TZ)]" | tee -a "$LOG"
