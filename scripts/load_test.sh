#!/usr/bin/env bash
# Run public_03_load end to end and write reports/public_03_load.md (grade) + reports/load_test.md (numbers).
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=public_03_load; PORT="${1:-8701}"; PY=.venv/bin/python
mkdir -p logs state reports
[ -d "runs/base_$NAME" ] || $PY -m sim baseline --scenario "scenarios/$NAME.yaml" --run-dir "runs/base_$NAME"
rm -f "state/$NAME.db" "state/$NAME.db-wal" "state/$NAME.db-shm"
$PY -m sim serve --scenario "scenarios/$NAME.yaml" --run-dir "runs/mine_$NAME" --port "$PORT" --force > "logs/sim_$NAME.log" 2>&1 &
SIM_PID=$!
trap 'kill $SIM_PID 2>/dev/null || true' EXIT
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; do sleep 1; done
START=$(date +%s)
/usr/bin/time -l env CTRL_DB="state/$NAME.db" ./run.sh "http://127.0.0.1:$PORT" > "logs/ctrl_$NAME.log" 2>&1 || \
  /usr/bin/time -v env CTRL_DB="state/$NAME.db" ./run.sh "http://127.0.0.1:$PORT" > "logs/ctrl_$NAME.log" 2>&1
WALL=$(( $(date +%s) - START ))
$PY scripts/load_report.py "state/$NAME.db" "Total wall clock, controller start to scenario end (7 history days + 5 sim days): ${WALL}s on $(uname -sm), $(sysctl -n hw.ncpu 2>/dev/null || nproc) CPUs." > reports/load_test.md
$PY -m sim grade --run "runs/mine_$NAME" --baseline "runs/base_$NAME" --out "reports/$NAME.md" > /dev/null
cat reports/load_test.md
