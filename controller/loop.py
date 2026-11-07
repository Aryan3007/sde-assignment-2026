"""The always-on loop: ingest -> reconcile -> observe -> decide -> govern -> advance.

Order matters for crash safety: everything the simulator already did is ingested and reconciled
BEFORE we decide anything, and time only moves (advance) after every decision is committed. Kill
the process anywhere: on restart the same order resumes from the store.
"""
from __future__ import annotations

import json
import logging
import os
import resource
import shutil
import time
from pathlib import Path

from . import settings as S
from .callerid import CallerId
from .client import Sim
from .configlib import clean
from .context import Ctx
from .experiments import Experiments
from .governance import Governance
from .incidents import Incidents
from .ingest import Ingestor
from .schedule import Schedule
from .stats import stratified_diff
from .store import Store

log = logging.getLogger("ctrl.loop")


def open_store(sim: Sim, db_path: str | None) -> Store:
    st = sim.state()
    path = Path(db_path or f"state/{st['scenario']}.db")
    if path.exists():
        store = Store(path)
        if not _same_world(store, sim, st):
            store.db.close()
            dest = path.with_suffix(f".stale-{int(time.time())}.db")
            shutil.move(str(path), dest)
            for ext in ("-wal", "-shm"):
                p = Path(str(path) + ext)
                if p.exists():
                    p.unlink()
            log.warning("store %s belongs to a different simulator world; moved to %s and starting fresh", path, dest)
            return Store(path)
        log.info("resuming from %s (last seen sim minute %s)", path, store.meta("sim_minute"))
        return store
    return Store(path)


def _same_world(store: Store, sim: Sim, st: dict) -> bool:
    if store.meta("scenario") not in (None, st["scenario"]):
        return False
    seen = store.meta("sim_minute")
    if seen is not None and st["sim_minute"] < seen:
        return False
    for wf, ver in store.q("SELECT wf, MAX(version) FROM config_versions GROUP BY wf"):
        try:
            if sim.workflow(wf)["current_version"] < ver:
                return False
        except Exception:
            return False
    last = store.one("SELECT chunk, last_event_id FROM chunks_done ORDER BY chunk DESC LIMIT 1")
    if last and st["last_event_id"] < (last[1] or 0):
        return False
    return True


class Controller:
    def __init__(self, sim_url: str, db_path: str | None = None):
        self.sim = Sim(sim_url)
        self.store = open_store(self.sim, db_path)
        st = self.sim.state()
        self.store.set_meta("scenario", st["scenario"])
        self.wfs = {w["workflow_id"]: w for w in self.sim.workflows()}
        for wid, w in self.wfs.items():
            self.store.db.execute("INSERT OR REPLACE INTO workflows VALUES(?,?)", (wid, json.dumps(w)))
        self.ingest = Ingestor(self.store, self.sim)
        self.gov = Governance(self.store, self.sim, S.preferences())
        self.restarts = self.store.meta("restarts", -1) + 1
        self.store.set_meta("restarts", self.restarts)
        if self.restarts:
            self.store.decide(None, "controller_restart", f"controller restarted (#{self.restarts}); resuming",
                              {"sim_minute": st["sim_minute"]})

    # ------------------------------------------------------------ main loop
    def run(self):
        while True:
            t0 = time.time()
            st = self.sim.state()
            self.store.now_minute = st["sim_minute"]
            self.store.set_meta("sim_minute", st["sim_minute"])
            ing = self.ingest.ingest_all()
            t1 = time.time()
            n_dec0 = self.store.one("SELECT COUNT(*) FROM decisions")[0]
            for wid in self.wfs:
                try:
                    self.step(wid, st)
                except Exception as e:  # one broken workflow must not stop the others
                    log.exception("workflow %s step failed", wid)
                    self.store.alert(wid, "page", f"step_error:{wid}", f"{wid}: controller step failed: {e!r}",
                                     "Controller bug. Logs have the traceback; the workflow is frozen until fixed.")
            t2 = time.time()
            self._metrics(st, ing, t1 - t0, t2 - t1, self.store.one("SELECT COUNT(*) FROM decisions")[0] - n_dec0)
            if st["finished"]:
                self.store.decide(None, "run_finished", f"scenario finished at {st['ts']}", {})
                log.info("scenario finished at %s", st["ts"])
                return
            self.sim.advance(self._next_step(st["sim_minute"]))

    def _next_step(self, now: int) -> int:
        """15 minutes while anything can happen; skip dead night hours in one call."""
        def active(m):
            h = (m % 1440) // 60
            for w in self.wfs.values():
                lo, hi = w["dialling_window"]
                alo, ahi = w["governance"]["approver_hours"]
                if lo <= h < hi or alo <= h < ahi or h == w["daily_batch"]["at_hour"]:
                    return True
            return False
        step = S.TICK_MINUTES
        while step < 12 * 60 and not active(now + step):
            step += S.TICK_MINUTES
        return step

    def step(self, wid: str, st: dict):
        store, sim = self.store, self.sim
        spec = self.wfs[wid]
        cfg = sim.config(wid)
        self.gov.snapshot_config(wid, cfg)
        ctx = Ctx(wf=wid, spec=spec, store=store, vocab=self.ingest.vocab, ingest=self.ingest, now=st["sim_minute"],
                  config=clean(cfg["config"]), version=cfg["version"], credits=st["credits_remaining"], sim=sim)
        self.gov.reconcile(wid)
        mods = {"experiments": Experiments(ctx), "callerid": CallerId(ctx), "schedule": Schedule(ctx),
                "incidents": Incidents(ctx)}
        self._supervisor_edits(ctx, mods)
        if ctx.now >= 0:
            mods["incidents"].observe()
            mods["callerid"].observe()
            mods["experiments"].observe()
            mods["schedule"].observe(mods["experiments"].carveout_cohorts())
            self._monitor(ctx)
        intents = [it for m in mods.values() for it in m.intents()]
        if self.gov.apply_approved(ctx, intents):
            return  # config moved; the next tick plans on the new base
        managed = mods["experiments"].carveout_cohorts() + mods["schedule"].cohorts()
        if ctx.now >= 0:
            self.gov.plan(ctx, intents, managed, self.ingest)

    def _supervisor_edits(self, ctx: Ctx, mods: dict):
        key = f"sup_handled:{ctx.wf}"
        done = self.store.meta(key, 0)
        rows = self.store.q("SELECT version, changed_dims, applied_minute FROM config_versions WHERE wf=? AND "
                            "actor='supervisor' AND version > ? ORDER BY version", (ctx.wf, done))
        for v, cd, at in rows:
            dims = json.loads(cd or "[]")
            self.store.decide(ctx.wf, "supervisor_edit",
                              f"supervisor changed {dims} (v{v}). Respected: no automatic revert; modules on these "
                              f"dims pause for {S.PIN_AFTER_SUPERVISOR_HOURS}h (safety incidents still go to approval).",
                              {"dims": dims, "applied_minute": at}, version=v)
            for m in mods.values():
                if hasattr(m, "on_supervisor_edit"):
                    m.on_supervisor_edit(dims, v)
            self.store.set_meta(key, v)

    def _monitor(self, ctx: Ctx):
        """Workflow KPI drift: last 24h vs the 3 days before, per KPI, never blended. Alerts only."""
        if ctx.now % 60 or not ctx.in_dialling():
            return
        h = ctx.now // 60
        rows = self.store.q("SELECT day*24+hour >= ?, SUM(dials), SUM(conn), SUM(eng) FROM agg_prompt WHERE wf=? "
                            "AND day >= ? AND day*24+hour >= ? GROUP BY 1", (h - 24, ctx.wf, ctx.day - 5, h - 96))
        by = {bool(r[0]): r[1:] for r in rows}
        if True not in by or False not in by:
            return
        (d1, c1, e1), (d0, c0, e0) = by[True], by[False]
        for name, a, b in (("pickup_rate", (d1, c1, d0, c0), None), ("engagement_rate", (c1, e1, c0, e0), None)):
            c = stratified_diff([a])
            key = f"kpi_drift:{ctx.wf}:{name}"
            if c.n_a >= 300 and c.rel <= -0.10 and c.z <= -3:
                self.store.alert(ctx.wf, "ticket", key,
                                 f"{ctx.wf}: {name} last 24h {c.p_a:.1%} vs {c.p_b:.1%} the 3 days before ({c.rel:+.0%})",
                                 "Open the supervisor view: was there a supervisor edit, caller-ID quarantine or "
                                 "prompt change in the window? The decisions table has the timeline.")
            elif c.rel > -0.05:
                self.store.resolve_alert(key)

    def _metrics(self, st, ing, ingest_s, decide_s, n_dec):
        last = self.store.one("SELECT MAX(last_event_id) FROM chunks_done")[0] or 0
        m = {"ts": st["ts"], "events_ingested": ing["events"], "chunks": ing["chunks"],
             "ingest_s": round(ingest_s, 3), "ingest_eps": round(ing["events"] / ing["seconds"], 0) if ing["seconds"] else None,
             "decide_s": round(decide_s, 3), "decision_lag_s": round(ingest_s + decide_s, 3),
             "event_lag": st["last_event_id"] - last, "decisions": n_dec, "credits_remaining": st["credits_remaining"],
             "open_alerts": self.store.one("SELECT COUNT(*) FROM alerts WHERE resolved_minute IS NULL")[0],
             "open_proposals": self.store.one("SELECT COUNT(*) FROM proposals WHERE status IN ('sending','pending','approved')")[0],
             "rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 if os.uname().sysname == "Linux" else 1024) / 1024, 1),
             "sim_calls": self.sim.calls, "wall": time.time()}
        self.store.db.execute("INSERT OR REPLACE INTO metrics VALUES(?,?)", (st["sim_minute"], json.dumps(m)))
        if m["event_lag"] > 0:
            self.store.alert(None, "page", "ingest_lag", f"ingest is {m['event_lag']} events behind the simulator",
                             "Check controller logs for chunk fetch errors; the loop will not decide on stale data.")
        else:
            self.store.resolve_alert("ingest_lag")
        if st["credits_remaining"] < S.CREDIT_RESERVE:
            self.store.alert(None, "page", "credits_low", f"credits_remaining={st['credits_remaining']}",
                             "Incident response may stall. Raise the budget or freeze LLM use.")
        if st["sim_minute"] % 1440 == 0 or ing["events"] > 100000:
            log.info("%s ingested=%d (%.0f ev/s) decide=%.2fs credits=%s rss=%sMB", st["ts"], ing["events"],
                     m["ingest_eps"] or 0, decide_s, st["credits_remaining"], m["rss_mb"])
