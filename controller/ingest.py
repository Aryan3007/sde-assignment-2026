"""Event ingestion: chunks -> aggregates, idempotently.

Each chunk is processed in one SQLite transaction that also records the chunk as done. A crash
mid-chunk rolls back, and the chunk is re-read on restart. A crash after commit never re-reads it.
`event_id` is the primary key of `calls`, as a second guard against double counting.

Aggregates (all keyed by the 16-bit feature code, so any cohort can be evaluated later):
  agg_hour   (wf, day, hour, code, caller_id)              -> dials, connected, engaged, qa flags
  agg_var    (wf, day, 3h-block, served cohort, code, welcome, voice) -> dials, connected, engaged
  agg_prompt (wf, day, hour, prompt)                       -> dials, connected, engaged, qa flags
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from array import array
from collections import defaultdict

from .client import Sim
from .cohorts import UNKNOWN, Vocab
from .store import Store

log = logging.getLogger("ctrl.ingest")
D0 = dt.date(2027, 1, 4)  # documented: D0 00:00 = 2027-01-04T00:00+05:30


class LeadIndex:
    """Per-workflow lead_idx -> feature code / campaign day / concluded. Rebuilt from SQLite on restart."""

    def __init__(self):
        self.code = array("H")
        self.camp_day = array("h")
        self.done = bytearray()

    def ensure(self, n: int):
        if n > len(self.code):
            k = n - len(self.code) + 4096
            self.code.extend([UNKNOWN] * k)
            self.camp_day.extend([-32000] * k)
            self.done.extend(b"\x00" * k)


def ts_to_minute(ts: str) -> int:
    """'2027-01-04T10:07:00+05:30' -> sim minute (D0 00:00 = 0)."""
    d = (dt.date.fromisoformat(ts[:10]) - D0).days
    return d * 1440 + int(ts[11:13]) * 60 + int(ts[14:16])


def lead_idx(lead_id: str) -> int:
    return int(lead_id[lead_id.rindex(":") + 2:])


class Ingestor:
    def __init__(self, store: Store, sim: Sim):
        self.store, self.sim = store, sim
        self.vocab = Vocab(store.meta("vocab", {}))
        self.leads: dict[str, LeadIndex] = defaultdict(LeadIndex)
        self.store_calls = os.environ.get("CTRL_STORE_CALLS", "1") == "1"
        self._day_cache: dict[str, int] = {}
        self._load_leads()

    def _load_leads(self):
        t0 = time.time()
        n = 0
        for wf, idx, code, cd in self.store.db.execute("SELECT wf, idx, code, camp_day FROM leads"):
            li = self.leads[wf]
            li.ensure(idx + 1)
            li.code[idx] = code
            li.camp_day[idx] = cd
            n += 1
        for wf, idx in self.store.db.execute("SELECT wf, idx FROM lead_done"):
            li = self.leads[wf]
            li.ensure(idx + 1)
            li.done[idx] = 1
        if n:
            log.info("restored %d leads from the store in %.1fs", n, time.time() - t0)

    # ------------------------------------------------------------ time
    def minute_of(self, ts: str) -> int:
        d = self._day_cache.get(ts[:10])
        if d is None:
            d = (dt.date.fromisoformat(ts[:10]) - D0).days
            self._day_cache[ts[:10]] = d
        return d * 1440 + int(ts[11:13]) * 60 + int(ts[14:16])

    # ------------------------------------------------------------ chunks
    def pending_chunks(self) -> list[dict]:
        done = self.store.one("SELECT MAX(chunk) FROM chunks_done")[0]
        after = -1 if done is None else done
        out = self.sim.chunks("history", after) + self.sim.chunks("events", after)
        return sorted(out, key=lambda c: c["chunk"])

    def ingest_all(self) -> dict:
        stats = {"chunks": 0, "events": 0, "seconds": 0.0}
        for ch in self.pending_chunks():
            t0 = time.time()
            n = self.ingest_chunk(ch)
            stats["chunks"] += 1
            stats["events"] += n
            stats["seconds"] += time.time() - t0
        return stats

    def ingest_chunk(self, ch: dict) -> int:
        t0 = time.time()
        text = self.sim.chunk_text(ch["chunk"])
        hour_agg: dict = defaultdict(lambda: [0, 0, 0, 0])
        var_agg: dict = defaultdict(lambda: [0, 0, 0])
        prm_agg: dict = defaultdict(lambda: [0, 0, 0, 0])
        new_leads: list[tuple] = []
        done_rows: list[tuple] = []
        call_rows: list[tuple] = []
        cfg_rows: list[tuple] = []
        n = 0
        enc = self.vocab.encode
        minute_of = self.minute_of
        leads = self.leads
        for line in text.splitlines():
            if not line:
                continue
            ev = json.loads(line)
            n += 1
            t = ev["type"]
            if t == "call":
                wf = ev["workflow_id"]
                li = leads[wf]
                try:
                    idx = lead_idx(ev["lead_id"])
                    code = li.code[idx] if idx < len(li.code) else UNKNOWN
                except (ValueError, IndexError):
                    idx, code = -1, UNKNOWN
                m = minute_of(ev["ts"])
                day, hour = divmod(m, 1440)
                hour //= 60
                v = ev["variant"]
                c, e = ev["connected"], ev["engaged"]
                qa = ev.get("qa") or {}
                fl = 1 if qa.get("hallucination_flag") else 0
                a = hour_agg[(wf, day, hour, code, v["caller_id"])]
                a[0] += 1; a[1] += c; a[2] += e; a[3] += fl
                b = var_agg[(wf, day, hour // 3, ev.get("cohort_id") or "default", code,
                             v["welcome_message"], v["voice"])]
                b[0] += 1; b[1] += c; b[2] += e
                p = prm_agg[(wf, day, hour, v["prompt_version"])]
                p[0] += 1; p[1] += c; p[2] += e; p[3] += fl
                if e and idx >= 0:
                    li.ensure(idx + 1)
                    if not li.done[idx]:
                        li.done[idx] = 1
                        done_rows.append((wf, idx))
                if self.store_calls and day >= 0:
                    call_rows.append((ev["event_id"], wf, idx, m, ev.get("config_version"), ev.get("cohort_id"),
                                      v["welcome_message"], v["voice"], v["caller_id"], v["prompt_version"],
                                      int(c), int(e), fl))
            elif t == "lead":
                wf = ev["workflow_id"]
                idx = lead_idx(ev["lead_id"])
                code = enc(ev["features"])
                cd = minute_of(ev["ts"]) // 1440
                li = leads[wf]
                li.ensure(idx + 1)
                li.code[idx] = code
                li.camp_day[idx] = cd
                new_leads.append((wf, idx, code, cd))
            elif t == "config_changed":
                cfg_rows.append((ev["workflow_id"], ev["version"], ev["actor"], minute_of(ev["ts"]),
                                 json.dumps(ev.get("changed_dims") or []), ev.get("proposal_id"),
                                 ev.get("approval_id")))
            # campaign_created / approval_decided: approvals are reconciled from GET /approvals,
            # which is the source of truth; nothing to aggregate.
        with self.store.tx() as db:
            db.executemany(
                "INSERT INTO agg_hour VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                "dials=dials+excluded.dials, conn=conn+excluded.conn, eng=eng+excluded.eng, flags=flags+excluded.flags",
                [(*k, *v) for k, v in hour_agg.items()])
            db.executemany(
                "INSERT INTO agg_var VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                "dials=dials+excluded.dials, conn=conn+excluded.conn, eng=eng+excluded.eng",
                [(*k, *v) for k, v in var_agg.items()])
            db.executemany(
                "INSERT INTO agg_prompt VALUES(?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                "dials=dials+excluded.dials, conn=conn+excluded.conn, eng=eng+excluded.eng, flags=flags+excluded.flags",
                [(*k, *v) for k, v in prm_agg.items()])
            db.executemany("INSERT OR IGNORE INTO leads VALUES(?,?,?,?)", new_leads)
            db.executemany("INSERT OR IGNORE INTO lead_done VALUES(?,?)", done_rows)
            if call_rows:
                db.executemany("INSERT OR IGNORE INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", call_rows)
            for r in cfg_rows:
                db.execute("INSERT INTO config_versions(wf,version,actor,applied_minute,changed_dims,proposal_id,"
                           "approval_id) VALUES(?,?,?,?,?,?,?) ON CONFLICT(wf,version) DO NOTHING", r)
            if self.vocab.dirty:
                db.execute("INSERT INTO meta(key,value) VALUES('vocab',?) ON CONFLICT(key) DO UPDATE "
                           "SET value=excluded.value", (json.dumps(self.vocab.to_json()),))
                self.vocab.dirty = False
            db.execute("INSERT INTO chunks_done VALUES(?,?,?,?,?,?,?,?)",
                       (ch["chunk"], ch["kind"], n, ch.get("first_event_id"), ch.get("last_event_id"),
                        ch.get("until_minute"), time.time() - t0, time.time()))
        return n

    # ------------------------------------------------------------ derived views
    def active_lead_codes(self, wf: str, today: int, cadence_days: int) -> dict[int, int]:
        """code -> number of leads still being worked (campaign inside its cadence, not concluded)."""
        li = self.leads.get(wf)
        if li is None:
            return {}
        lo = today - cadence_days + 1
        out: dict[int, int] = defaultdict(int)
        cd, code, done = li.camp_day, li.code, li.done
        for i in range(len(cd)):
            d = cd[i]
            if d >= lo and d <= today and not done[i]:
                out[code[i]] += 1
        return out
