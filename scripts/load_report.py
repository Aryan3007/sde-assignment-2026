"""Summarise the controller's own metrics table for a run into markdown (ingest rate, decision lag, wall per day).

  .venv/bin/python scripts/load_report.py state/public_03_load.db "<extra header line>"
"""
import json
import os
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
rows = [(m, json.loads(d)) for m, d in db.execute("SELECT minute, data FROM metrics ORDER BY minute")]
by, prev = {}, None
for m, r in rows:
    d = m // 1440
    b = by.setdefault(d, {"ticks": 0, "events": 0, "ingest_s": 0.0, "decide_s": 0.0, "wall_s": 0.0, "max_lag_s": 0.0})
    b["ticks"] += 1
    b["events"] += r["events_ingested"]
    b["ingest_s"] += r["ingest_s"]
    b["decide_s"] += r["decide_s"]
    b["max_lag_s"] = max(b["max_lag_s"], r["decision_lag_s"])
    if prev:
        b["wall_s"] += r["wall"] - prev
    prev = r["wall"]
print("# Load test: public_03_load (12 workflows)\n")
if len(sys.argv) > 2:
    print(sys.argv[2] + "\n")
print("Measured from the controller's own `metrics` table (one row per tick). `wall` includes the simulator's "
      "own time to generate the day (it runs on the same machine), so it is an upper bound on controller time.\n")
print("| sim day | ticks | events ingested | ingest s | ingest ev/s | decide s | wall s (incl. simulator) | max decision lag s |")
print("|---|---|---|---|---|---|---|---|")
for d, b in sorted(by.items()):
    label = "history+D0 start" if d == 0 and b["events"] > 2_000_000 else f"D{d}"
    eps = b["events"] / b["ingest_s"] if b["ingest_s"] else 0
    print(f"| {label} | {b['ticks']} | {b['events']:,} | {b['ingest_s']:.1f} | {eps:,.0f} | {b['decide_s']:.1f} | "
          f"{b['wall_s']:.1f} | {b['max_lag_s']:.2f} |")
last = rows[-1][1]
print(f"\nPeak RSS (controller): {last['rss_mb']} MB. Store size: {os.path.getsize(sys.argv[1]) / 1e6:.0f} MB "
      f"({db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]:,} raw call rows kept for the audit trail). "
      f"Simulator HTTP calls: {last['sim_calls']:,}.")
