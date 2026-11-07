"""Call-time optimisation per cohort: call each kind of person in the hours they actually pick up.

The business owns how many calls happen per hour. We only change *who* is picked in a given hour
(schedule.hour_weights per cohort).

1. Estimate pickup per (cohort, hour) from history + live data, shrunk toward the cohort's
   flat profile (SCHED_SHRINK_K pseudo-dials).
2. Weight w_c(h) = (cohort's relative hour shape / population's hour shape) ^ gamma, normalised
   per cohort. This moves a cohort's calls toward its good hours WITHOUT systematically
   favouring easy-to-reach cohorts (which would quietly starve hard-to-reach people of attempts).
3. Dry run: hold each cohort's daily dials and each hour's capacity fixed (Sinkhorn/IPF on the
   cohort x hour table), and predict pickup with weights fitted on one half of the days and
   evaluated on the other half (2-fold). The partition and gamma with the best out-of-sample gain win,
   and only if it is >= SCHED_MIN_GAIN.

Per-person preferred hours (lead_preferred_hour) are deliberately not used. A person has
1-3 historical connects; one connect at 11:00 is an anecdote, not a preference.
"""
from __future__ import annotations

from collections import defaultdict

from . import settings as S
from .cohorts import Cohort, partition
from .context import Ctx, Intent

PARTITIONS = (("age_band",), ("city_tier",), ("age_band", "city_tier"), ("lang",), ("value_band",),
              ("age_band", "value_band"))


def sinkhorn(base: dict, rows: dict, cols: dict, iters: int = 60) -> dict:
    """Scale base[(c,h)] so row sums match rows[c] and column sums match cols[h]."""
    q = dict(base)
    for _ in range(iters):
        rs = defaultdict(float)
        for (c, h), v in q.items():
            rs[c] += v
        q = {(c, h): v * (rows[c] / rs[c] if rs[c] else 0) for (c, h), v in q.items()}
        cs = defaultdict(float)
        for (c, h), v in q.items():
            cs[h] += v
        q = {(c, h): v * (cols[h] / cs[h] if cs[h] else 0) for (c, h), v in q.items()}
    return q


class Schedule:
    name = "schedule"

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.st = ctx.store.load_state(ctx.wf, self.name) or {
            "status": "none", "partition": None, "cohorts": [], "tables": {}, "gamma": None, "gain": 0.0,
            "v": 0, "computed_day": None, "dry_run": {}, "pinned_until": None}

    def save(self):
        self.ctx.store.save_state(self.ctx.wf, self.name, self.st)

    def on_supervisor_edit(self, dims: list[str], version: int):
        if "schedule" in dims or "cohorts" in dims:
            self.st["pinned_until"] = self.ctx.now + S.PIN_AFTER_SUPERVISOR_HOURS * 60
            self.ctx.store.decide(self.ctx.wf, "schedule_pinned",
                                  f"supervisor changed {dims} in v{version}; schedule changes paused "
                                  f"{S.PIN_AFTER_SUPERVISOR_HOURS}h", {}, version=version)
            self.save()

    # ------------------------------------------------------------ estimation
    def _cells(self) -> dict[int, list]:
        """{day parity: [(hour, code, dials, conn)]} over the last 21 days, inside the dialling window."""
        ctx = self.ctx
        rows = ctx.store.q("SELECT day % 2, hour, code, SUM(dials), SUM(conn), SUM(eng) FROM agg_hour WHERE wf=? "
                           "AND day >= ? GROUP BY day % 2, hour, code", (ctx.wf, ctx.day - 21))
        lo, hi = ctx.window
        out: dict[int, list] = {0: [], 1: []}
        for par, h, code, n, x, e in rows:
            if lo <= h < hi and code != 0xFFFF:
                out[abs(par)].append((h, code, n, x, e))
        return out

    @staticmethod
    def _all(cells: dict[int, list]) -> list:
        return cells[0] + cells[1]

    def _fit(self, cells, cohorts: list[Cohort], gamma: float):
        """Return (weights {cid: {h: w}}, phat {(cid,h): p}, dials {(cid,h): n}, engagement {cid: e})."""
        vocab = self.ctx.vocab
        cmap = {}
        n = defaultdict(float)
        x = defaultdict(float)
        eng = defaultdict(float)
        for h, code, nn, xx, ee in cells:
            cid = cmap.get(code)
            if cid is None:
                cid = next((c.id for c in cohorts if c.matches_code(code, vocab)), "default")
                cmap[code] = cid
            n[(cid, h)] += nn
            x[(cid, h)] += xx
            eng[cid] += ee
        hours = sorted({h for _, h in n})
        cids = sorted({c for c, _ in n})
        tot_n, tot_x = sum(n.values()), sum(x.values())
        if not tot_n:
            return {}, {}, {}, {}
        p_all = tot_x / tot_n
        e_all = sum(eng.values()) / tot_x if tot_x else 0.5
        erate = {c: (eng[c] + 50 * e_all) / (sum(x[(c, h)] for h in hours) + 50) for c in cids}
        m = {h: (sum(x[(c, h)] for c in cids) / max(sum(n[(c, h)] for c in cids), 1)) / p_all for h in hours}
        weights, phat = {}, {}
        lo_c, hi_c = S.SCHED_WEIGHT_CLIP
        for c in cids:
            nc = sum(n[(c, h)] for h in hours)
            pc = sum(x[(c, h)] for h in hours) / nc if nc else p_all
            w = {}
            for h in hours:
                ph = (x[(c, h)] + S.SCHED_SHRINK_K * pc * m[h]) / (n[(c, h)] + S.SCHED_SHRINK_K)
                phat[(c, h)] = ph
                rel = (ph / pc) / m[h] if pc > 0 and m[h] > 0 else 1.0
                w[h] = min(max(rel ** gamma, lo_c), hi_c)
            mean = sum(w[h] * n[(c, h)] for h in hours) / nc if nc else 1.0
            weights[c] = {h: w[h] / mean for h in hours}
        return weights, phat, dict(n), erate

    def _predict(self, weights, phat_eval, n_eval, e_eval) -> tuple[float, float, float, float]:
        """(pickup now, pickup under `weights`, engagement now, engagement under `weights`) on evaluation cells.

        Engagement moves too: changing who connects changes the mix of connected people. That is
        how a pickup win can quietly breach an engagement guardrail, so the dry run predicts it."""
        rows, cols = defaultdict(float), defaultdict(float)
        for (c, h), v in n_eval.items():
            rows[c] += v
            cols[h] += v

        def kpis(q):
            conn = sum(v * phat_eval.get(k, 0) for k, v in q.items())
            eng = sum(v * phat_eval.get(k, 0) * e_eval.get(k[0], 0) for k, v in q.items())
            return conn / max(sum(q.values()), 1), eng / conn if conn else 0.0
        bp, be = kpis(n_eval)
        start = {(c, h): v * weights.get(c, {}).get(h, 1.0) for (c, h), v in n_eval.items()}
        np_, ne = kpis(sinkhorn(start, rows, cols))
        return bp, np_, be, ne

    def _cv_gain(self, cells, cohorts, gamma) -> tuple[float, float]:
        """Out-of-sample (pickup gain, engagement change), both relative, averaged over the 2 folds."""
        if not cells[0] or not cells[1]:
            return 0.0, 0.0
        gp, ge = [], []
        for parity in (0, 1):
            fit, ev = cells[parity], cells[1 - parity]
            w, _, _, _ = self._fit(fit, cohorts, gamma)
            _, ph_ev, n_ev, e_ev = self._fit(ev, cohorts, 0.0)
            bp, np_, be, ne = self._predict(w, ph_ev, n_ev, e_ev)
            gp.append(np_ / bp - 1 if bp else 0.0)
            ge.append(ne / be - 1 if be else 0.0)
        return sum(gp) / 2, sum(ge) / 2

    def _eng_limit(self) -> float:
        """How much (relative) engagement a schedule change may cost, by objective."""
        name, mx = self.ctx.guardrail
        if self.ctx.primary == "pickup_rate" and name == "engagement_rate":
            return 0.5 * mx          # half the guardrail: leave room for noise and other changes
        return 0.002                 # engagement is primary: the schedule must not trade it away

    # ------------------------------------------------------------ lifecycle
    def observe(self, extra_cohorts: list[Cohort]):
        ctx, st = self.ctx, self.st
        if ctx.now < 0 or st["status"] == "disabled":
            return
        if st["pinned_until"] and ctx.now < st["pinned_until"]:
            return
        # (re)compute once per day, before the dialling window opens
        if st["computed_day"] == ctx.day:
            self._ensure_tables(extra_cohorts)
            return
        if st["computed_day"] is not None and ctx.hour < 6:
            return
        cells = self._cells()
        codes = {c[1] for c in self._all(cells)}
        best = (0.0, None, None, None)
        trials = []
        limit = self._eng_limit()
        eng_of = {}
        for feats in PARTITIONS:
            cohorts = partition(feats, ctx.vocab, codes)
            if len(cohorts) < 2 or len(cohorts) > S.SCHED_MAX_COHORTS:
                continue
            for g in S.SCHED_GAMMAS[1:]:
                gain, de = self._cv_gain(cells, cohorts, g)
                ok = de >= -limit
                trials.append({"partition": list(feats), "gamma": g, "cv_pickup_gain": round(gain, 4),
                               "cv_engagement_change": round(de, 4), "within_engagement_limit": ok})
                eng_of[(feats, g)] = de
                if not ok:
                    continue
                # prefer fewer cohorts unless the gain is clearly better
                if gain > best[0] + 0.002 or (best[1] is not None and abs(gain - best[0]) <= 0.002
                                             and len(cohorts) < len(best[2])):
                    best = (gain, feats, cohorts, g)
        st["computed_day"] = ctx.day
        gain, feats, cohorts, g = best
        current_gain = st["gain"] if st["status"] == "active" else 0.0
        if feats is None or gain < S.SCHED_MIN_GAIN:
            ctx.store.decide(ctx.wf, "schedule_skip",
                             f"no call-time reweighting predicts >= {S.SCHED_MIN_GAIN:.0%} pickup gain "
                             f"(best {gain:+.2%})", {"trials": trials})
            self.save()
            return
        if st["status"] == "active" and gain < current_gain + S.SCHED_MIN_GAIN and \
                [list(x) for x in [feats]] == [st["partition"]]:
            self._ensure_tables(extra_cohorts)
            self.save()
            return
        weights, _, n, _ = self._fit(self._all(cells), cohorts, g)
        st.update(status="active", partition=list(feats), cohorts=[c.to_json() for c in cohorts], gamma=g,
                  gain=round(gain, 4), tables={c: {str(h): round(w, 3) for h, w in t.items()}
                                                for c, t in weights.items() if c != "default"})
        st["dry_run"] = {"method": "2-fold (even/odd days) out-of-sample replay of historical dials; each cohort's "
                                   "daily dials and each hour's capacity held fixed (Sinkhorn)",
                         "partition": list(feats), "gamma": g, "predicted_pickup_gain": round(gain, 4),
                         "predicted_engagement_change": round(eng_of[(feats, g)], 4),
                         "engagement_limit": -limit,
                         "dials_used": int(sum(n.values())), "alternatives": trials}
        st["v"] += 1
        self._ensure_tables(extra_cohorts, force=True)
        ctx.store.decide(ctx.wf, "schedule_fit",
                         f"call time by {'x'.join(feats)} ({len(cohorts)} cohorts, gamma={g}): predicted pickup "
                         f"{gain:+.1%}, engagement {eng_of[(feats, g)]:+.2%} out of sample", st["dry_run"])
        self.save()

    def _ensure_tables(self, extra: list[Cohort], force: bool = False):
        """Tables for carve-out cohorts (from experiments) that sit in front of the partition."""
        st = self.st
        if st["status"] != "active":
            return
        missing = [c for c in extra if c.id not in st["tables"]]
        if not missing and not force:
            return
        if missing:
            cohorts = missing + [Cohort.from_json(c) for c in st["cohorts"]]
            w, _, _, _ = self._fit(self._all(self._cells()), cohorts, st["gamma"])
            for c in missing:
                if c.id in w:
                    st["tables"][c.id] = {str(h): round(v, 3) for h, v in w[c.id].items()}
            st["v"] += 1

    def cohorts(self) -> list[Cohort]:
        if self.st["status"] != "active":
            return []
        return [Cohort.from_json(c) for c in self.st["cohorts"]]

    def intents(self) -> list[Intent]:
        st, ctx = self.st, self.ctx
        if st["status"] != "active" or (st["pinned_until"] and ctx.now < st["pinned_until"]):
            return []
        lo, hi = ctx.window
        tables = {cid: {h: w for h, w in t.items() if lo <= int(h) < hi} for cid, t in st["tables"].items()}

        def apply(cfg):
            ids = {c["id"] for c in cfg["cohorts"]}
            hw = cfg["schedule"].setdefault("hour_weights", {})
            for cid, t in tables.items():
                if cid in ids and t:
                    # every dialling hour keeps a positive weight, so capacity is never left unused
                    hw[cid] = {str(h): float(t.get(str(h), 1.0)) for h in range(lo, hi)}

        return [Intent(
            module=self.name, dims=["cohorts", "schedule"], apply=apply, version=st["v"],
            summary=f"call-time weights by {'x'.join(st['partition'])} (predicted pickup {st['gain']:+.1%})",
            rationale=("Historical pickup differs by hour between these cohorts. Shifting each cohort's calls "
                       "toward its own better hours, with hourly capacity unchanged, is predicted to lift pickup "
                       f"{st['gain']:+.1%} (out of sample). Every hour keeps a positive weight."),
            priority="routine", material=True, auto_ok=True, safety=False,
            expected_rel_gain=st["gain"] if ctx.primary == "pickup_rate" else 0.0, dry_run=st["dry_run"],
            affected=None)]
