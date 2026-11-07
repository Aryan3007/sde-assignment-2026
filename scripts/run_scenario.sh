#!/usr/bin/env bash
# Serve a scenario, run the controller to completion, grade against a fresh baseline.
#   scripts/run_scenario.sh public_01_dev [port]
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:?scenario name, e.g. public_01_dev}"; PORT="${2:-8700}"
PY=.venv/bin/python
mkdir -p runs reports state logs
[ -d "runs/base_$NAME" ] || $PY -m sim baseline --scenario "scenarios/$NAME.yaml" --run-dir "runs/base_$NAME"
rm -f "state/$NAME.db" "state/$NAME.db-wal" "state/$NAME.db-shm"
$PY -m sim serve --scenario "scenarios/$NAME.yaml" --run-dir "runs/mine_$NAME" --port "$PORT" --force > "logs/sim_$NAME.log" 2>&1 &
SIM_PID=$!
trap 'kill $SIM_PID 2>/dev/null || true' EXIT
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; do sleep 0.5; done
START=$(date +%s)
CTRL_DB="state/$NAME.db" ./run.sh "http://127.0.0.1:$PORT" 2>&1 | tee "logs/ctrl_$NAME.log"
echo "controller wall time: $(( $(date +%s) - START ))s" | tee -a "logs/ctrl_$NAME.log"
$PY -m sim grade --run "runs/mine_$NAME" --baseline "runs/base_$NAME" --out "reports/$NAME.md" > /dev/null
head -12 "reports/$NAME.md"
