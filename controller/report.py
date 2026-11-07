"""Read-only views over the store: supervisor view, audit ('why'), customer lookup, loop health.

Everything here answers from the controller's own SQLite store. It never calls the simulator.
"""
from __future__ import annotations

import json

from .ingest import ts_to_minute
from .store import Store


def fmt_min(m: int | None) -> str:
    if m is None:
        return "-"
    d, r = divmod(int(m), 1440)
    return f"D{d} {r // 60:02d}:{r % 60:02d}"


def parse_when(s: str) -> int:
    s = s.strip()
    if s.upper().startswith("D"):
        day, hm = s.split()
        h, m = hm.split(":")
        return int(day[1:]) * 1440 + int(h) * 60 + int(m)
    return ts_to_minute(s)


def _kpis(store: Store, wf: str, d0: int, d1: int) -> dict:
    r = store.one("SELECT SUM(dials), SUM(conn), SUM(eng), SUM(flags) FROM agg_prompt WHERE wf=? AND day*1440+hour*60 >= ? "
                  "AND day*1440+hour*60 < ?", (wf, d0, d1))
    d, c, e, f = (x or 0 for x in r)
    return {"dials": d, "pickup_rate": round(c / d, 4) if d else None, "engagement_rate": round(e / c, 4) if c else None,
            "halluc_flag_rate": round(f / c, 4) if c else None}


# ---------------------------------------------------------------- supervisor view (C11)
def supervisor_view(store: Store, wf: str) -> str:
    now = store.meta("sim_minute", 0)
    spec = json.loads(store.one("SELECT spec FROM workflows WHERE wf=?", (wf,))[0])
    obj = spec["objective"]
    L = [f"# {wf} — supervisor view at {fmt_min(now)}", "",
         f"Objective: **{obj['primary']}** (guardrail {obj.get('guardrail')}). Pickup and engagement are reported "
         f"separately and never combined.", ""]
    today = (now // 1440) * 1440
    t, y = _kpis(store, wf, today, today + 1440), _kpis(store, wf, today - 1440, today)
    base = _kpis(store, wf, -14 * 1440, 0)
    L += ["## KPIs", "", "| | pickup | engagement | hallucination flags | dials |", "|---|---|---|---|---|"]
    for name, k in (("today", t), ("yesterday", y), ("history (pre-D0)", base)):
        L.append(f"| {name} | {k['pickup_rate'] or '-'} | {k['engagement_rate'] or '-'} | {k['halluc_flag_rate'] or '-'} | {k['dials']} |")
    # what we want from you
    pend = store.q("SELECT proposal_id, approval_id, status, kind, request, created_minute FROM proposals WHERE wf=? AND "
                   "route='approval' AND status IN ('pending','approved','sending') ORDER BY created_minute", (wf,))
    L += ["", "## Waiting on you", ""]
    if not pend:
        L.append("Nothing.")
    for pid, aid, stt, kind, req, cm in pend:
        r = json.loads(req)
        L.append(f"- **{aid or pid}** ({kind}, {stt}, sent {fmt_min(cm)}, impact {r.get('impact_score')}, "
                 f"{r.get('affected_leads')} leads): {r.get('change_summary')}")
    # tests
    exp = store.load_state(wf, "experiments") or {}
    L += ["", "## What is being tested / what is winning", ""]
    if not exp.get("enabled", False):
        L.append(f"No variant tests: {exp.get('reason', 'not started')}.")
    for d, s in (exp.get("dims") or {}).items():
        line = f"- **{d}**: {s['status']}"
        if s["status"] == "exploring":
            line += f", arms {s['shares']} vs incumbent {s['incumbent']}, live since {fmt_min(s['live_since'])}"
        elif s["status"] == "promoted":
            p = s["promo"]
            line += (f": {s['winner']} won ({p['rel_lift']:+.1%} engagement, z={p['z']}), {s['control']} kept as "
                     f"control; last decay check {s.get('last_decay_check', {}).get('rel_lift', '-')}")
        if s.get("excluded"):
            line += f"; excluded {s['excluded']}"
        if s.get("carveouts"):
            line += f"; carved out {list(s['carveouts'])}"
        if s.get("note"):
            line += f" ({s['note']})"
        L.append(line)
    did = store.load_state(wf, "callerid") or {}
    if did:
        L.append(f"- **caller IDs**: target {did.get('target')}; quarantined {list(did.get('quarantined', {}))}")
    sch = store.load_state(wf, "schedule") or {}
    if sch.get("status") == "active":
        L.append(f"- **call time**: weights by {'x'.join(sch['partition'])}, predicted pickup {sch['gain']:+.1%}")
    inc = store.load_state(wf, "incidents") or {}
    if inc.get("open"):
        o = inc["open"]
        L.append(f"- **OPEN INCIDENT** {o['id']}: {o['kind']} on {o['prompt']} ({o['status']}, target {o.get('target')})")
    # changed today
    L += ["", "## What changed today", ""]
    rows = store.q("SELECT version, actor, applied_minute, changed_dims, proposal_id, approval_id FROM config_versions "
                   "WHERE wf=? AND applied_minute >= ? ORDER BY version", (wf, today))
    if not rows:
        L.append("No config changes today.")
    for v, actor, at, cd, pid, aid in rows:
        why = ""
        if pid:
            r = store.one("SELECT request FROM proposals WHERE proposal_id=?", (pid,))
            if r:
                why = json.loads(r[0]).get("change_summary", "")
        L.append(f"- {fmt_min(at)} v{v} by {actor} {json.loads(cd or '[]')}"
                 f"{' approval ' + aid if aid else ''}: {why or ('supervisor edit' if actor == 'supervisor' else '')}")
    # operational changes applied under policy (for the weekly summary to compliance, stakeholders/02)
    auto = store.q("SELECT applied_minute, version, changed_dims FROM config_versions WHERE wf=? AND actor='controller_auto' "
                   "AND applied_minute >= ? ORDER BY version", (wf, today - 6 * 1440))
    L += ["", f"## Operational changes applied under the signed policy (last 7 days): {len(auto)}", ""]
    for at, v, cd in auto[-10:]:
        L.append(f"- {fmt_min(at)} v{v} {json.loads(cd or '[]')}")
    al = store.q("SELECT severity, message, minute FROM alerts WHERE (wf=? OR wf IS NULL) AND resolved_minute IS NULL", (wf,))
    L += ["", f"## Open issues ({len(al)})", ""]
    L += [f"- [{s}] {fmt_min(m)} {msg}" for s, msg, m in al] or ["None."]
    return "\n".join(L)


# ---------------------------------------------------------------- audit (C12)
def why(store: Store, wf: str, at: int) -> str:
    row = store.one("SELECT version, actor, applied_minute, changed_dims, proposal_id, approval_id, config FROM config_versions "
                    "WHERE wf=? AND (applied_minute IS NULL OR applied_minute <= ?) ORDER BY version DESC LIMIT 1", (wf, at))
    if not row:
        return f"no config version known for {wf} at {fmt_min(at)}"
    v, actor, applied, cd, pid, aid, cfg = row
    L = [f"## {wf} at {fmt_min(at)}: config v{v}", "",
         f"- set by **{actor}** at {fmt_min(applied)}; dims changed: {json.loads(cd or '[]')}"]
    if aid:
        L.append(f"- approval **{aid}** (proposal {pid})")
    if pid:
        p = store.one("SELECT kind, route, created_minute, request, decided_minute, reasons FROM proposals WHERE proposal_id=?", (pid,))
        if p:
            kind, route, cm, req, dm, reasons = p
            r = json.loads(req)
            L += [f"- requested {fmt_min(cm)} via {route} ({kind}); decided {fmt_min(dm)}",
                  f"- summary: {r.get('change_summary')}", f"- rationale: {r.get('rationale')}",
                  *([f"- impact score {r.get('impact_score')}, affected leads {r.get('affected_leads')}"]
                    if route == "approval" else ["- auto-applied within the signed policy (no approval needed)"]),
                  f"- evidence: {json.dumps(r.get('evidence'))[:600]}",
                  f"- diff: {json.dumps(r.get('diff'))[:900]}"]
    elif actor == "supervisor":
        L.append("- a client supervisor edit (outside governance, by design); the controller did not revert it")
    L += ["", "### Decisions that led here", ""]
    q = store.q("SELECT minute, kind, summary FROM decisions WHERE wf=? AND minute <= ? AND minute >= ? AND "
                "kind NOT IN ('callerid_target','experiment_live') ORDER BY id DESC LIMIT 12",
                (wf, applied if applied is not None else at, (applied or at) - 3 * 1440))
    L += [f"- {fmt_min(m)} [{k}] {s}" for m, k, s in reversed(q)]
    L += ["", "### What happened next", ""]
    if applied is not None:
        before = _kpis(store, wf, applied - 1440, applied)
        after = _kpis(store, wf, applied, applied + 1440)
        L.append(f"- 24h before: {before}")
        L.append(f"- 24h after:  {after}")
    nxt = store.one("SELECT version, actor, applied_minute, changed_dims FROM config_versions WHERE wf=? AND version > ? "
                    "ORDER BY version LIMIT 1", (wf, v))
    if nxt:
        L.append(f"- next change: v{nxt[0]} by {nxt[1]} at {fmt_min(nxt[2])} {json.loads(nxt[3] or '[]')}")
    q = store.q("SELECT minute, kind, summary FROM decisions WHERE wf=? AND minute > ? AND kind NOT IN "
                "('callerid_target','experiment_live') ORDER BY id LIMIT 8", (wf, applied if applied is not None else at))
    L += [f"- {fmt_min(m)} [{k}] {s}" for m, k, s in q]
    return "\n".join(L)


def customer(store: Store, lead_id: str) -> str:
    """'Why did this customer hear what they heard?' (stakeholders/02, point 3)."""
    wf = lead_id.split(":")[0]
    idx = int(lead_id[lead_id.rindex(":") + 2:])
    rows = store.q("SELECT event_id, minute, ver, cohort, wm, voice, did, prompt, conn, eng FROM calls WHERE wf=? AND lead=? "
                   "ORDER BY minute", (wf, idx))
    if not rows:
        return f"no calls stored for {lead_id}"
    L = [f"## {lead_id}", ""]
    for eid, m, ver, coh, wm, vo, did, pr, c, e in rows:
        L.append(f"- {fmt_min(m)} config v{ver}, cohort {coh}: welcome {wm}, voice {vo}, caller ID {did}, prompt {pr} "
                 f"-> {'engaged' if e else 'connected' if c else 'no answer'}")
        r = store.one("SELECT actor, approval_id, proposal_id FROM config_versions WHERE wf=? AND version=?", (wf, ver))
        if r:
            reason = ""
            if r[2]:
                p = store.one("SELECT request FROM proposals WHERE proposal_id=?", (r[2],))
                reason = json.loads(p[0]).get("change_summary") if p else ""
            L.append(f"    v{ver} was set by {r[0]}{' (approval ' + r[1] + ')' if r[1] else ''}{': ' + reason if reason else ''}")
    return "\n".join(L)


# ---------------------------------------------------------------- loop health (C14)
def status(store: Store) -> str:
    rows = store.q("SELECT minute, data FROM metrics ORDER BY minute DESC LIMIT 96")
    if not rows:
        return "no metrics yet"
    ms = [json.loads(d) for _, d in rows]
    last = ms[0]
    eps = [m["ingest_eps"] for m in ms if m.get("ingest_eps")]
    L = ["# Controller health", "",
         f"- sim time {last['ts']}, restarts {store.meta('restarts', 0)}",
         f"- event lag: {last['event_lag']} events (0 = fully caught up)",
         f"- ingest rate (last {len(ms)} ticks): median {sorted(eps)[len(eps) // 2] if eps else '-'} ev/s",
         f"- decision lag (ingest+decide, last tick): {last['decision_lag_s']}s; max over window "
         f"{max(m['decision_lag_s'] for m in ms)}s",
         f"- decisions in window: {sum(m['decisions'] for m in ms)}; credits remaining {last['credits_remaining']}",
         f"- open proposals {last['open_proposals']}; peak RSS {last['rss_mb']} MB", "", "## Open alerts", ""]
    al = store.q("SELECT severity, wf, message, runbook, minute FROM alerts WHERE resolved_minute IS NULL ORDER BY severity")
    L += [f"- [{s}] {fmt_min(m)} {wf or '-'}: {msg}\n    runbook: {rb}" for s, wf, msg, rb, m in al] or ["None."]
    return "\n".join(L)
