"""Small, dependency-free statistics used by every decision the controller makes.

Everything here is deterministic: the same counts always give the same verdict. That is the
point. An LLM may suggest *what* to try. These functions decide *what won*.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def ncdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def nppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, |err| < 1.2e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError(p)
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > hi:
        return -nppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


@dataclass(frozen=True)
class Comparison:
    """Arm A (challenger) vs arm B (control) on a binary outcome, stratified."""
    diff: float          # stratified risk difference p_A - p_B
    se: float
    z: float
    n_a: int
    n_b: int
    x_a: int
    x_b: int

    @property
    def p_a(self) -> float:
        return self.x_a / self.n_a if self.n_a else float("nan")

    @property
    def p_b(self) -> float:
        return self.x_b / self.n_b if self.n_b else float("nan")

    @property
    def rel(self) -> float:
        """Relative lift of A over B (diff / control rate)."""
        return self.diff / self.p_b if self.n_b and self.p_b > 0 else 0.0

    def as_dict(self) -> dict:
        return {"diff": round(self.diff, 5), "se": round(self.se, 5), "z": round(self.z, 3),
                "rel_lift": round(self.rel, 4), "n_a": self.n_a, "x_a": self.x_a,
                "n_b": self.n_b, "x_b": self.x_b}


EMPTY = Comparison(0.0, float("inf"), 0.0, 0, 0, 0, 0)


def stratified_diff(strata: list[tuple[int, int, int, int]]) -> Comparison:
    """Mantel-Haenszel-weighted risk difference over strata of (n_a, x_a, n_b, x_b).

    Arms are randomised *within* each stratum (a time bucket x served cohort), so comparing
    inside strata removes drift that hits both arms (a prompt incident, a caller-ID swap, a
    change in allocation proportions between periods). A pooled comparison would not.
    """
    sw = num = var = 0.0
    na = nb = xa = xb = 0
    tot_n = sum(s[0] + s[2] for s in strata)
    tot_x = sum(s[1] + s[3] for s in strata)
    p_all = (tot_x + 1) / (tot_n + 2) if tot_n else 0.5
    for n1, x1, n2, x2 in strata:
        na += n1; nb += n2; xa += x1; xb += x2
        if n1 == 0 or n2 == 0:
            continue
        w = n1 * n2 / (n1 + n2)
        p1, p2 = x1 / n1, x2 / n2
        pb = (x1 + x2 + 2 * p_all) / (n1 + n2 + 2)  # pooled, lightly smoothed toward the overall rate
        sw += w
        num += w * (p1 - p2)
        var += w * w * pb * (1 - pb) * (1 / n1 + 1 / n2)
    if sw == 0:
        return Comparison(0.0, float("inf"), 0.0, na, nb, xa, xb)
    d = num / sw
    se = math.sqrt(var) / sw
    return Comparison(d, se, d / se if se > 0 else 0.0, na, nb, xa, xb)


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p). Exact, log-space."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    lp, lq = math.log(p), math.log1p(-p)
    lg = math.lgamma
    base = lg(n + 1)
    terms = [base - lg(i + 1) - lg(n - i + 1) + i * lp + (n - i) * lq for i in range(k, n + 1)]
    m = max(terms)
    return min(1.0, math.exp(m) * sum(math.exp(t - m) for t in terms))


def tv_distance(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def normalise(d: dict) -> dict:
    s = float(sum(v for v in d.values() if v > 0))
    return {k: v / s for k, v in d.items() if v > 0} if s > 0 else {}


def round_dist(d: dict, nd: int = 4) -> dict:
    """Round a distribution while keeping it summing to ~1 (drops zero weights)."""
    out = {k: round(v, nd) for k, v in sorted(d.items()) if round(v, nd) > 0}
    return out
