#!/usr/bin/env bash
# Starts the controller against a running simulator and runs until the scenario finishes.
#   ./run.sh http://127.0.0.1:8700
# No Docker needed: Python 3.11+ with the packages in requirements.txt (the controller itself only
# needs httpx + the standard library; SQLite is built in). State goes to state/<scenario>.db, or
# $CTRL_DB if set. Re-running after a crash resumes from that store.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
PY=python3
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif ! python3 -c "import httpx" 2>/dev/null; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && PY=.venv/bin/python
fi
exec "$PY" -m controller run --sim "$SIM_URL" ${CTRL_DB:+--db "$CTRL_DB"}
