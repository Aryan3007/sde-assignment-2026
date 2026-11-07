"""Hallucination incidents: detect -> pick a fix -> validate -> propose with evidence -> verify after apply.

Detection is a one-sided exact binomial test of the current prompt's QA hallucination-flag rate
over the last HALLUC_WINDOW_HOURS against 1.2x the workflow's historical baseline flag rate,
requiring p < HALLUC_P AND an observed rate >= HALLUC_MIN_RATIO x baseline on >= HALLUC_MIN_CONN
connected calls. The QA detector has false positives (~1%), so the baseline already includes
them. We compare like with like.

Picking a fix, cheapest evidence first ("the best compute is no compute"):
1. A prompt this workflow has actually run, with enough connected calls and a flag rate not
   above baseline. That is observed engagement AND safety on real traffic. Most recent first.
2. Only if there is none: ask the LLM (3 credits) for alternatives.
Every candidate must pass ONE validation run (VALIDATION_N conversations, 2 credits) at the
approver's min_validation_pass_rate. We never re-roll a failed validation.

Verification after apply: once VERIFY_MIN_CONN calls have connected on the fixed prompt, the flag
rate must be back under the detection threshold. Engagement is checked against the pre-incident
baseline. A "fix" that is safe but kills engagement (a near-verbatim compliance script) raises an
alert instead of being declared a success.
"""
from __future__ import annotations

import logging

from . import settings as S
from .client import SimError
from .context import Ctx, Intent
from .stats import binom_sf, stratified_diff

log = logging.getLogger("ctrl.incidents")


class Incidents:
    name = "incidents"

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.st = ctx.store.load_state(ctx.wf, self.name) or {
            "baseline": None, "open": None, "v": 0, "bad": {}, "resolved": [], "credits_spent": 0}

    def save(self):
        self.ctx.store.save_state(self.ctx.wf, self.name, self.st)

    # ------------------------------------------------------------ data
    def _prompt_stats(self, since_minute: int | None = None, until_day: int | None = None) -> dict:
        ctx = self.ctx
        if since_minute is None:
            rows = ctx.store.q("SELECT prompt, SUM(dials), SUM(conn), SUM(eng), SUM(flags), MAX(day*24+hour) "
                               "FROM agg_prompt WHERE wf=? GROUP BY prompt", (ctx.wf,))
        else:
            h0 = since_minute // 60
            rows = ctx.store.q("SELECT prompt, SUM(dials), SUM(conn), SUM(eng), SUM(flags), MAX(day*24+hour) "
                               "FROM agg_prompt WHERE wf=? AND day >= ? AND day*24+hour >= ? GROUP BY prompt",
                               (ctx.wf, h0 // 24, h0))
        return {p: {"dials": d, "conn": c, "eng": e, "flags": f, "last_hour": lh} for p, d, c, e, f, lh in rows}

    def _baseline(self) -> dict:
        b = self.st["baseline"]
        if b is None:
            hist = self.ctx.store.q("SELECT prompt, SUM(conn), SUM(eng), SUM(flags) FROM agg_prompt WHERE wf=? AND day < 0 "
                                    "GROUP BY prompt ORDER BY SUM(conn) DESC", (self.ctx.wf,))
            if not hist:
                return {"prompt": None, "flag_rate": 0.015, "eng": None, "conn": 0}
            p, c, e, f = hist[0]
            b = {"prompt": p, "flag_rate": max(f / c, 0.005) if c else 0.015, "eng": e / c if c else None, "conn": c}
            self.st["baseline"] = b
            self.save()
        return b

    def _excess(self, conn: int, flags: int, base: float, fast: bool = False) -> tuple[bool, float]:
        """Is the flag rate on `conn` connected calls above baseline? `fast` = an independent validation run
        already failed this prompt, so live evidence needs a lower bar (two independent signals)."""
        if conn == 0:
            return False, 1.0
        p = binom_sf(flags, conn, min(base * 1.2, 0.99))
        alpha, n_min = (S.HALLUC_P_FAST, S.HALLUC_MIN_CONN_FAST) if fast else (S.HALLUC_P, S.HALLUC_MIN_CONN)
        return (conn >= n_min and p < alpha and flags / conn >= S.HALLUC_MIN_RATIO * base), p

    def _watch_prompt_change(self, cur: str):
        """Someone else changed the prompt: spend one validation run (2 credits) on the new prompt now.

        It changes a decision: if it fails, a hallucination incident can open on less live evidence,
        cutting time-to-fix by hours. A prompt we changed ourselves was already validated."""
        ctx, st = self.ctx, self.st
        last = st.get("last_prompt")
        if last == cur:
            return
        st["last_prompt"] = cur
        if last is None:
            self.save()
            return
        row = ctx.store.one("SELECT actor FROM config_versions WHERE wf=? ORDER BY version DESC LIMIT 1", (ctx.wf,))
        actor = row[0] if row else None
        if actor not in ("supervisor", None) or ctx.credits - 2 < S.CREDIT_RESERVE:
            self.save()
            return
        try:
            v = ctx.sim.post("/validation/runs", {"workflow_id": ctx.wf, "prompt_version": cur, "n": S.VALIDATION_N})
        except SimError as e:
            ctx.store.decide(ctx.wf, "incident_watch_error", str(e), {"prompt": cur})
            self.save()
            return
        ctx.credits = v.get("credits_remaining", ctx.credits)
        ctx.store.db.execute("INSERT OR REPLACE INTO validation_runs VALUES(?,?,?,?,?,?,?)",
                             (v["validation_run_id"], ctx.wf, cur, S.VALIDATION_N, v["pass_rate"], ctx.now, _j(v)))
        ok = v["pass_rate"] >= ctx.gov["min_validation_pass_rate"]
        st["watch"] = {"prompt": cur, "at": ctx.now, "validation_run_id": v["validation_run_id"],
                       "pass_rate": v["pass_rate"], "ok": ok}
        ctx.store.decide(ctx.wf, "incident_watch",
                         f"prompt changed {last} -> {cur} by {actor}; validation pass rate {v['pass_rate']:.3f} "
                         f"({'ok' if ok else 'BELOW ' + str(ctx.gov['min_validation_pass_rate']) + ': watching live flags closely'})",
                         {"from": last, "to": cur, **v})
        self.save()

    # ------------------------------------------------------------ lifecycle
    def observe(self):
        ctx, st = self.ctx, self.st
        if ctx.now < 0:
            return
        base = self._baseline()
        cur = ctx.config["prompt_version"]
        inc = st["open"]
        self._watch_prompt_change(cur)
        win = self._prompt_stats(ctx.now - S.HALLUC_WINDOW_HOURS * 60).get(cur)
        if inc is None:
            if not win:
                return
            w = st.get("watch") or {}
            failed_validation = w.get("prompt") == cur and not w.get("ok", True)
            hit, p = self._excess(win["conn"], win["flags"], base["flag_rate"], fast=failed_validation)
            if hit:
                self._open(cur, win, p, base)
            return
        # an incident is open
        if inc["status"] in ("selecting", "validating"):
            self._select_fix()
        if inc["status"] == "proposed" and cur == inc["target"]:
            inc.update(status="verifying", applied_at=ctx.now)
            ctx.store.decide(ctx.wf, "incident_fix_live", f"{inc['id']}: prompt {cur} is live, verifying",
                             {"incident": inc["id"]}, version=ctx.version)
            self.save()
        if inc["status"] == "proposed" and cur != inc["target"] and cur != inc["prompt"]:
            # someone else (a supervisor) changed the prompt while our fix was pending: re-assess
            inc.update(status="superseded")
            ctx.store.decide(ctx.wf, "incident_superseded",
                             f"{inc['id']}: prompt changed to {cur} by someone else; re-assessing from scratch",
                             {"incident": inc["id"]})
            st["resolved"].append(inc)
            st["open"] = None
            st["v"] += 1
            self.save()
            return
        if inc["status"] == "verifying":
            post = self._prompt_stats(inc["applied_at"]).get(cur)
            if post and post["conn"] >= S.VERIFY_MIN_CONN:
                still, p = self._excess(post["conn"], post["flags"], base["flag_rate"])
                rate = post["flags"] / post["conn"]
                eng = post["eng"] / post["conn"]
                if still:
                    st["bad"][cur] = "hallucinates after apply"
                    inc.update(status="selecting", target=None)
                    ctx.store.decide(ctx.wf, "incident_fix_failed",
                                     f"{inc['id']}: {cur} still flags {rate:.2%} (p={p:.1e}); trying the next candidate",
                                     {"incident": inc["id"], **post})
                else:
                    inc.update(status="resolved", resolved_at=ctx.now, after={"flag_rate": round(rate, 4),
                                                                                "engagement": round(eng, 4), **post})
                    ttf = ctx.now - inc["opened_at"]
                    ctx.store.decide(ctx.wf, "incident_resolved",
                                     f"{inc['id']}: flag rate back to {rate:.2%} on {post['conn']} calls "
                                     f"(baseline {base['flag_rate']:.2%}); {ttf // 60}h{ttf % 60:02d}m from detection",
                                     {"incident": inc["id"], **inc["after"]})
                    ctx.store.resolve_alert(f"halluc:{ctx.wf}")
                    ctx.store.db.execute("UPDATE incidents SET status='resolved', closed_minute=?, detail=? WHERE id=?",
                                         (ctx.now, _j(inc), inc["id"]))
                    if base["eng"] and eng < base["eng"] * 0.95:
                        c = stratified_diff([(post["conn"], post["eng"], base["conn"], int(base["eng"] * base["conn"]))])
                        if c.z <= -2.5:
                            ctx.store.alert(ctx.wf, "ticket", f"fix_engagement:{ctx.wf}",
                                            f"{ctx.wf}: prompt {cur} fixed hallucinations but engagement is "
                                            f"{eng:.1%} vs {base['eng']:.1%} before the incident",
                                            "Safe-but-boring fix. Look at the supervisor view; consider the next "
                                            "candidate prompt with the client.")
                    st["resolved"].append(inc)
                    st["open"] = None
                st["v"] += 1
                self.save()

    def _open(self, cur: str, win: dict, p: float, base: dict):
        ctx, st = self.ctx, self.st
        iid = f"inc_{ctx.wf}_{ctx.now}"
        rate = win["flags"] / win["conn"]
        inc = {"id": iid, "kind": "hallucination", "prompt": cur, "opened_at": ctx.now, "status": "selecting",
               "evidence": {"window_hours": S.HALLUC_WINDOW_HOURS, "connected": win["conn"], "flags": win["flags"],
                            "flag_rate": round(rate, 4), "baseline_flag_rate": round(base["flag_rate"], 4),
                            "p_value": p},
               "candidates_tried": [], "target": None, "validation": None, "credits": 0}
        st["open"] = inc
        st["bad"][cur] = f"flag rate {rate:.2%} vs baseline {base['flag_rate']:.2%}"
        ctx.store.db.execute("INSERT OR REPLACE INTO incidents VALUES(?,?,?,?,?,?,?)",
                             (iid, ctx.wf, "hallucination", "open", ctx.now, None, _j(inc)))
        ctx.store.decide(ctx.wf, "incident_open",
                         f"hallucination flags on prompt {cur}: {win['flags']}/{win['conn']} = {rate:.2%} in the last "
                         f"{S.HALLUC_WINDOW_HOURS}h vs baseline {base['flag_rate']:.2%} (p={p:.1e})", inc["evidence"])
        ctx.store.alert(ctx.wf, "page", f"halluc:{ctx.wf}",
                        f"{ctx.wf}: hallucination rate {rate:.1%} on prompt {cur} ({base['flag_rate']:.1%} baseline). "
                        f"Controller is preparing a validated fix.",
                        "Watch `python -m controller view --workflow WF`. If no fix is proposed within 1h, check "
                        "credits_remaining and the incident row in the store.")
        self._select_fix()
        self.save()

    def _candidates(self) -> list[tuple[str, str]]:
        """(prompt, source) in preference order."""
        ctx, st = self.ctx, self.st
        inc = st["open"]
        base = self._baseline()
        tried = {c["prompt"] for c in inc["candidates_tried"]}
        options = ctx.spec["options"]["prompt_version"]
        allp = self._prompt_stats()
        good = []
        for p, s in allp.items():
            if p == inc["prompt"] or p in st["bad"] or p in tried or p not in options or s["conn"] < 300:
                continue
            hit, _ = self._excess(s["conn"], s["flags"], base["flag_rate"])
            if not hit and s["flags"] / s["conn"] <= 1.3 * base["flag_rate"]:
                good.append((s["last_hour"], p))
        out = [(p, "observed on this workflow's own traffic") for _, p in sorted(good, reverse=True)]
        for p in inc.get("llm_suggestions", []):
            if p not in tried and p not in st["bad"] and p != inc["prompt"] and p not in [o for o, _ in out]:
                out.append((p, "LLM suggestion"))
        return out

    def _select_fix(self):
        ctx, st = self.ctx, self.st
        inc = st["open"]
        while True:
            cands = self._candidates()
            if not cands and not inc.get("llm_asked"):
                if ctx.credits - 3 < S.CREDIT_RESERVE / 2 or inc["credits"] + 3 > S.INCIDENT_MAX_CREDITS:
                    break
                try:
                    r = ctx.sim.post("/llm/suggest-fixes", {
                        "workflow_id": ctx.wf,
                        "problem": f"prompt {inc['prompt']} hallucination-flag rate {inc['evidence']['flag_rate']:.2%} "
                                   f"vs {inc['evidence']['baseline_flag_rate']:.2%} baseline"})
                except SimError as e:
                    ctx.store.decide(ctx.wf, "incident_llm_error", str(e), {"incident": inc["id"]})
                    break
                inc["llm_asked"] = True
                inc["credits"] += 3
                ctx.credits = r.get("credits_remaining", ctx.credits)
                inc["llm_suggestions"] = [s["prompt_version"] for s in r.get("suggestions", [])]
                ctx.store.decide(ctx.wf, "incident_llm_suggest",
                                 f"{inc['id']}: no known-good prompt; LLM suggested {inc['llm_suggestions']} (3 credits)",
                                 {"incident": inc["id"], "suggestions": r.get("suggestions")})
                continue
            if not cands:
                break
            p, source = cands[0]
            n = S.VALIDATION_N
            cost = -(-n // 100)
            if inc["credits"] + cost > S.INCIDENT_MAX_CREDITS or ctx.credits - cost < 1:
                ctx.store.alert(ctx.wf, "page", f"halluc_credits:{ctx.wf}",
                                f"{ctx.wf}: incident {inc['id']} cannot validate a fix (credits).",
                                "Raise the credit budget or approve a known-good prompt manually.")
                break
            try:
                v = ctx.sim.post("/validation/runs", {"workflow_id": ctx.wf, "prompt_version": p, "n": n})
            except SimError as e:
                ctx.store.decide(ctx.wf, "incident_validation_error", str(e), {"incident": inc["id"], "prompt": p})
                break
            inc["credits"] += cost
            ctx.credits = v.get("credits_remaining", ctx.credits)
            ctx.store.db.execute("INSERT OR REPLACE INTO validation_runs VALUES(?,?,?,?,?,?,?)",
                                 (v["validation_run_id"], ctx.wf, p, n, v["pass_rate"], ctx.now, _j(v)))
            need = ctx.gov["min_validation_pass_rate"]
            ok = v["pass_rate"] >= need
            inc["candidates_tried"].append({"prompt": p, "source": source, "validation_run_id": v["validation_run_id"],
                                            "pass_rate": v["pass_rate"], "ok": ok})
            ctx.store.decide(ctx.wf, "incident_validate",
                             f"{inc['id']}: candidate {p} ({source}) validation pass rate {v['pass_rate']:.3f} "
                             f"on n={n} ({'pass' if ok else 'FAIL'}, need >= {need})",
                             {"incident": inc["id"], **v})
            if ok:
                inc.update(status="proposed", target=p, validation=v["validation_run_id"], source=source)
                st["v"] += 1
                break
            st["bad"][p] = f"validation pass rate {v['pass_rate']:.3f} < {need}"
        if inc["status"] in ("selecting", "validating") and not self._candidates() and inc.get("llm_asked"):
            ctx.store.alert(ctx.wf, "page", f"halluc_nofix:{ctx.wf}",
                            f"{ctx.wf}: no validated prompt available to fix incident {inc['id']}",
                            "Human decision needed: the client must pick a prompt. See incident evidence.")
        ctx.store.db.execute("UPDATE incidents SET detail=? WHERE id=?", (_j(inc), inc["id"]))
        self.save()

    def intents(self) -> list[Intent]:
        inc = self.st["open"]
        if not inc or inc["status"] != "proposed" or not inc["target"]:
            return []
        target = inc["target"]
        tried = inc["candidates_tried"][-1]
        allp = self._prompt_stats()
        obs = allp.get(target)

        def apply(cfg):
            cfg["prompt_version"] = target

        ev = inc["evidence"]
        excess = ev["flag_rate"] - ev["baseline_flag_rate"]
        return [Intent(
            module=self.name, dims=["prompt_version"], apply=apply, version=self.st["v"],
            summary=f"fix hallucinations: prompt {inc['prompt']} -> {target} (validated {tried['pass_rate']:.1%})",
            rationale=(f"Prompt {inc['prompt']} is producing hallucination flags at {ev['flag_rate']:.2%} vs "
                       f"{ev['baseline_flag_rate']:.2%} baseline ({ev['flags']}/{ev['connected']} connected calls, "
                       f"p={ev['p_value']:.1e}). {target} is {inc['source']}"
                       + (f" ({obs['conn']} connected calls, flag rate {obs['flags'] / obs['conn']:.2%}, engagement "
                          f"{obs['eng'] / obs['conn']:.1%})" if obs and obs["conn"] else "")
                       + f" and passed validation at {tried['pass_rate']:.3f}."),
            # prompts always go to a named human, even if a policy allowed auto-apply (stakeholders/02)
            priority="incident", material=True, auto_ok=False, safety=True,
            expected_rel_gain=0.0, impact=min(1.0, 0.5 + excess / 0.04),
            dry_run={"method": "observed history of the candidate prompt on this workflow + validation run",
                     "candidate_observed": obs, "current_window": ev, "validation": tried},
            evidence={"validation_run_id": inc["validation"]},
            affected=None)] if excess > 0 else []


def _j(o) -> str:
    import json
    return json.dumps(o, default=str)
