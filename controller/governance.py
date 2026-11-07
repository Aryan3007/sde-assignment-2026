"""The governance client: the only code that changes a workflow's config.

Proposal lifecycle (ours on top of the simulator's):

    sending --(POST ok)--> pending --> approved --(apply ok)--> applied
       |                      |           |--(409 stale)--> stale     -> re-planned with fresh data
       |                      |           |--(410)--------> lapsed    -> re-planned
       |                      |           '--(withdrawn)--> withdrawn -> a safety decision overtook it
       |                      |--> rejected  -> cooldown, then re-planned
       |                      '--> expired   -> treated as a rejection (stakeholders/05), re-planned
       '--(crash / unknown)--> reconciled from GET /approvals by proposal_id
    auto: sending --(PUT ok)--> applied | --(403)--> refused (dims routed to approval next time)

Routing rules:
- Auto-apply only when ALL hold: the policy permits every changed dim and the shift; the module
  allows it; no supervisor touched the dim in the last 72h; and for customer-facing dims
  (welcome/voice) the move only shrinks exposure (no new arm, only the safe arm gains share).
  Risk-increasing customer-facing moves (start a test, promote) always go to a human.
- At most one approval request in flight per workflow (a second would go stale on apply), except
  an incident fix, which may go while a routine request is pending.
- Request budget: min(policy max_requests_per_24h, client preference). One slot is reserved
  for incidents and rollbacks.
- Send only inside approver hours (plus client preference), and only if a median-latency decision
  still lands inside today's approver hours (routine) or before expiry (urgent).
"""
from __future__ import annotations

import copy
import json
import logging

from . import settings as S
from .client import Sim, SimError, TransportError
from .cohorts import MANAGED_PREFIX, Cohort
from .configlib import (AGENT_DIMS, CUSTOMER_FACING, changed_dims, clean, diff_summary, dist,
                        interpolate_allocations, max_shift)
from .context import Ctx, Intent
from .ingest import ts_to_minute
from .stats import normalise
from .store import Store

log = logging.getLogger("ctrl.gov")
OPEN = ("sending", "pending", "approved")


def _j(o) -> str:
    return json.dumps(o, default=str, sort_keys=True)


def compile_config(current: dict, intents: list[Intent], managed: list[Cohort], dims: set[str] | None = None) -> dict:
    """Desired config = current config + the managed cohort list + each module's intent, applied in order."""
    cfg = clean(current)
    if dims is None or "cohorts" in dims:
        keep = [c for c in cfg["cohorts"] if not c["id"].startswith(MANAGED_PREFIX)]
        seen, mc = set(), []
        for c in managed:
            if c.id not in seen and len(keep) + len(mc) < 20:
                seen.add(c.id)
                mc.append(c.to_config())
        cfg["cohorts"] = keep + mc
        ids = {c["id"] for c in cfg["cohorts"]}
        for k in list(cfg["allocations"]):
            if k != "default" and k not in ids:
                del cfg["allocations"][k]
        hw = cfg["schedule"]["hour_weights"]
        for k in list(hw):
            if k != "default" and k not in ids:
                del hw[k]
    for it in intents:
        if dims is None or set(it.dims) & dims:
            it.apply(cfg)
    return cfg


class Governance:
    def __init__(self, store: Store, sim: Sim, prefs: dict):
        self.store, self.sim, self.prefs = store, sim, prefs

    # ------------------------------------------------------------ bookkeeping
    def _rows(self, wf: str, statuses=OPEN) -> list[dict]:
        cols = [d[1] for d in self.store.q("PRAGMA table_info(proposals)")]
        q = f"SELECT * FROM proposals WHERE wf=? AND status IN ({','.join('?' * len(statuses))}) ORDER BY created_minute"
        return [dict(zip(cols, r)) for r in self.store.q(q, (wf, *statuses))]

    def _set(self, pid: str, **kw):
        kw["updated_minute"] = self.store.now_minute
        sets = ", ".join(f"{k}=?" for k in kw)
        self.store.db.execute(f"UPDATE proposals SET {sets} WHERE proposal_id=?", (*kw.values(), pid))

    def _next_id(self, wf: str, kind: str) -> str:
        n = self.store.one("SELECT COUNT(*) FROM proposals WHERE wf=?", (wf,))[0] + 1
        return f"{wf}-{kind}-{n:04d}"

    def snapshot_config(self, wf: str, cfg: dict):
        """Keep the config of every version we see, so 'why' queries can show exactly what was live."""
        v = cfg["version"]
        self.store.db.execute(
            "INSERT INTO config_versions(wf,version,actor,applied_minute,config) VALUES(?,?,?,?,?) "
            "ON CONFLICT(wf,version) DO UPDATE SET config=COALESCE(config_versions.config, excluded.config), "
            "actor=COALESCE(config_versions.actor, excluded.actor)",
            (wf, v, cfg.get("actor"), None, _j(cfg["config"])))

    # ------------------------------------------------------------ reconcile (every tick, and after a crash)
    def reconcile(self, wf: str):
        rows = self._rows(wf, ("sending", "pending", "approved"))
        if not rows:
            return
        server = {a["proposal_id"]: a for a in self.sim.approvals(wf)}
        hist = None
        for r in rows:
            pid = r["proposal_id"]
            if r["route"] == "auto":
                if hist is None:
                    hist = {h.get("proposal_id"): h for h in self.sim.config_history(wf)}
                h = hist.get(pid)
                if h:
                    self._set(pid, status="applied", applied_version=h["version"])
                    self.store.decide(wf, "reconciled", f"auto-apply {pid} had landed as v{h['version']}",
                                      proposal_id=pid, version=h["version"])
                else:
                    self._set(pid, status="unsent", last_error="not found on server after restart")
                continue
            a = server.get(pid)
            if a is None:
                if r["status"] == "sending":
                    self._set(pid, status="unsent", last_error="not found on server after restart")
                continue
            st = a["status"]
            if st == r["status"] and r["approval_id"]:
                continue
            self._set(pid, status=st, approval_id=a["approval_id"], apply_by=a.get("apply_by"),
                      reasons=_j(a.get("reasons") or []),
                      decided_minute=self.store.now_minute if st not in ("pending",) else None)
            if st in ("approved", "rejected", "expired"):
                self.store.decide(wf, f"approval_{st}",
                                  f"{pid} {st}" + (f": {a.get('reasons')}" if a.get("reasons") else ""),
                                  {"approval": a}, proposal_id=pid, approval_id=a["approval_id"])
            if st == "rejected":
                reasons = " ".join(a.get("reasons") or [])
                if "judgement" not in reasons:
                    # a rejection we caused (bad fields, fatigue, evidence) is our bug, not the approver's call
                    self.store.alert(wf, "ticket", f"reject_bug:{pid}",
                                     f"{wf}: proposal {pid} rejected for a fixable reason: {reasons}",
                                     "Check the proposal row's request JSON; fix the field the approver named.")

    # ------------------------------------------------------------ apply approved
    def apply_approved(self, ctx: Ctx, intents: list[Intent]) -> bool:
        changed = False
        by_dim = {}
        for it in intents:
            for d in it.dims:
                by_dim.setdefault(d, []).append(it)
        open_urgent = [r for r in self._rows(ctx.wf, ("sending", "pending")) if r["priority"] == "incident"]
        for r in self._rows(ctx.wf, ("approved",)):
            pid = r["proposal_id"]
            snap = json.loads(r["intents"] or "{}")
            # withdraw if a later SAFETY decision changed a dim this proposal touches
            for d, ver in snap.items():
                cur = [it for it in by_dim.get(d, []) if it.safety and it.version != ver]
                if cur and r["priority"] != "incident":
                    self._set(pid, status="withdrawn", last_error=f"superseded by safety decision on {d}")
                    self.store.decide(ctx.wf, "proposal_withdrawn",
                                      f"{pid} approved but not applied: a later safety decision on {d} overrides it "
                                      f"({cur[0].summary})", {"dim": d}, proposal_id=pid, approval_id=r["approval_id"])
                    break
            else:
                if open_urgent and r["priority"] == "routine" and not self._near_deadline(r, ctx.now):
                    continue  # let the incident fix land first; we apply before apply_by
                changed |= self._apply(ctx, r)
        return changed

    @staticmethod
    def _near_deadline(r: dict, now: int) -> bool:
        if not r.get("apply_by"):
            return True
        return ts_to_minute(r["apply_by"]) - now <= 30

    def _apply(self, ctx: Ctx, r: dict) -> bool:
        pid, aid = r["proposal_id"], r["approval_id"]
        try:
            res = self.sim.post(f"/workflows/{ctx.wf}/apply", {"approval_id": aid})
        except TransportError:
            a = self.sim.approval(aid)
            if a["status"] != "applied":
                return False
            res = {"version": None, "changed_dims": a.get("changed_dims")}
        except SimError as e:
            if e.code == "stale_base_version":
                self._set(pid, status="stale", last_error=str(e.detail))
                self.store.decide(ctx.wf, "apply_stale",
                                  f"{pid} approved but the config moved on (v{r['base_version']} -> "
                                  f"{e.detail}); will re-propose on top of the current config",
                                  {"detail": e.detail}, proposal_id=pid, approval_id=aid)
            elif e.status == 410:
                self._set(pid, status="lapsed", last_error=str(e.detail))
                self.store.decide(ctx.wf, "apply_lapsed", f"{pid} lapsed before we applied it",
                                  proposal_id=pid, approval_id=aid)
                self.store.alert(ctx.wf, "ticket", f"lapsed:{pid}", f"{ctx.wf}: approved change {pid} lapsed",
                                 "Controller missed the apply window: check decision lag / whether it was held.")
            elif e.code == "not_approved" and (e.detail or {}).get("status") == "applied":
                self._set(pid, status="applied")
            else:
                self._set(pid, status="failed", last_error=str(e))
            return False
        v = res.get("version")
        self._set(pid, status="applied", applied_version=v)
        self.store.db.execute(
            "INSERT INTO config_versions(wf,version,actor,applied_minute,changed_dims,proposal_id,approval_id,config) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(wf,version) DO UPDATE SET config=excluded.config, "
            "proposal_id=excluded.proposal_id, approval_id=excluded.approval_id",
            (ctx.wf, v, "controller_approved", ctx.now, _j(res.get("changed_dims")), pid, aid, r["config"]))
        self.store.decide(ctx.wf, "applied", f"applied {pid} ({aid}) as v{v}: {json.loads(r['request'])['change_summary']}",
                          {"changed_dims": res.get("changed_dims")}, proposal_id=pid, approval_id=aid, version=v)
        return True

    # ------------------------------------------------------------ plan new changes
    def plan(self, ctx: Ctx, intents: list[Intent], managed: list[Cohort], ingest) -> None:
        current = clean(ctx.config)
        desired = compile_config(current, intents, managed)
        dims = changed_dims(current, desired)
        if not dims:
            return
        by_dim: dict[str, list[Intent]] = {}
        for it in intents:
            for d in it.dims:
                by_dim.setdefault(d, []).append(it)
        # cohorts changes are attributed to whoever needs the cohort list (schedule / carve-outs)
        by_dim.setdefault("cohorts", [it for it in intents if it.module in ("schedule", "experiments")])
        open_rows = self._rows(ctx.wf)
        touched = self._supervisor_touched(ctx)

        # ---------- 1. auto-apply what the policy and our own rules allow
        if self._try_auto(ctx, current, desired, dims, by_dim, intents, managed, open_rows, touched):
            return  # version moved; plan the rest next tick on the fresh base

        # ---------- 2. approval path for the rest
        auto_dims_possible = set(self._auto_dims(ctx, current, desired, dims, by_dim, touched))
        appr_dims = [d for d in dims if d not in auto_dims_possible or self._auto_blocked(ctx, d)]
        if not appr_dims:
            return
        urgent_its = [it for d in appr_dims for it in by_dim.get(d, []) if it.priority == "incident"]
        if open_rows:
            if not urgent_its or any(r["priority"] == "incident" for r in open_rows):
                return
            kind = "incident"
        else:
            kind = "incident" if urgent_its else (
                "rollback" if any(it.priority == "rollback" for d in appr_dims for it in by_dim.get(d, [])) else "routine")
        if kind == "routine" and not any(it.material for d in appr_dims for it in by_dim.get(d, [])):
            return
        if not self._can_send(ctx, kind):
            return
        if kind == "incident":
            its = [it for it in intents if it.priority == "incident" and it.module == "incidents"] or urgent_its
            proposed = compile_config(current, its, managed, dims={"prompt_version"} | {d for it in its for d in it.dims})
        else:
            its = [it for it in intents if set(it.dims) & set(dims) or it.module in ("schedule",)]
            proposed = desired
        pdims = changed_dims(current, proposed)
        if not pdims:
            return
        its = [it for it in its if set(it.dims) & set(pdims) or ("cohorts" in pdims and it.module == "schedule")]
        self._send_approval(ctx, kind, current, proposed, pdims, its, ingest)

    def _supervisor_touched(self, ctx: Ctx) -> set[str]:
        rows = self.store.q("SELECT changed_dims FROM config_versions WHERE wf=? AND actor='supervisor' AND applied_minute >= ?",
                            (ctx.wf, ctx.now - S.PIN_AFTER_SUPERVISOR_HOURS * 60))
        out = set()
        for (cd,) in rows:
            out |= set(json.loads(cd or "[]"))
        return out

    def _auto_blocked(self, ctx: Ctx, dim: str) -> bool:
        return bool(self.store.one("SELECT 1 FROM proposals WHERE wf=? AND route='auto' AND status='refused' "
                                   "AND created_minute >= ? AND request LIKE ?",
                                   (ctx.wf, ctx.now - 1440, f'%"{dim}"%')))

    def _auto_dims(self, ctx, current, desired, dims, by_dim, touched) -> list[str]:
        pol = ctx.gov.get("auto_apply") or {}
        if not pol.get("enabled"):
            return []
        out = []
        for d in dims:
            its = by_dim.get(d, [])
            if d not in pol.get("allowed_dims", []) or d in touched or not its or not all(it.auto_ok for it in its):
                continue
            if d == "prompt_version":
                continue
            if d in CUSTOMER_FACING and not self._shrinks_exposure(current, desired, d, its):
                continue
            out.append(d)
        # cohorts + schedule must move together when the cohort list changes
        if "cohorts" in dims and "cohorts" not in out:
            out = [d for d in out if d != "schedule"] if any(
                c.get("id", "").startswith(MANAGED_PREFIX) for c in desired["cohorts"]
                if c not in current["cohorts"]) else out
        return out

    @staticmethod
    def _shrinks_exposure(cur: dict, new: dict, d: str, its: list[Intent]) -> bool:
        safe = {it.safe_arm for it in its if it.safe_arm}
        if not safe:
            return False
        keys = {"default"} | {c["id"] for c in cur["cohorts"]} | {c["id"] for c in new["cohorts"]}
        for k in keys:
            if k != "default" and k not in {c["id"] for c in new["cohorts"]}:
                continue
            a, b = normalise(dist(cur, k, d)), normalise(dist(new, k, d))
            if not set(b) <= set(a) | safe:
                return False
            for o, w in b.items():
                if w > a.get(o, 0) + 1e-6 and o not in safe:
                    return False
        return True

    def _try_auto(self, ctx, current, desired, dims, by_dim, intents, managed, open_rows, touched) -> bool:
        pol = ctx.gov.get("auto_apply") or {}
        auto = self._auto_dims(ctx, current, desired, dims, by_dim, touched)
        auto = [d for d in auto if not self._auto_blocked(ctx, d)]
        if not auto:
            return False
        urgent = any(it.safety for d in auto for it in by_dim.get(d, []))
        if open_rows and not urgent:
            return False  # don't make a pending approval stale for a routine tweak
        last = self.store.one("SELECT MAX(created_minute) FROM proposals WHERE wf=? AND route='auto' AND status='applied'",
                              (ctx.wf,))[0]
        if last is not None and ctx.now - last < (S.TICK_MINUTES if urgent else S.AUTO_MIN_GAP_MIN):
            return False
        its = [it for it in intents if set(it.dims) & set(auto)]
        target = compile_config(current, its, managed, dims=set(auto))
        tdims = changed_dims(current, target)
        if not tdims or not set(tdims) <= set(auto):
            # e.g. a schedule table that needs a cohort we can't add without approval
            target = compile_config(current, [it for it in its if it.module != "schedule"], managed,
                                    dims=set(auto) - {"schedule", "cohorts"})
            tdims = changed_dims(current, target)
            if not tdims or not set(tdims) <= set(auto):
                return False
        shift = max_shift(current, target)
        ms = float(pol.get("max_shift", 0))
        stepped = False
        if shift > ms + 1e-9:
            if ms <= 0:
                return False
            alpha = (ms - 1e-6) / shift
            step = interpolate_allocations(current, target, alpha, [d for d in tdims if d in AGENT_DIMS])
            step["cohorts"], step["schedule"], step["prompt_version"] = target["cohorts"], target["schedule"], target["prompt_version"]
            target, stepped = step, True
            tdims = changed_dims(current, target)
            if not tdims:
                return False
        pid = self._next_id(ctx.wf, "auto")
        summary = "; ".join(it.summary for it in its if set(it.dims) & set(tdims))
        rationale = " ".join(it.rationale for it in its if set(it.dims) & set(tdims))
        if stepped:
            rationale += f" (step toward target within max_shift {ms}; shift this step {max_shift(current, target):.3f})"
        body = {"proposal_id": pid, "base_version": ctx.version, "proposed_config": target, "rationale": rationale,
                "evidence": {it.module: it.dry_run for it in its if set(it.dims) & set(tdims)}}
        self._insert(pid, ctx, "auto", "auto", "incident" if urgent else "routine", target,
                     {"change_summary": summary, "rationale": rationale, "dims": tdims,
                      "diff": diff_summary(current, target), "evidence": body["evidence"]}, its)
        try:
            res = self.sim.put(f"/workflows/{ctx.wf}/config", body)
        except TransportError:
            return True  # reconcile() resolves it next tick
        except SimError as e:
            if e.status == 403:
                self._set(pid, status="refused", last_error=_j(e.detail))
                self.store.decide(ctx.wf, "auto_refused", f"auto-apply refused for {tdims}: {e.detail}",
                                  {"problems": e.detail}, proposal_id=pid)
            else:
                self._set(pid, status="failed", last_error=str(e))
            return False
        v = res["version"]
        self._set(pid, status="applied", applied_version=v)
        self.store.db.execute(
            "INSERT INTO config_versions(wf,version,actor,applied_minute,changed_dims,proposal_id,config) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(wf,version) DO UPDATE SET config=excluded.config, proposal_id=excluded.proposal_id",
            (ctx.wf, v, "controller_auto", ctx.now, _j(res.get("changed_dims")), pid, _j(target)))
        self.store.decide(ctx.wf, "auto_applied", f"auto-applied v{v} ({', '.join(tdims)}): {summary}",
                          {"dims": tdims, "diff": diff_summary(current, target), "stepped": stepped},
                          proposal_id=pid, version=v)
        return True

    # ------------------------------------------------------------ approval requests
    def _limits(self, ctx: Ctx) -> tuple[int, int]:
        pref = self.prefs.get(ctx.wf, {})
        limit = min(int(ctx.gov["max_requests_per_24h"]), int(pref.get("max_requests_per_24h", 10**6)))
        sent = self.store.one("SELECT COUNT(*) FROM proposals WHERE wf=? AND route='approval' AND approval_id IS NOT NULL "
                              "AND created_minute > ?", (ctx.wf, ctx.now - 1440))[0]
        return sent, limit

    def _can_send(self, ctx: Ctx, kind: str) -> bool:
        sent, limit = self._limits(ctx)
        reserve = 0 if kind in S.URGENT_KINDS else S.ROUTINE_RESERVE_SLOTS
        if sent >= max(limit - reserve, 0 if kind in S.URGENT_KINDS else 1) or sent >= limit:
            return False
        lo, hi = ctx.gov["approver_hours"]
        mod = ctx.now % 1440
        earliest = lo * 60
        if kind == "routine":
            earliest = max(earliest, int(self.prefs.get(ctx.wf, {}).get("earliest_request_hour", lo)) * 60)
        if not earliest <= mod < hi * 60:
            return False
        lat_lo, lat_hi = ctx.gov["approver_latency_minutes"]
        if kind == "routine":
            if mod + (lat_lo + lat_hi) / 2 > hi * 60:
                return False  # would sit in tonight's queue (stakeholders/02)
            last_neg = self.store.one("SELECT MAX(updated_minute) FROM proposals WHERE wf=? AND status IN "
                                      "('rejected','expired')", (ctx.wf,))[0]
            if last_neg is not None and ctx.now - last_neg < S.REJECT_COOLDOWN_HOURS * 60:
                return False
        else:
            ttl = float(ctx.gov["proposal_ttl_hours"]) * 60
            worst = mod + lat_hi
            if worst > hi * 60:
                worst = 1440 + lo * 60 + (worst - hi * 60)
            if worst - mod > ttl:
                return False
        return True

    def _send_approval(self, ctx: Ctx, kind: str, current: dict, proposed: dict, pdims: list[str],
                       its: list[Intent], ingest) -> None:
        pid = self._next_id(ctx.wf, kind)
        active = ingest.active_lead_codes(ctx.wf, ctx.day, len(ctx.spec["cadence"]))
        total = sum(active.values())
        affected = total
        scoped = [it.affected for it in its if it.affected is not None]
        if scoped and len(scoped) == len(its):
            cs = [c for lst in scoped for c in lst]
            affected = sum(n for code, n in active.items() if any(c.matches_code(code, ctx.vocab) for c in cs))
        impacts = [it.impact if it.impact is not None else max(0.0, min(1.0, it.expected_rel_gain / 0.10)) for it in its]
        impact = round(max(impacts) if impacts else 0.05, 3)
        summary = "; ".join(dict.fromkeys(it.summary for it in its)) or f"update {', '.join(pdims)}"
        rationale = " ".join(dict.fromkeys(it.rationale for it in its)) or summary
        evidence = {}
        for it in its:
            if it.evidence:
                evidence.update(it.evidence)
        dry = {it.module + (":" + "+".join(it.dims) if it.module == "experiments" else ""): it.dry_run for it in its}
        dry["affected_leads_basis"] = (f"{affected} of {total} leads still being worked (campaigns inside their "
                                       f"cadence, not yet concluded)")
        body = {"proposal_id": pid, "workflow_id": ctx.wf, "base_version": ctx.version, "proposed_config": proposed,
                "change_summary": summary[:300], "diff": diff_summary(current, proposed) or {"dims": pdims},
                "affected_leads": int(affected), "dry_run": dry, "impact_score": impact, "rationale": rationale,
                "evidence": evidence or None, "expires_in_hours": float(ctx.gov["proposal_ttl_hours"])}
        self._insert(pid, ctx, kind, "approval", kind, proposed,
                     {k: v for k, v in body.items() if k != "proposed_config"}, its)
        try:
            a = self.sim.post("/approvals", body)
        except TransportError:
            return  # stays 'sending'; reconcile() looks it up by proposal_id
        except SimError as e:
            if e.code == "duplicate_proposal_id":
                return  # it landed before a crash; reconcile() will pick it up
            self._set(pid, status="unsent" if e.code == "stale_base_version" else "failed", last_error=str(e))
            self.store.decide(ctx.wf, "proposal_error", f"{pid} not accepted: {e}", {"error": str(e)}, proposal_id=pid)
            if e.code != "stale_base_version":
                self.store.alert(ctx.wf, "ticket", f"proposal_error:{ctx.wf}:{e.code}",
                                 f"{ctx.wf}: approval request rejected by API: {e}",
                                 "Contract mismatch between controller and governance API; see proposals.last_error.")
            return
        self._set(pid, status=a["status"], approval_id=a["approval_id"])
        self.store.decide(ctx.wf, "proposal_sent",
                          f"requested approval {a['approval_id']} ({kind}, impact {impact}): {summary}",
                          {"dims": pdims, "affected_leads": affected, "impact_score": impact,
                           "expires_at": a.get("expires_at")}, proposal_id=pid, approval_id=a["approval_id"])

    def _insert(self, pid, ctx, kind, route, priority, cfg, request, its):
        snap = {}
        for it in its:
            for d in it.dims:
                snap[d] = it.version
        self.store.db.execute(
            "INSERT INTO proposals(proposal_id,wf,kind,route,status,priority,created_minute,base_version,config,request,"
            "intents,updated_minute) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, ctx.wf, kind, route, "sending", priority, ctx.now, ctx.version, _j(cfg), _j(request), _j(snap), ctx.now))

