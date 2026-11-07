"""SQLite store: the controller's memory, its audit trail and its crash-recovery point.

One file, WAL mode, one writer (this process). Why SQLite rather than the Postgres SRE already
runs: see SUBMISSION.md section 12. The schema maps 1:1 onto Postgres if we move.

Durability rules:
- An event chunk's aggregates and the ingest cursor are committed in the SAME transaction,
  so a crash can never double-count or skip a chunk.
- A proposal row is written ('sending') BEFORE the HTTP call that creates it. After a crash,
  governance.reconcile() reads the simulator back to learn whether the call landed.
- Module state (what we learned) is written as JSON after every change.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks_done (
  chunk INTEGER PRIMARY KEY, kind TEXT, events INTEGER, first_event_id INTEGER, last_event_id INTEGER,
  until_minute INTEGER, wall_s REAL, ingested_wall REAL);
CREATE TABLE IF NOT EXISTS workflows (wf TEXT PRIMARY KEY, spec TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leads (
  wf TEXT, idx INTEGER, code INTEGER, camp_day INTEGER, PRIMARY KEY (wf, idx)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS lead_done (wf TEXT, idx INTEGER, PRIMARY KEY (wf, idx)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS agg_hour (
  wf TEXT, day INTEGER, hour INTEGER, code INTEGER, did TEXT,
  dials INTEGER, conn INTEGER, eng INTEGER, flags INTEGER,
  PRIMARY KEY (wf, day, hour, code, did)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS agg_var (
  wf TEXT, day INTEGER, blk INTEGER, cohort TEXT, code INTEGER, wm TEXT, voice TEXT,
  dials INTEGER, conn INTEGER, eng INTEGER,
  PRIMARY KEY (wf, day, blk, cohort, code, wm, voice)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS agg_prompt (
  wf TEXT, day INTEGER, hour INTEGER, prompt TEXT, dials INTEGER, conn INTEGER, eng INTEGER, flags INTEGER,
  PRIMARY KEY (wf, day, hour, prompt)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS calls (
  event_id INTEGER PRIMARY KEY, wf TEXT, lead INTEGER, minute INTEGER, ver INTEGER, cohort TEXT,
  wm TEXT, voice TEXT, did TEXT, prompt TEXT, conn INTEGER, eng INTEGER, flag INTEGER);
CREATE TABLE IF NOT EXISTS config_versions (
  wf TEXT, version INTEGER, actor TEXT, applied_minute INTEGER, changed_dims TEXT,
  proposal_id TEXT, approval_id TEXT, config TEXT, PRIMARY KEY (wf, version));
CREATE TABLE IF NOT EXISTS proposals (
  proposal_id TEXT PRIMARY KEY, wf TEXT, kind TEXT, route TEXT, status TEXT, priority TEXT,
  created_minute INTEGER, base_version INTEGER, config TEXT, request TEXT, intents TEXT,
  approval_id TEXT, decided_minute INTEGER, apply_by TEXT, reasons TEXT, applied_version INTEGER,
  last_error TEXT, updated_minute INTEGER);
CREATE INDEX IF NOT EXISTS proposals_wf ON proposals (wf, status);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, minute INTEGER, wall REAL, wf TEXT, kind TEXT, summary TEXT,
  detail TEXT, proposal_id TEXT, approval_id TEXT, version INTEGER);
CREATE INDEX IF NOT EXISTS decisions_wf ON decisions (wf, minute);
CREATE TABLE IF NOT EXISTS module_state (wf TEXT, module TEXT, state TEXT, PRIMARY KEY (wf, module));
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, wf TEXT, kind TEXT, status TEXT, opened_minute INTEGER, closed_minute INTEGER,
  detail TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, minute INTEGER, wf TEXT, severity TEXT, key TEXT, message TEXT,
  runbook TEXT, resolved_minute INTEGER);
CREATE INDEX IF NOT EXISTS alerts_key ON alerts (key, resolved_minute);
CREATE TABLE IF NOT EXISTS metrics (minute INTEGER PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS validation_runs (
  id TEXT PRIMARY KEY, wf TEXT, prompt TEXT, n INTEGER, pass_rate REAL, minute INTEGER, raw TEXT);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA cache_size=-200000")
        self.db.executescript(SCHEMA)
        self.now_minute: int = int(self.meta("sim_minute", -10**9))

    # ------------------------------------------------------------ helpers
    def q(self, sql: str, args=()) -> list[tuple]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        r = self.db.execute(sql, args).fetchone()
        return r

    def tx(self):
        return _Tx(self.db)

    def meta(self, key: str, default=None):
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(r[0]) if r else default

    def set_meta(self, key: str, value):
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)))

    # ------------------------------------------------------------ module state
    def load_state(self, wf: str, module: str, default=None):
        r = self.one("SELECT state FROM module_state WHERE wf=? AND module=?", (wf, module))
        return json.loads(r[0]) if r else default

    def save_state(self, wf: str, module: str, state):
        self.db.execute("INSERT INTO module_state(wf,module,state) VALUES(?,?,?) "
                        "ON CONFLICT(wf,module) DO UPDATE SET state=excluded.state",
                        (wf, module, json.dumps(state, sort_keys=True)))

    # ------------------------------------------------------------ audit
    def decide(self, wf: str | None, kind: str, summary: str, detail: dict | None = None, *,
               proposal_id: str | None = None, approval_id: str | None = None, version: int | None = None):
        self.db.execute("INSERT INTO decisions(minute,wall,wf,kind,summary,detail,proposal_id,approval_id,version) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (self.now_minute, time.time(), wf, kind, summary, json.dumps(detail or {}, default=str),
                         proposal_id, approval_id, version))

    def alert(self, wf: str | None, severity: str, key: str, message: str, runbook: str):
        """Open an alert unless one with the same key is already open (dedup = one line, not fifty)."""
        if self.one("SELECT 1 FROM alerts WHERE key=? AND resolved_minute IS NULL", (key,)):
            return False
        self.db.execute("INSERT INTO alerts(minute,wf,severity,key,message,runbook) VALUES(?,?,?,?,?,?)",
                        (self.now_minute, wf, severity, key, message, runbook))
        return True

    def resolve_alert(self, key: str):
        self.db.execute("UPDATE alerts SET resolved_minute=? WHERE key=? AND resolved_minute IS NULL",
                        (self.now_minute, key))


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, et, ev, tb):
        self.db.execute("COMMIT" if et is None else "ROLLBACK")
        return False
