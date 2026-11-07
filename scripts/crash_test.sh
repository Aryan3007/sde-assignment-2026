#!/usr/bin/env bash
# Crash-recovery test: SIGKILL the controller at random moments, restart it, let it finish, then check
# invariants against the simulator (scripts/check_run.py) and grade.
#   scripts/crash_test.sh [scenario] [kills] [port]
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:-public_02_incidents}"; KILLS="${2:-6}"; PORT="${3:-8702}"
PY=.venv/bin/python
RUN="runs/crash_$NAME"; DB="state/crash_$NAME.db"; LOG="logs/crash_$NAME.log"
mkdir -p logs state reports
rm -f "$DB" "$DB-wal" "$DB-shm"
[ -d "runs/base_$NAME" ] || $PY -m sim baseline --scenario "scenarios/$NAME.yaml" --run-dir "runs/base_$NAME"
$PY -m sim serve --scenario "scenarios/$NAME.yaml" --run-dir "$RUN" --port "$PORT" --force > "logs/sim_crash_$NAME.log" 2>&1 &
SIM_PID=$!
trap 'kill $SIM_PID 2>/dev/null || true' EXIT
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; do sleep 0.5; done
: > "$LOG"
for i in $(seq 1 "$KILLS"); do
  CTRL_DB="$DB" ./run.sh "http://127.0.0.1:$PORT" >> "$LOG" 2>&1 &
  CPID=$!
  # kill somewhere between 1.0 and 4.0 seconds in: lands mid-ingest, mid-decision or mid-HTTP call
  SLEEP=$(awk -v s="$RANDOM" 'BEGIN{srand(s); printf "%.2f", 1.0 + rand()*3.0}')
  sleep "$SLEEP"
  if kill -0 "$CPID" 2>/dev/null; then
    kill -9 "$CPID"; wait "$CPID" 2>/dev/null || true
    MIN=$(curl -s "http://127.0.0.1:$PORT/sim/state" | $PY -c "import json,sys; print(json.load(sys.stdin)['ts'])")
    echo "=== kill #$i: SIGKILL after ${SLEEP}s, simulator at $MIN ===" | tee -a "$LOG"
  else
    echo "=== run finished before kill #$i ===" | tee -a "$LOG"; break
  fi
done
echo "=== final restart, running to completion ===" | tee -a "$LOG"
CTRL_DB="$DB" ./run.sh "http://127.0.0.1:$PORT" >> "$LOG" 2>&1
grep -E "resuming|restored|scenario finished" "$LOG" | tail -5
$PY scripts/check_run.py --run "$RUN" --db "$DB" --sim "http://127.0.0.1:$PORT" | tee "reports/crash_recovery_$NAME.txt"
$PY -m sim grade --run "$RUN" --baseline "runs/base_$NAME" --out "reports/crash_$NAME.md" > /dev/null
sed -n 5,12p "reports/crash_$NAME.md" | tee -a "reports/crash_recovery_$NAME.txt"
