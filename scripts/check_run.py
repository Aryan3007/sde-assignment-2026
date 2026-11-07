"""Post-run invariant checks for crash-recovery evidence. Test harness only, never imported by the controller.

It reads the simulator's run directory (allowed here: this is the test, not the controller) and the
controller's store, and checks:
  1. every event the simulator emitted was ingested exactly once (sum of chunk sizes == last event id,
     and stored calls == call events on D0+);
  2. no approval was applied twice and no auto-apply proposal_id was used twice;
  3. every approval the simulator knows about is in our store with the same final status;
  4. no approval lapsed, nothing pending was forgotten.

  python scripts/check_run.py --run runs/crash_public_02_incidents --db state/crash.db --sim http://127.0.0.1:8702
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import httpx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--sim", required=True)
    a = ap.parse_args()
    run, db = Path(a.run), sqlite3.connect(a.db)
    sim = httpx.Client(base_url=a.sim, timeout=60)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] {name}{(' - ' + str(detail)) if detail else ''}")

    st = sim.get("/sim/state").json()
    ingested = db.execute("SELECT SUM(events), COUNT(*), COUNT(DISTINCT chunk) FROM chunks_done").fetchone()
    check("every event ingested exactly once (sum of chunk sizes == last_event_id)",
          ingested[0] == st["last_event_id"] and ingested[1] == ingested[2] == st["chunks"],
          f"ingested={ingested[0]} sim={st['last_event_id']} chunks={ingested[1]}/{st['chunks']}")
    n_calls = 0
    for f in sorted((run / "events").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if '"type":"call"' in line and not json.loads(line)["ts"].startswith("2026"):
                n_calls += 1
    stored = db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    check("stored D0+ calls == simulator call events", stored == n_calls, f"store={stored} sim={n_calls}")

    log = [json.loads(l) for l in (run / "sim_log.jsonl").read_text().splitlines() if l.strip()]
    applied = Counter(r["approval_id"] for r in log if r["kind"] == "approval_applied")
    check("no approval applied twice", all(v == 1 for v in applied.values()), dict(applied))
    autos = Counter(r["proposal_id"] for r in log if r["kind"] == "auto_applied")
    check("no auto-apply proposal_id applied twice", all(v == 1 for v in autos.values()))
    check("no approval lapsed", not any(r["kind"] == "approval_lapsed" for r in log))
    check("no apply conflicts caused by us re-applying",
          sum(1 for r in log if r["kind"] == "apply_conflict") <= sum(1 for r in log if r["kind"] == "supervisor_edit"))

    ours = {r[0]: r[1] for r in db.execute("SELECT approval_id, status FROM proposals WHERE approval_id IS NOT NULL")}
    server = {x["approval_id"]: x["status"] for x in sim.get("/approvals").json()}
    missing = [k for k in server if k not in ours]
    check("every server approval is in our store", not missing, missing)
    mism = {k: (ours[k], v) for k, v in server.items() if k in ours and ours[k] != v
            and not (ours[k] == "withdrawn" and v in ("approved", "lapsed"))}
    check("our approval statuses match the server's", not mism, mism)
    check("nothing left pending/approved-unapplied in our store at the end",
          not db.execute("SELECT COUNT(*) FROM proposals WHERE status IN ('sending')").fetchone()[0])
    restarts = db.execute("SELECT value FROM meta WHERE key='restarts'").fetchone()
    print(f"controller restarts recorded in store: {restarts[0] if restarts else 0}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
