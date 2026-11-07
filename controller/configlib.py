"""Config semantics from docs/SIMULATOR.md section 3-4, reimplemented from the contract.

We compute changed dims and the allocation shift ourselves so we can route a change to auto-apply
or the approval path BEFORE sending it, instead of discovering a 403 after the fact.
"""
from __future__ import annotations

import copy

from .stats import tv_distance

AGENT_DIMS = ("welcome_message", "voice", "caller_id")
CHANGE_DIMS = ("cohorts", "welcome_message", "voice", "caller_id", "prompt_version", "schedule")
CUSTOMER_FACING = ("welcome_message", "voice", "prompt_version")


def clean(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    out.setdefault("cohorts", [])
    out.setdefault("allocations", {})
    sch = out.setdefault("schedule", {})
    sch.setdefault("hour_weights", {})
    sch.setdefault("lead_preferred_hour", {})
    return out


def dist(cfg: dict, cohort: str, dim: str) -> dict:
    a = cfg["allocations"]
    return (a.get(cohort) or {}).get(dim) or a["default"][dim]


def _norm(d: dict) -> dict:
    s = sum(d.values())
    return {k: round(v / s, 9) for k, v in d.items() if v > 0} if s else {}


def changed_dims(old: dict, new: dict) -> list[str]:
    old, new = clean(old), clean(new)
    ch = set()
    if old["cohorts"] != new["cohorts"]:
        ch.add("cohorts")
    keys = set(old["allocations"]) | set(new["allocations"])
    for d in AGENT_DIMS:
        if any(_norm(dist(old, k, d)) != _norm(dist(new, k, d)) for k in keys):
            ch.add(d)
    if old.get("prompt_version") != new.get("prompt_version"):
        ch.add("prompt_version")
    if _norm_sched(old["schedule"]) != _norm_sched(new["schedule"]):
        ch.add("schedule")
    return [d for d in CHANGE_DIMS if d in ch]


def _norm_sched(s: dict) -> dict:
    return {"hour_weights": {k: {str(h): float(w) for h, w in v.items()} for k, v in (s.get("hour_weights") or {}).items()},
            "lead_preferred_hour": {str(k): int(v) for k, v in (s.get("lead_preferred_hour") or {}).items()}}


def max_shift(old: dict, new: dict) -> float:
    old, new = clean(old), clean(new)
    keys = {"default"} | {c["id"] for c in old["cohorts"]} | {c["id"] for c in new["cohorts"]}
    worst = 0.0
    for k in keys:
        for d in AGENT_DIMS:
            worst = max(worst, tv_distance(_norm(dist(old, k, d)), _norm(dist(new, k, d))))
    return worst


def interpolate_allocations(cur: dict, target: dict, alpha: float, dims: list[str]) -> dict:
    """Move `cur` toward `target` by fraction alpha on the given agent dims. TV scales linearly with alpha."""
    out = copy.deepcopy(cur)
    keys = set(cur["allocations"]) | set(target["allocations"])
    for k in keys:
        for d in dims:
            if d not in AGENT_DIMS:
                continue
            a, b = _norm(dist(cur, k, d)), _norm(dist(target, k, d))
            if a == b:
                continue
            mixed = {o: (1 - alpha) * a.get(o, 0) + alpha * b.get(o, 0) for o in set(a) | set(b)}
            mixed = {o: round(w, 6) for o, w in mixed.items() if w > 1e-6}
            out["allocations"].setdefault(k, {})[d] = mixed
    return out


def diff_summary(old: dict, new: dict) -> dict:
    """Human-readable diff: only what changed, by dimension and cohort."""
    old, new = clean(old), clean(new)
    out: dict = {}
    if old["cohorts"] != new["cohorts"]:
        oi = {c["id"]: c for c in old["cohorts"]}
        ni = {c["id"]: c for c in new["cohorts"]}
        out["cohorts"] = {"added": [c for i, c in ni.items() if i not in oi],
                          "removed": [i for i in oi if i not in ni],
                          "order": [c["id"] for c in new["cohorts"]]}
    keys = sorted(set(old["allocations"]) | set(new["allocations"]))
    for d in AGENT_DIMS:
        per = {}
        for k in keys:
            a, b = _norm(dist(old, k, d)), _norm(dist(new, k, d))
            if a != b:
                per[k] = {"from": {o: round(w, 3) for o, w in a.items()}, "to": {o: round(w, 3) for o, w in b.items()}}
        if per:
            out[d] = per
    if old.get("prompt_version") != new.get("prompt_version"):
        out["prompt_version"] = {"from": old.get("prompt_version"), "to": new.get("prompt_version")}
    os_, ns = _norm_sched(old["schedule"]), _norm_sched(new["schedule"])
    if os_ != ns:
        hw = {}
        for k in sorted(set(os_["hour_weights"]) | set(ns["hour_weights"])):
            if os_["hour_weights"].get(k) != ns["hour_weights"].get(k):
                hw[k] = {"to": ns["hour_weights"].get(k)}
        out["schedule"] = {"hour_weights": hw}
        if os_["lead_preferred_hour"] != ns["lead_preferred_hour"]:
            out["schedule"]["lead_preferred_hour"] = {"entries": len(ns["lead_preferred_hour"])}
    return out
