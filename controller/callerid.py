"""Caller-ID health: detect a number going bad, rotate away, spread volume, and probe unknown numbers safely.

- Health = a number's pickup relative to the OTHER numbers on the same workflow, in the same
  (day, hour) strata. Numbers are randomised per dial, so this comparison isn't confounded by
  who is being called or when.
- Quarantine needs both a large effect (< 65% of the others' pickup) and strong evidence (z <= -3
  on >= 250 dials in the last 6 dialling hours). Load-driven differences between numbers (a
  fresh number vs one carrying the volume) are typically 10-30% and must not trip it.
- Target allocation: spread evenly over healthy numbers. Per-number volume is what carriers'
  spam labelling reacts to, so minimising the busiest number's share is the robust default.
  A number we have no evidence on gets at most DID_PROBE_SHARE until it has a track record.
- Quarantine is sticky for the run. See SUBMISSION section 13 for why we don't re-probe.
"""
from __future__ import annotations

from . import settings as S
from .configlib import dist
from .context import Ctx, Intent
from .stats import normalise, round_dist, stratified_diff, tv_distance


class CallerId:
    name = "callerid"

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.st = ctx.store.load_state(ctx.wf, self.name) or {
            "quarantined": {}, "target": None, "v": 0, "safety_v": 0, "pinned_until": None,
            "last_eval": None, "health": {}}

    def save(self):
        self.ctx.store.save_state(self.ctx.wf, self.name, self.st)

    def on_supervisor_edit(self, dims: list[str], version: int):
        if "caller_id" in dims:
            self.st["pinned_until"] = self.ctx.now + S.PIN_AFTER_SUPERVISOR_HOURS * 60
            self.st["target"] = None
            self.st["v"] += 1
            self.ctx.store.decide(self.ctx.wf, "callerid_pinned",
                                  f"supervisor changed caller IDs in v{version}; we stay off caller_id for "
                                  f"{S.PIN_AFTER_SUPERVISOR_HOURS}h", {"supervisor_dist": dist(self.ctx.config, 'default', 'caller_id')},
                                  version=version)
            self.save()

    def _window_rows(self):
        """(day, hour, did, dials, conn) over the last 3 days of dialling hours, newest first."""
        ctx = self.ctx
        return ctx.store.q("SELECT day, hour, did, SUM(dials), SUM(conn) FROM agg_hour WHERE wf=? AND day >= ? "
                           "AND day*24+hour <= ? GROUP BY day, hour, did ORDER BY day DESC, hour DESC",
                           (ctx.wf, ctx.now // 1440 - 2, ctx.now // 60))

    @staticmethod
    def _recent_hours_for(d: str, rows) -> set:
        """The most recent dialling hours that give number `d` enough evidence: at least
        DID_WINDOW_HOURS, extended (up to 3 days) until it has 2x DID_MIN_DIALS dials. A number on a
        10% probe share needs a longer window than one carrying a third of the volume."""
        hours, n = [], 0
        for day, hour, did, dials, _ in rows:
            if did != d:
                continue
            hours.append((day, hour))
            n += dials
            if len(hours) >= S.DID_WINDOW_HOURS and n >= 2 * S.DID_MIN_DIALS:
                break
        return set(hours)

    def _own_baseline(self, d: str, rows) -> tuple[float | None, float]:
        """This number's pickup now vs its own pickup in the same hours over the previous 3 days.

        Guards against quarantining a number just because it carries more volume than a fresh one:
        a number that is as good as it has been is not 'going bad', whatever the others look like."""
        ctx = self.ctx
        mine = self._recent_hours_for(d, rows)
        if not mine:
            return None, 0.0
        cur = {}
        for day, hour, did, n, x in rows:
            if did == d and (day, hour) in mine:
                a = cur.setdefault(hour, [0, 0])
                a[0] += n; a[1] += x
        lo_day = min(day for day, _ in mine)
        base = ctx.store.q("SELECT hour, SUM(dials), SUM(conn) FROM agg_hour WHERE wf=? AND did=? AND day >= ? AND day < ? "
                           "GROUP BY hour", (ctx.wf, d, lo_day - 3, lo_day))
        bmap = {h: (n, x) for h, n, x in base}
        strata = [(cur[h][0], cur[h][1], bmap[h][0], bmap[h][1]) for h in cur if h in bmap]
        if sum(s[2] for s in strata) < 2 * S.DID_MIN_DIALS:
            return None, 0.0
        c = stratified_diff(strata)
        return (c.p_a / c.p_b if c.p_b else None), c.z

    def observe(self):
        ctx = self.ctx
        st = self.st
        if st["pinned_until"] and ctx.now < st["pinned_until"]:
            return
        if st["last_eval"] is not None and ctx.now - st["last_eval"] < S.DID_EVAL_EVERY_MIN:
            return
        if ctx.now >= 0 and not ctx.in_dialling() and st["target"] is not None:
            return  # nothing new to learn outside the window
        st["last_eval"] = ctx.now
        dids = list(ctx.spec["options"]["caller_id"])
        rows = self._window_rows()
        by = {}
        for day, hour, did, n, x in rows:
            by.setdefault((day, hour), {})[did] = (n, x)
        changed = False
        health = {}
        for d in dids:
            if d in st["quarantined"]:
                continue
            strata = []
            mine = self._recent_hours_for(d, rows)
            for key, cell in by.items():
                if d not in cell or key not in mine:
                    continue
                n1, x1 = cell[d]
                n2 = sum(v[0] for k, v in cell.items() if k != d and k not in st["quarantined"])
                x2 = sum(v[1] for k, v in cell.items() if k != d and k not in st["quarantined"])
                strata.append((n1, x1, n2, x2))
            c = stratified_diff(strata)
            ratio = (c.p_a / c.p_b) if c.n_a and c.n_b and c.p_b > 0 else None
            health[d] = {"dials": c.n_a, "pickup": round(c.p_a, 4) if c.n_a else None,
                         "others_pickup": round(c.p_b, 4) if c.n_b else None,
                         "ratio": round(ratio, 3) if ratio else None, "z": round(c.z, 2)}
            if not (c.n_a >= S.DID_MIN_DIALS and ratio is not None and ratio < S.DID_QUARANTINE_RATIO
                    and c.z <= S.DID_QUARANTINE_Z and ctx.now >= 0):
                continue
            own, z_own = self._own_baseline(d, rows)
            health[d]["own_ratio"] = round(own, 3) if own else None
            if own is not None and not (own < S.DID_OWN_DROP_RATIO and z_own <= S.DID_OWN_DROP_Z):
                continue  # as good as it has been: the others are just fresher
            st["quarantined"][d] = {"at": ctx.now, **health[d]}
            st["safety_v"] += 1
            changed = True
            ctx.store.decide(ctx.wf, "callerid_quarantine",
                             f"caller ID {d} pickup {c.p_a:.1%} vs {c.p_b:.1%} on the other numbers "
                             f"(ratio {ratio:.2f}, z={c.z:.1f}, {c.n_a} dials in the last {S.DID_WINDOW_HOURS} "
                             f"dialling hours" + (f"; {own:.2f}x its own recent pickup" if own else
                                                  "; no track record of its own") + "): rotate it out",
                             {"did": d, **health[d]})
            ctx.store.alert(ctx.wf, "ticket", f"did_quarantine:{ctx.wf}:{d}",
                            f"{ctx.wf}: caller ID {d} quarantined (pickup ratio {ratio:.2f}). Traffic moved to "
                            f"the other numbers automatically or via an urgent request.",
                            "Check the number's reputation with the carrier. Re-enable by clearing the "
                            "quarantine in module_state once it is cleaned.")
        st["health"] = health
        target = self._target(dids)
        if target is not None:
            old = st["target"]
            if old is None or tv_distance(old, target) >= S.DID_MIN_CHANGE_TV or changed:
                st["target"] = target
                st["v"] += 1
                changed = True
                ctx.store.decide(ctx.wf, "callerid_target", f"caller-ID target {target}",
                                 {"target": target, "previous": old, "health": health,
                                  "quarantined": list(st["quarantined"])})
        if changed or health:
            self.save()

    def _target(self, dids: list[str]) -> dict | None:
        ctx = self.ctx
        if ctx.now < 0:
            return None
        healthy = [d for d in dids if d not in self.st["quarantined"]]
        if not healthy:
            return None
        totals = dict(ctx.store.q("SELECT did, SUM(dials) FROM agg_hour WHERE wf=? AND day >= ? GROUP BY did",
                                  (ctx.wf, ctx.day - 7)))
        # a number graduates from probe share only with volume AND no sign of being worse than the rest
        hl = self.st.get("health", {})

        def looks_ok(d):
            h = hl.get(d) or {}
            if (h.get("dials") or 0) < S.DID_MIN_DIALS:
                return False   # not enough evidence yet: stay on probe share
            if h.get("others_pickup") is None:
                return True    # the only number carrying traffic is its own reference
            if h.get("ratio") is None:
                return False
            return h["ratio"] >= S.DID_GRADUATE_RATIO or h.get("z", 0) > -1.0
        grad = set(self.st.setdefault("graduated", []))
        for d in healthy:
            if d not in grad and totals.get(d, 0) >= S.DID_MIN_DIALS and looks_ok(d):
                grad.add(d)   # hysteresis: once graduated, a number keeps its full share unless quarantined
        self.st["graduated"] = sorted(grad)
        known = [d for d in healthy if d in grad]
        unknown = [d for d in healthy if d not in known]
        eq = 1.0 / len(healthy)
        if not known:
            return {d: round(eq, 4) for d in unknown}
        # where every caller-ID step costs an approval request, probe bigger: fewer round trips
        pol = ctx.gov.get("auto_apply") or {}
        auto = pol.get("enabled") and "caller_id" in (pol.get("allowed_dims") or [])
        probe = min(eq, S.DID_PROBE_SHARE if auto else S.DID_PROBE_SHARE_APPROVAL)
        rest = (1.0 - probe * len(unknown)) / len(known)
        t = {d: rest for d in known}
        t.update({d: probe for d in unknown})
        return round_dist(normalise(t))

    def intents(self) -> list[Intent]:
        st, ctx = self.st, self.ctx
        if not st["target"] or (st["pinned_until"] and ctx.now < st["pinned_until"]):
            return []
        target = dict(st["target"])
        cur = normalise(dist(ctx.config, "default", "caller_id"))
        on_bad = sum(cur.get(d, 0) for d in st["quarantined"])
        tv = tv_distance(cur, target)
        if tv < S.DID_MIN_CHANGE_TV and not on_bad:
            return []

        def apply(cfg):
            cfg["allocations"].setdefault("default", {})["caller_id"] = dict(target)

        health = st.get("health", {})
        pick = {d: h.get("pickup") for d, h in health.items()}
        known = [p for p in pick.values() if p]
        mean = sum(known) / len(known) if known else None
        exp_cur = sum(w * (pick.get(d) or mean or 0) for d, w in cur.items()) if mean else None
        exp_new = sum(w * (pick.get(d) or mean or 0) for d, w in target.items()) if mean else None
        gain = (exp_new / exp_cur - 1) if exp_cur and exp_new else 0.0
        q = list(st["quarantined"])
        return [Intent(
            module=self.name, dims=["caller_id"], apply=apply, version=st["v"],
            summary=(f"rotate out caller ID {', '.join(q)}; " if on_bad else "") + f"caller IDs -> {target}",
            rationale=("A caller ID's pickup collapsed relative to the other numbers; moving its volume away. "
                       if on_bad else "") +
                      "Volume is spread evenly over healthy numbers to keep per-number load (and spam "
                      "labelling) low; numbers without a track record get at most "
                      f"{S.DID_PROBE_SHARE:.0%} until they have one.",
            priority="incident" if on_bad else "routine", material=bool(on_bad) or tv >= 0.1,
            auto_ok=True, safety=bool(on_bad), expected_rel_gain=gain if self.ctx.primary == "pickup_rate" else 0.0,
            impact=0.7 if on_bad else None,
            dry_run={"method": "per-number pickup over the last dialling hours, stratified by hour; "
                               "unknown numbers assumed at the mean (they are probes)",
                     "health": health, "current": cur, "proposed": target,
                     "expected_pickup_current": round(exp_cur, 4) if exp_cur else None,
                     "expected_pickup_proposed": round(exp_new, 4) if exp_new else None})]
