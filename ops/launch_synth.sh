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
  local conds="$1" mp="$2" t=0
  while [ "$t" -lt 100 ]; do t=$((t+1))
    PYTHONPATH=src python3 scripts/run_grid.py --conditions "$conds" --seeds 1,2,3 \
      --group-size 2 --max-parallel "$mp" --judge-model codex-gpt-5.5 \
      --sequence-records experiments/env/sequences_synth.jsonl >>"$LOG" 2>&1
    rc=$?; echo "[$conds rc=$rc try=$t $(date -u +%FT%TZ)]" | tee -a "$LOG"
    [ "$rc" -eq 0 ] && break
    echo "[resume 30s]" | tee -a "$LOG"; sleep 30
  done
}

# mp=16 hermetic synth batch: prereg B0/B1/B3/B5 plus the ablation ladder.
# VALID_CONDITIONS spells the prereg DF-typed-only arm as DF.
run_cond "B0,B1,B3,B5,DF,DF-raw-only,DF-hybrid" 16
# mp=4: real Mem0 baseline at low concurrency (async-indexing fairness).
run_cond "B5-MEM0" 4
echo "[SYNTH_COMPLETE $(date -u +%FT%TZ)]" | tee -a "$LOG"
