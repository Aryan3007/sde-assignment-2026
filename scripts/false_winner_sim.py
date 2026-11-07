"""Monte Carlo of the experiment decision rule with the controller's real peeking schedule.

Simulates one dim (incumbent 55%, three challengers 15% each) on a workflow with C connected calls
per dialling hour, 12 dialling hours a day, evaluated every hour after MIN_LIVE_HOURS, for
MAX_EXPLORE_DAYS. Uses controller.stats.stratified_diff on 3-hour strata exactly like the controller.

Reports, per scenario:
  false winner rate  P(any challenger promoted | all arms equal)
  false drop rate    P(a given null challenger dropped as a loser)
  power / median time-to-promote for a true +10% / +15% / +20% challenger

  .venv/bin/python scripts/false_winner_sim.py [--sims 2000] [--conn-per-hour 90]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller import settings as S  # noqa: E402
from controller.stats import stratified_diff  # noqa: E402


def run(rng, conn_per_hour, lifts, base=0.55, days=S.MAX_EXPLORE_DAYS):
    k = len(lifts)
    shares = np.array([1 - S.EXPLORE_SHARE_EACH * k] + [S.EXPLORE_SHARE_EACH] * k)
    p = np.array([base] + [min(base * (1 + l), 0.95) for l in lifts])
    blocks = []  # per 3h block: (n[arms], x[arms])
    hours = 0
    dropped = set()
    for day in range(days):
        for h in range(12):
            if h % 3 == 0:
                blocks.append([np.zeros(k + 1), np.zeros(k + 1)])
            sh = shares.copy()
            for d in dropped:
                sh[0] += sh[d]
                sh[d] = 0
            n = rng.multinomial(rng.poisson(conn_per_hour), sh / sh.sum())
            x = rng.binomial(n, p)
            blocks[-1][0] += n
            blocks[-1][1] += x
            hours += 1
            if hours < 2:
                continue
            for a in range(1, k + 1):
                if a in dropped:
                    continue
                strata = [(int(b[0][a]), int(b[1][a]), int(b[0][0]), int(b[1][0])) for b in blocks]
                c = stratified_diff(strata)
                if c.n_a >= S.MIN_CONN_PER_ARM_DROP and c.z <= S.Z_DROP_LOSER:
                    dropped.add(a)
                    continue
                if hours < S.MIN_LIVE_HOURS_BEFORE_DECIDE or c.n_a < S.MIN_CONN_PER_ARM_PROMOTE or c.z < S.Z_PROMOTE:
                    continue
                recent = stratified_diff(strata[-4:])  # last ~24 dialling hours = 4 blocks of 3h
                if recent.n_a and recent.diff < 0:
                    continue
                return a, day * 12 + h, dropped
    return None, None, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--conn-per-hour", type=float, default=90)
    a = ap.parse_args()
    rng = np.random.default_rng(7)
    null = [run(rng, a.conn_per_hour, [0, 0, 0]) for _ in range(a.sims)]
    fw = sum(1 for w, _, _ in null if w) / a.sims
    fd = sum(len(d) for _, _, d in null) / (3 * a.sims)
    print(f"conn/hour={a.conn_per_hour}  Z_PROMOTE={S.Z_PROMOTE}  Z_DROP={S.Z_DROP_LOSER}  sims={a.sims}")
    print(f"null (all arms equal): false-winner rate {fw:.1%}, false-drop rate per arm {fd:.1%}")
    for lift in (0.10, 0.15, 0.20):
        res = [run(rng, a.conn_per_hour, [lift, 0, 0]) for _ in range(a.sims // 2)]
        hit = [t for w, t, _ in res if w == 1]
        wrong = sum(1 for w, _, _ in res if w and w != 1) / len(res)
        med = np.median(hit) / 12 if hit else float("nan")
        print(f"true +{lift:.0%} arm: promoted {len(hit) / len(res):.0%} of runs, median {med:.1f} dialling days; "
              f"wrong arm promoted {wrong:.1%}")


if __name__ == "__main__":
    main()
