"""Variant experiments on welcome_message and voice: explore, decide, promote, watch for decay, roll back.

Design (details and the false-winner simulation are in SUBMISSION.md section 5):
- Factorial by construction. The simulator samples each dim independently per dial, so one test
  per dim runs on the same traffic and each dim's marginal comparison is unbiased.
- Evidence = what was actually served (the `variant` on each call event), never the intended
  allocation. An arm that never ran contributes nothing.
- Comparisons are stratified by (day, 3h block, served cohort). Drift that hits every arm
  (prompt incident, caller-ID change, allocation re-weighting between periods) cancels out.
- Engagement-primary workflows only. On pickup-primary workflows engagement is the guardrail,
  so we don't buy engagement risk there.
"""
from __future__ import annotations

import math
from collections import defaultdict

from . import settings as S
from .cohorts import MANAGED_PREFIX, Cohort, segments
from .configlib import dist
from .context import Ctx, Intent
from .stats import Comparison, normalise, stratified_diff

DIMS = ("welcome_message", "voice")
COL = {"welcome_message": "wm", "voice": "voice"}


def bucket(minute: int) -> int:
    return (minute // 1440) * 8 + (minute % 1440) // 180


def _fresh(incumbent: str) -> dict:
    return {"status": "idle", "incumbent": incumbent, "shares": None, "control": None, "winner": None,
            "live_since": None, "started": None, "excluded": {}, "carveouts": {}, "promo": None,
            "pinned_until": None, "v": 0, "last_eval": None, "note": ""}


class Experiments:
    name = "experiments"

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        st = ctx.store.load_state(ctx.wf, self.name)
        if st is None:
            enabled = ctx.primary == "engagement_rate"
            st = {"enabled": enabled,
                  "reason": "" if enabled else "primary KPI is pickup; engagement is only a guardrail here",
                  "dims": {}}
            for d in DIMS:
                cur = dist(ctx.config, "default", d)
                st["dims"][d] = _fresh(max(cur, key=cur.get))
        self.st = st

    def save(self):
        self.ctx.store.save_state(self.ctx.wf, self.name, self.st)

    # ------------------------------------------------------------ data
    def _default_cohorts(self, dim: str) -> set[str]:
        """Served cohorts whose allocation for `dim` is the default one (so they share one randomisation)."""
        cfg = self.ctx.config
        own = {k for k, v in cfg["allocations"].items() if k != "default" and dim in (v or {})}
        return {"default"} | {c["id"] for c in cfg["cohorts"] if c["id"] not in own}

    def strata(self, dim: str, since: int, by_code: bool = False):
        """{arm: {stratum: (dials, conn, eng)}}. Stratum = (day, block, cohort[, code])."""
        col = COL[dim]
        rows = self.ctx.store.q(
            f"SELECT day, blk, cohort, {'code,' if by_code else ''} {col}, SUM(dials), SUM(conn), SUM(eng) "
            f"FROM agg_var WHERE wf=? AND day >= ? AND day*8+blk >= ? "
            f"GROUP BY day, blk, cohort, {'code,' if by_code else ''} {col}",
            (self.ctx.wf, since // 1440, bucket(since)))
        allowed = self._default_cohorts(dim)
        out: dict = defaultdict(dict)
        for r in rows:
            if r[2] not in allowed:
                continue
            key = tuple(r[:4]) if by_code else tuple(r[:3])
            arm = r[4] if by_code else r[3]
            out[arm][key] = tuple(r[-3:])
        return out

    def strata_between(self, dim: str, since: int, until: int):
        data = self.strata(dim, since)
        ub = bucket(until)
        return {arm: {k: v for k, v in st.items() if k[0] * 8 + k[1] < ub} for arm, st in data.items()}

    @staticmethod
    def compare(data: dict, a: str, b: str, metric: str = "eng", keyfilter=None) -> Comparison:
        """Challenger a vs control b. metric 'eng' = engaged/connected, 'pick' = connected/dials."""
        A, B = data.get(a, {}), data.get(b, {})
        strata = []
        for k in set(A) & set(B):
            if keyfilter and not keyfilter(k):
                continue
            da, ca, ea = A[k]
            db, cb, eb = B[k]
            strata.append((ca, ea, cb, eb) if metric == "eng" else (da, ca, db, cb))
        # arms seen in strata the other never shared still count toward n (reported, not compared)
        return stratified_diff(strata)

    # ------------------------------------------------------------ lifecycle
    def on_supervisor_edit(self, dims: list[str], version: int):
        for d in DIMS:
            if d not in dims:
                continue
            s = self.st["dims"][d]
            cur = dist(self.ctx.config, "default", d)
            prev = s["status"]
            s.update(status="pinned", shares=None, live_since=None, winner=None, control=None,
                     incumbent=max(cur, key=cur.get),
                     pinned_until=self.ctx.now + S.PIN_AFTER_SUPERVISOR_HOURS * 60,
                     note=f"supervisor set {d} to {cur} in v{version}; test ({prev}) stopped")
            s["v"] += 1
            self.ctx.store.decide(self.ctx.wf, "experiment_pinned",
                                  f"{d}: supervisor edit v{version} overrides our test ({prev}). "
                                  f"We adopt {cur} and stay off {d} for {S.PIN_AFTER_SUPERVISOR_HOURS}h.",
                                  {"dim": d, "supervisor_dist": cur, "previous_status": prev}, version=version)
        self.save()

    def observe(self):
        ctx = self.ctx
        if not self.st["enabled"] or ctx.now < 0:
            return
        changed = False
        for d in DIMS:
            s = self.st["dims"][d]
            opts = [o for o in ctx.spec["options"][d]]
            if s["status"] == "pinned" and ctx.now >= (s["pinned_until"] or 0):
                cur = dist(ctx.config, "default", d)
                s.update(status="idle", incumbent=max(cur, key=cur.get), note="pin expired")
                changed = True
            if s["status"] == "rolled_back" and ctx.now >= (s.get("idle_at") or 0):
                s["status"] = "idle"
                changed = True
            if s["status"] == "idle":
                challengers = [o for o in opts if o != s["incumbent"] and o not in s["excluded"]]
                if challengers:
                    k = len(challengers)
                    each = min(S.EXPLORE_SHARE_EACH, S.EXPLORE_SHARE_TOTAL_MAX / k)
                    shares = {o: each for o in challengers}
                    shares[s["incumbent"]] = 1 - each * k
                    s.update(status="exploring", shares={o: round(w, 4) for o, w in shares.items()},
                             live_since=None, started=ctx.now, note="", last_change_kind="explore")
                    s["v"] += 1
                    changed = True
                    ctx.store.decide(ctx.wf, "experiment_start",
                                     f"{d}: test {challengers} at {each:.0%} each vs incumbent {s['incumbent']}",
                                     {"dim": d, "shares": s["shares"], "excluded": s["excluded"]})
                else:
                    s["status"] = "done"
                    changed = True
            # live detection: the intended allocation is what the simulator is actually serving
            if s["shares"] and s["live_since"] is None:
                cur = normalise(dist(ctx.config, "default", d))
                if all(abs(cur.get(o, 0) - w) < 0.01 for o, w in normalise(s["shares"]).items()) and \
                        all(o in s["shares"] for o in cur):
                    s["live_since"] = ctx.now
                    changed = True
                    ctx.store.decide(ctx.wf, "experiment_live", f"{d}: allocation {s['shares']} is live",
                                     {"dim": d, "status": s["status"]}, version=ctx.version)
            if s["live_since"] is not None and (s["last_eval"] is None or ctx.now - s["last_eval"] >= S.ANALYSIS_EVERY_MIN):
                s["last_eval"] = ctx.now
                if s["status"] == "exploring":
                    changed |= self._evaluate_exploration(d, s)
                elif s["status"] == "promoted":
                    changed |= self._evaluate_decay(d, s)
            elif s["status"] == "promoted" and s["live_since"] is None and \
                    (s["last_eval"] is None or ctx.now - s["last_eval"] >= S.ANALYSIS_EVERY_MIN):
                s["last_eval"] = ctx.now
                changed |= self._recheck_pending_promotion(d, s)
        if changed:
            self.save()

    def _evaluate_exploration(self, d: str, s: dict) -> bool:
        ctx = self.ctx
        hours_live = (ctx.now - s["live_since"]) / 60
        data = self.strata(d, s["live_since"])
        inc = s["incumbent"]
        arms = [a for a in s["shares"] if a != inc]
        res = {a: self.compare(data, a, inc) for a in arms}
        changed = False
        # 1. drop losers early: it only reduces exposure
        for a, c in res.items():
            if c.n_a >= S.MIN_CONN_PER_ARM_DROP and c.z <= S.Z_DROP_LOSER:
                s["excluded"][a] = "loser"
                freed = s["shares"].pop(a)
                s["shares"][inc] = round(s["shares"][inc] + freed, 4)
                s["v"] += 1
                s["safety_v"] = s.get("safety_v", 0) + 1
                s["last_change_kind"] = "drop"
                changed = True
                ctx.store.decide(ctx.wf, "experiment_drop_loser",
                                 f"{d}={a} is worse than {inc} (engagement {c.rel:+.1%}, z={c.z:.2f}, "
                                 f"n={c.n_a}); removed from traffic", {"dim": d, "arm": a, "vs": inc, **c.as_dict()})
        if hours_live < S.MIN_LIVE_HOURS_BEFORE_DECIDE:
            return changed
        # 1b. drop arms that are fading: looked clearly good earlier, clearly bad over the last day.
        # Checked once a day (first look after the dialling window closes) on non-overlapping
        # windows; hourly overlapping looks turned noise into "fades" and killed good arms.
        lo, hi = ctx.window
        daily = ctx.hour == hi and ctx.now % 60 < S.TICK_MINUTES
        cut = ctx.now - S.FADE_WINDOW_HOURS * 60
        recent_all = self.strata(d, cut) if daily else {}
        early_all = self.strata_between(d, s["live_since"], cut) if daily else {}
        for a in [x for x in arms if x in s["shares"]] if daily else []:
            r, e = self.compare(recent_all, a, inc), self.compare(early_all, a, inc)
            if not (r.n_a >= S.MIN_CONN_PER_ARM_DROP / 2 and e.n_a >= S.MIN_CONN_PER_ARM_DROP and r.se < 1e9 and e.se < 1e9):
                continue
            tz = (r.diff - e.diff) / (r.se ** 2 + e.se ** 2) ** 0.5
            if e.z >= 1.64 and r.diff < 0 and tz <= S.FADE_Z_DROP:
                s["excluded"][a] = "decayed"
                s["shares"][inc] = round(s["shares"][inc] + s["shares"].pop(a), 4)
                s["v"] += 1
                s["safety_v"] = s.get("safety_v", 0) + 1
                s["last_change_kind"] = "drop"
                changed = True
                ctx.store.decide(ctx.wf, "experiment_drop_fading",
                                 f"{d}={a} was {e.rel:+.1%} vs {inc} earlier but {r.rel:+.1%} in the last "
                                 f"{S.FADE_WINDOW_HOURS}h (trend z={tz:.2f}); removed before it does more harm",
                                 {"dim": d, "arm": a, "recent": r.as_dict(), "earlier": e.as_dict(), "trend_z": tz})
        # 2. a winner?
        recent = self.strata(d, ctx.now - S.DECAY_WINDOW_HOURS * 60)
        cands = []
        for a, c in res.items():
            if a not in s["shares"] or c.n_a < S.MIN_CONN_PER_ARM_PROMOTE or c.z < S.Z_PROMOTE:
                continue
            r = self.compare(recent, a, inc)
            early = self.compare(self.strata_between(d, s["live_since"], ctx.now - S.DECAY_WINDOW_HOURS * 60), a, inc)
            trend = ((r.diff - early.diff) / (r.se ** 2 + early.se ** 2) ** 0.5
                     if early.n_a and r.n_a and r.se < 1e9 and early.se < 1e9 else 0.0)
            if r.n_a and (r.diff < S.RECENT_MIN_FRACTION * c.diff or trend <= S.TREND_Z_HOLD):
                ctx.store.decide(ctx.wf, "experiment_hold",
                                 f"{d}={a} is significant overall (z={c.z:.2f}) but its last-24h lift ({r.rel:+.1%}) is "
                                 f"{'well below its cumulative lift' if r.diff < S.RECENT_MIN_FRACTION * c.diff else 'significantly below the earlier period'} "
                                 f"(trend z={trend:.2f}); not promoting a fading winner",
                                 {"dim": d, "arm": a, "overall": c.as_dict(), "recent": r.as_dict(),
                                  "earlier": early.as_dict(), "trend_z": trend})
                continue
            gname, gmax = ctx.guardrail
            if gname == "pickup_rate":
                g = self.compare(data, a, inc, metric="pick")
                if g.rel < -gmax and g.z <= -1.28:
                    ctx.store.decide(ctx.wf, "experiment_guardrail_veto",
                                     f"{d}={a} lifts engagement {c.rel:+.1%} but pickup {g.rel:+.1%} breaches the "
                                     f"{gmax:.0%} guardrail", {"dim": d, "arm": a, "primary": c.as_dict(),
                                                               "guardrail": g.as_dict()})
                    continue
            cands.append((c.diff, a, c, r))
        if cands:
            _, a, c, r = max(cands)
            carve = self._segment_harm(d, a, inc, s["live_since"])
            s["explore_shares"] = dict(s["shares"])
            s.update(status="promoted", winner=a, control=inc,
                     shares={a: round(1 - S.HOLDOUT_SHARE, 4), inc: S.HOLDOUT_SHARE},
                     live_since=None, promo={**c.as_dict(), "at": ctx.now, "recent": r.as_dict()},
                     carveouts={co.id: {"where": co.where, "arm": inc, "why": why} for co, why in carve})
            for o in list(res):
                if o != a and o not in s["excluded"]:
                    s["excluded"][o] = "not_selected"
            s["v"] += 1
            s["last_change_kind"] = "promote"
            ctx.store.decide(ctx.wf, "experiment_promote",
                             f"{d}: promote {a} over {inc} (engagement {c.rel:+.1%}, z={c.z:.2f}, n={c.n_a}/{c.n_b}); "
                             f"keep {S.HOLDOUT_SHARE:.0%} on {inc} as control"
                             + (f"; carve out {[co.label() for co, _ in carve]}" if carve else ""),
                             {"dim": d, "winner": a, "control": inc, "overall": c.as_dict(), "recent": r.as_dict(),
                              "carveouts": [{"cohort": co.id, "why": why} for co, why in carve],
                              "threshold_z": S.Z_PROMOTE})
            return True
        if (ctx.now - s["started"]) / 1440 >= S.MAX_EXPLORE_DAYS:
            # inconclusive: stop the arms that aren't even pointing the right way, keep the promising ones
            weak = [a for a in arms if a in s["shares"] and res[a].diff <= 0]
            if weak and len(weak) < len([a for a in arms if a in s["shares"]]):
                for a in weak:
                    s["excluded"][a] = "inconclusive"
                    s["shares"][inc] = round(s["shares"][inc] + s["shares"].pop(a), 4)
                s["v"] += 1
                s["safety_v"] = s.get("safety_v", 0) + 1
                s["last_change_kind"] = "drop"
                ctx.store.decide(ctx.wf, "experiment_trim",
                                 f"{d}: after {S.MAX_EXPLORE_DAYS} days {weak} show no lift; stopped. Still testing "
                                 f"{[a for a in s['shares'] if a != inc]}",
                                 {"dim": d, "results": {a: c.as_dict() for a, c in res.items()}})
                return True
        if all(a not in s["shares"] for a in arms) or ((ctx.now - s["started"]) / 1440 >= S.MAX_EXPLORE_DAYS and
                                                       all(res[a].diff <= 0 for a in arms if a in s["shares"])):
            s.update(status="done", shares={inc: 1.0}, live_since=None, note="inconclusive")
            s["v"] += 1
            s["safety_v"] = s.get("safety_v", 0) + 1
            s["last_change_kind"] = "stop"
            ctx.store.decide(ctx.wf, "experiment_stop",
                             f"{d}: no challenger beat {inc} after {(ctx.now - s['started']) / 1440:.1f} days; "
                             f"back to 100% {inc}", {"dim": d, "results": {a: c.as_dict() for a, c in res.items()}})
            return True
        return changed

    def _recheck_pending_promotion(self, d: str, s: dict) -> bool:
        """A promotion waiting on the approver can go stale: re-check that the winner is still winning.

        If its last-24h lift has faded, go back to exploring. That also bumps the safety version, so
        governance withdraws the approved-but-unapplied request instead of applying a faded winner."""
        ctx = self.ctx
        if ctx.now - s["promo"]["at"] < S.ANALYSIS_EVERY_MIN:
            return False
        w, c0 = s["winner"], s["control"]
        r = self.compare(self.strata(d, ctx.now - S.DECAY_WINDOW_HOURS * 60), w, c0)
        if not r.n_a or r.diff >= S.RECENT_MIN_FRACTION * s["promo"]["diff"]:
            return False
        shares = s.get("explore_shares") or {c0: 1.0}
        s.update(status="exploring", shares=shares, winner=None, control=None, incumbent=c0, carveouts={},
                 live_since=None, last_change_kind="drop", promo=None)
        s["excluded"] = {a: why for a, why in s["excluded"].items() if why != "not_selected"}
        s["v"] += 1
        s["safety_v"] = s.get("safety_v", 0) + 1
        ctx.store.decide(ctx.wf, "experiment_promotion_withdrawn",
                         f"{d}: {w} was approved for promotion but its last-24h lift is now {r.rel:+.1%} vs {c0}; "
                         f"not applying it, back to the test", {"dim": d, "arm": w, "recent": r.as_dict()})
        return True

    def _segment_harm(self, d: str, winner: str, control: str, since: int) -> list[tuple[Cohort, dict]]:
        """Single-feature segments where the winner is significantly worse than control: keep them on control."""
        ctx = self.ctx
        data = self.strata(d, since, by_code=True)
        codes = {k[3] for arm in data.values() for k in arm}
        found = []
        for seg in segments(ctx.vocab, codes):
            match = {code for code in codes if seg.matches_code(code, ctx.vocab)}
            if not match or len(match) == len(codes):
                continue
            c = self.compare(data, winner, control, keyfilter=lambda k, m=match: k[3] in m)
            if c.n_a >= S.SEGMENT_MIN_CONN and c.z <= S.SEGMENT_HARM_Z and c.rel <= -S.SEGMENT_HARM_MIN_REL:
                found.append((c.z, seg, c))
        found.sort(key=lambda x: x[0])
        return [(seg, c.as_dict()) for _, seg, c in found[: S.MAX_CARVEOUTS_PER_DIM]]

    def _evaluate_decay(self, d: str, s: dict) -> bool:
        """Has the promoted winner stopped winning?

        The reference is the winner's lift measured AFTER promotion (excluding the last window),
        not the lift we promoted on: an arm is promoted the moment it crosses the threshold, so
        the promotion estimate is biased upward (winner's curse). Comparing against it would
        "detect decay" in every true but smaller winner."""
        ctx = self.ctx
        w, c0 = s["winner"], s["control"]
        cut = max(s["live_since"], ctx.now - S.DECAY_WINDOW_HOURS * 60)
        r = self.compare(self.strata(d, cut), w, c0)
        if r.n_b < S.DECAY_MIN_CONTROL_CONN:
            return False
        if (ctx.now - s["live_since"]) / 60 < S.DECAY_MIN_LIVE_HOURS and r.z > S.DECAY_Z_CLEARLY_WORSE:
            return False   # too early to call decay unless the winner is clearly worse right now
        p = s["promo"]
        e = self.compare(self.strata_between(d, s["live_since"], cut), w, c0)
        if e.n_b >= S.DECAY_MIN_CONTROL_CONN and e.se < 1e9:
            ref_diff, ref_se, ref = e.diff, e.se, "post-promotion"
        else:   # not enough post-promotion history yet: shrink the promotion estimate instead
            ref_diff, ref_se, ref = p["diff"] - 1.5 * p["se"], p["se"], "promotion (shrunk)"
        z_drop = (r.diff - ref_diff) / math.sqrt(r.se ** 2 + ref_se ** 2) if r.se < 1e9 else 0.0
        # all post-promotion data (grows, so it gains power) vs the shrunk promotion estimate:
        # catches a winner that had ALREADY faded by the time it was promoted
        post = self.compare(self.strata(d, s["live_since"]), w, c0)
        shrunk = p["diff"] - 1.5 * p["se"]
        z_post_drop = ((post.diff - shrunk) / math.sqrt(post.se ** 2 + p["se"] ** 2)
                       if post.se < 1e9 and post.n_b >= S.DECAY_MIN_CONTROL_CONN else 0.0)
        s["last_decay_check"] = {"at": ctx.now, **r.as_dict(), "ref": ref, "ref_diff": round(ref_diff, 4),
                                 "z_drop": round(z_drop, 3), "post": post.as_dict(), "z_post_drop": round(z_post_drop, 3)}
        lost_recent = z_drop <= S.DECAY_Z_DROP_FROM_PROMO and ref_diff > 0 and r.diff <= S.DECAY_KEEP_FRACTION * ref_diff
        lost_post = (post.z <= S.DECAY_POST_Z_WORSE or
                     (z_post_drop <= S.DECAY_Z_DROP_FROM_PROMO and shrunk > 0 and post.diff <= S.DECAY_KEEP_FRACTION * shrunk))
        if r.z <= S.DECAY_Z_WORSE or lost_recent or lost_post:
            if lost_post and not lost_recent and r.z > S.DECAY_Z_WORSE:
                ref, ref_diff, z_drop, r = "post-promotion (all) vs shrunk promotion", shrunk, z_post_drop, post
            s["excluded"] = {a: why for a, why in s["excluded"].items() if why != "not_selected"}
            s["excluded"][w] = "decayed"
            s.update(status="rolled_back", shares={c0: 1.0}, incumbent=c0, winner=None, control=None,
                     live_since=None, carveouts={}, idle_at=ctx.now + 24 * 60)
            s["v"] += 1
            s["safety_v"] = s.get("safety_v", 0) + 1
            s["last_change_kind"] = "rollback"
            ctx.store.decide(ctx.wf, "experiment_rollback",
                             f"{d}: {w} stopped winning. Last {S.DECAY_WINDOW_HOURS}h engagement {r.rel:+.1%} vs "
                             f"control {c0} (z={r.z:.2f}); {ref} lift was {ref_diff:+.3f} (drop z={z_drop:.2f}). "
                             f"Rolling back to {c0}.",
                             {"dim": d, "winner": w, "control": c0, "recent": r.as_dict(), "reference": ref,
                              "reference_diff": ref_diff, "post_promotion": e.as_dict(), "promo": p,
                              "z_drop": z_drop})
            return True
        return False

    # ------------------------------------------------------------ outputs
    def carveout_cohorts(self) -> list[Cohort]:
        out = []
        for d in DIMS:
            for cid, co in self.st["dims"][d]["carveouts"].items():
                out.append(Cohort(cid, co["where"], "carveout"))
        return out

    def intents(self) -> list[Intent]:
        if not self.st["enabled"]:
            return []
        out = []
        for d in DIMS:
            s = self.st["dims"][d]
            if s["status"] in ("pinned", "disabled") or not s["shares"]:
                continue
            shares = dict(s["shares"])
            carve = {cid: co["arm"] for cid, co in s["carveouts"].items()}

            def apply(cfg, d=d, shares=shares, carve=carve):
                al = cfg["allocations"]
                al.setdefault("default", {})[d] = dict(shares)
                # this module owns `d` on managed cohorts: clear, then set the current carve-outs
                for k in list(al):
                    if k.startswith(MANAGED_PREFIX) and d in (al[k] or {}):
                        del al[k][d]
                        if not al[k]:
                            del al[k]
                present = {c["id"] for c in cfg["cohorts"]}
                for cid, arm in carve.items():
                    if cid in present:
                        al.setdefault(cid, {})[d] = {arm: 1.0}

            kind = s.get("last_change_kind") or "explore"
            # risk-reducing = only shrinks exposure to non-incumbent arms. The planner re-checks this
            # against the live config (no new arm, only the safe arm gains share) before any auto-apply.
            risk_reducing = kind in ("drop", "rollback", "stop")
            gain = 0.0
            if s["status"] == "promoted" and s["promo"]:
                gain = s["promo"]["rel_lift"] * (1 - S.HOLDOUT_SHARE)
            out.append(Intent(
                module=self.name, dims=[d], apply=apply, version=s["v"],
                summary=self._summary(d, s, kind), rationale=self._rationale(d, s, kind),
                priority="rollback" if risk_reducing else "routine",
                material=True, auto_ok=risk_reducing, safety=risk_reducing,
                expected_rel_gain=gain, dry_run=self._dry_run(d, s, kind),
                impact=0.6 if risk_reducing else (0.08 if kind == "explore" else None),
                safe_arm=s["incumbent"] if risk_reducing else None))
        return out

    def _summary(self, d, s, kind) -> str:
        if kind == "explore":
            ch = [a for a in s["shares"] if a != s["incumbent"]]
            return f"test {d} {'/'.join(ch)} at {s['shares'][ch[0]]:.0%} each vs {s['incumbent']}" if ch else f"{d} test"
        if kind == "promote" or s["status"] == "promoted":
            return f"promote {d} {s['winner']} ({s['promo']['rel_lift']:+.1%} engagement), keep {S.HOLDOUT_SHARE:.0%} control"
        if kind == "drop":
            gone = [f"{a} ({r})" for a, r in s["excluded"].items() if a not in s["shares"] and r != "not_selected"]
            return f"stop {d} arm(s) {', '.join(gone)}; still testing {[a for a in s['shares'] if a != s['incumbent']]}"
        if kind == "rollback":
            return f"roll back {d} to {s['incumbent']} (winner decayed)"
        return f"{d}: back to {s['incumbent']}"

    def _rationale(self, d, s, kind) -> str:
        if kind == "explore":
            return (f"{d} has never been tested on this workflow's traffic. We split a bounded share across "
                    f"challengers; any arm significantly worse than {s['incumbent']} is removed automatically, "
                    f"and a winner needs z>={S.Z_PROMOTE} on >= {S.MIN_CONN_PER_ARM_PROMOTE} connected calls.")
        if s["status"] == "promoted":
            p = s["promo"]
            return (f"{s['winner']} beat {s['control']} on engagement by {p['rel_lift']:+.1%} (z={p['z']:.2f}, "
                    f"n={p['n_a']} vs {p['n_b']} connected calls, stratified by time block and cohort). "
                    f"The last 24h agree ({p['recent']['rel_lift']:+.1%}). "
                    f"{S.HOLDOUT_SHARE:.0%} stays on {s['control']} so we notice if the lift decays.")
        return s.get("note") or f"Risk-reducing change on {d}."

    def _dry_run(self, d, s, kind) -> dict:
        ctx = self.ctx
        since = s["live_since"] if s["live_since"] is not None else ctx.now - 7 * 1440
        data = self.strata(d, since)
        rows = {a: [sum(v[1] for v in st.values()), sum(v[2] for v in st.values())] for a, st in data.items()}
        obs = {a: {"connected": c, "engaged": e, "engagement": round(e / c, 4) if c else None}
               for a, (c, e) in rows.items()}
        out = {"method": "replay of observed calls since the current allocation went live "
                         "(or last 7 days), per arm actually served",
               "observed": obs, "proposed_shares": s["shares"]}
        tot_c = sum(v[0] for v in rows.values())
        if tot_c:
            cur = normalise(dist(ctx.config, "default", d))
            base = sum(v[1] for v in rows.values()) / tot_c

            def exp(shares):
                return sum(w * (rows[a][1] / rows[a][0] if a in rows and rows[a][0] >= 50 else base)
                           for a, w in normalise(shares).items())
            out["expected_engagement_current_alloc"] = round(exp(cur), 4)
            out["expected_engagement_proposed_alloc"] = round(exp(s["shares"]), 4)
        if kind == "explore":
            untested = sum(w for a, w in s["shares"].items() if a != s["incumbent"])
            out["downside_bound"] = (f"if every challenger were 10% worse, workflow engagement drops "
                                     f"{untested * 0.10:.1%} until losers are removed")
        return out
