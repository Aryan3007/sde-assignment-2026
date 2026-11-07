"""Controller tests where they matter: statistics, config semantics vs the contract, governance routing,
ingestion idempotency/crash safety, and the decision thresholds' false-positive rates.

Run: .venv/bin/python -m pytest -q tests
(These tests may import `sim` to check our reimplementation against the contract; the controller never does.)
"""
import copy
import json
import math
import random

import numpy as np
import pytest

from controller import settings as S
from controller.cohorts import Cohort, Vocab, assign
from controller.configlib import changed_dims, clean, interpolate_allocations, max_shift
from controller.context import Intent
from controller.governance import Governance, compile_config
from controller.ingest import Ingestor, ts_to_minute
from controller.schedule import sinkhorn
from controller.stats import binom_sf, ncdf, nppf, stratified_diff
from controller.store import Store


# ------------------------------------------------------------------ stats
def test_nppf_inverts_ncdf():
    for p in (1e-6, 0.001, 0.025, 0.3, 0.5, 0.9, 0.999):
        assert abs(ncdf(nppf(p)) - p) < 1e-8


def test_binom_sf_matches_brute_force():
    n, p = 60, 0.07
    pmf = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    for k in (0, 1, 4, 9, 20):
        assert abs(binom_sf(k, n, p) - sum(pmf[k:])) < 1e-12


def test_stratified_diff_removes_drift_that_pooling_gets_wrong():
    # Period 1: base rate 0.6, arm A gets 10% of traffic. Period 2: base rate 0.3 (an incident),
    # arm A gets 50%. A and B are identical within each period. Pooling makes A look worse.
    strata = [(100, 60, 900, 540), (500, 150, 500, 150)]
    c = stratified_diff(strata)
    pooled = (60 + 150) / 600 - (540 + 150) / 1400
    assert pooled < -0.1
    assert abs(c.diff) < 1e-9


def test_stratified_diff_detects_real_lift():
    rng = np.random.default_rng(1)
    strata = []
    for _ in range(8):
        na, nb = 150, 600
        strata.append((na, int(rng.binomial(na, 0.66)), nb, int(rng.binomial(nb, 0.55))))
    c = stratified_diff(strata)
    assert c.z > 3 and 0.05 < c.diff < 0.17


# ------------------------------------------------------------------ cohorts
def test_cohort_ids_are_canonical_and_first_match_like_the_sim():
    a = Cohort.of({"lang": ["ta"], "age_band": ["60+"]})
    b = Cohort.of({"age_band": ["60+"], "lang": ["ta"]})
    assert a.id == b.id and a.id.startswith("fc_")
    young = Cohort.of({"age_band": ["18-30"]})
    feats = {"lang": "ta", "city_tier": "1", "age_band": "60+", "value_band": "low"}
    assert assign([young, a], feats) == a.id
    assert assign([young], feats) == "default"


def test_vocab_roundtrip():
    v = Vocab()
    f = {"lang": "kn", "city_tier": "3", "age_band": "46-60", "value_band": "high"}
    code = v.encode(f)
    assert v.decode(code) == f
    assert Vocab(json.loads(json.dumps(v.to_json()))).decode(code) == f


# ------------------------------------------------------------------ config semantics vs the contract
def _random_cfg(rng):
    opts = {"welcome_message": ["W1", "W2", "W3"], "voice": ["V1", "V2", "V3", "V4"], "caller_id": ["D1", "D2", "D3"]}

    def d(dim):
        ks = rng.sample(opts[dim], rng.randint(1, len(opts[dim])))
        return {k: rng.choice([0.2, 0.5, 1.0]) for k in ks}
    cohorts = [{"id": "fc_age_60p", "where": {"age_band": ["60+"]}}] if rng.random() < 0.5 else []
    alloc = {"default": {k: d(k) for k in opts}}
    if cohorts and rng.random() < 0.7:
        alloc["fc_age_60p"] = {"voice": d("voice")}
    return {"cohorts": cohorts, "allocations": alloc, "prompt_version": rng.choice(["P1", "P3"]),
            "schedule": {"hour_weights": {"default": {"9": 1.0}} if rng.random() < 0.3 else {}, "lead_preferred_hour": {}}}


def test_changed_dims_and_max_shift_match_simulator_contract():
    from sim import world as W
    spec = {"options": {"welcome_message": ["W1", "W2", "W3"], "voice": ["V1", "V2", "V3", "V4"],
                        "caller_id": ["D1", "D2", "D3"], "prompt_version": ["P1", "P3"]}, "dialling_window": [9, 21]}
    rng = random.Random(3)
    for _ in range(300):
        a, b = _random_cfg(rng), _random_cfg(rng)
        na, nb = W.normalise_config(spec, a), W.normalise_config(spec, b)
        assert changed_dims(na, nb) == W.changed_dims(na, nb)
        assert abs(max_shift(na, nb) - W.max_shift(na, nb)) < 1e-9


def test_interpolation_scales_shift_linearly():
    cur = clean({"allocations": {"default": {"caller_id": {"D1": 0.5, "D2": 0.5}, "voice": {"V1": 1}, "welcome_message": {"W1": 1}}}})
    tgt = copy.deepcopy(cur)
    tgt["allocations"]["default"]["caller_id"] = {"D3": 1.0}
    full = max_shift(cur, tgt)
    step = interpolate_allocations(cur, tgt, 0.2 / full, ["caller_id"])
    assert abs(max_shift(cur, step) - 0.2) < 1e-6


# ------------------------------------------------------------------ governance routing
def _it(dims, apply, **kw):
    return Intent(module=kw.pop("module", "experiments"), dims=dims, apply=apply, summary="s", rationale="r",
                  version=1, **kw)


def test_risk_reducing_check_blocks_new_arms_and_allows_shrinking():
    cur = clean({"allocations": {"default": {"voice": {"V1": 0.55, "V2": 0.45}, "welcome_message": {"W1": 1},
                                             "caller_id": {"D1": 1}}}})
    shrink = copy.deepcopy(cur)
    shrink["allocations"]["default"]["voice"] = {"V1": 1.0}
    grow = copy.deepcopy(cur)
    grow["allocations"]["default"]["voice"] = {"V1": 0.5, "V2": 0.3, "V3": 0.2}
    promote = copy.deepcopy(cur)
    promote["allocations"]["default"]["voice"] = {"V1": 0.15, "V2": 0.85}
    safe = [_it(["voice"], lambda c: None, safe_arm="V1")]
    assert Governance._shrinks_exposure(cur, shrink, "voice", safe)
    assert not Governance._shrinks_exposure(cur, grow, "voice", safe)        # new arm V3
    assert not Governance._shrinks_exposure(cur, promote, "voice", safe)     # V2 gains share
    assert not Governance._shrinks_exposure(cur, shrink, "voice", [_it(["voice"], lambda c: None)])


def test_compile_keeps_supervisor_cohorts_first_and_drops_dangling_managed_entries():
    cur = clean({"cohorts": [{"id": "sup_vip", "where": {"value_band": ["high"]}},
                             {"id": "fc_old", "where": {"lang": ["ta"]}}],
                 "allocations": {"default": {"voice": {"V1": 1}, "welcome_message": {"W1": 1}, "caller_id": {"D1": 1}},
                                 "fc_old": {"voice": {"V2": 1}}},
                 "prompt_version": "P1",
                 "schedule": {"hour_weights": {"fc_old": {"9": 2.0}}, "lead_preferred_hour": {}}})
    new = compile_config(cur, [], [Cohort.of({"age_band": ["60+"]})])
    assert [c["id"] for c in new["cohorts"]] == ["sup_vip", "fc_age_60p"]
    assert "fc_old" not in new["allocations"] and "fc_old" not in new["schedule"]["hour_weights"]


def test_sinkhorn_preserves_margins():
    base = {("a", 9): 10.0, ("a", 10): 30.0, ("b", 9): 20.0, ("b", 10): 5.0}
    w = {("a", 9): 3.0, ("a", 10): 0.5, ("b", 9): 0.5, ("b", 10): 2.0}
    q = sinkhorn({k: base[k] * w[k] for k in base}, {"a": 40, "b": 25}, {9: 30, 10: 35}, iters=200)
    assert abs(q[("a", 9)] + q[("a", 10)] - 40) < 1e-6
    assert abs(q[("a", 9)] + q[("b", 9)] - 30) < 1e-6


# ------------------------------------------------------------------ ingestion idempotency / crash safety
class FakeSim:
    def __init__(self, chunks):
        self._chunks = chunks

    def chunks(self, kind, after=-1):
        return [c["meta"] for c in self._chunks if c["meta"]["kind"] == kind and c["meta"]["chunk"] > after]

    def chunk_text(self, n):
        return self._chunks[n]["text"]


def _chunks():
    lead = {"type": "lead", "ts": "2027-01-04T08:00:00+05:30", "workflow_id": "w", "campaign_id": "w:c0001",
            "lead_id": "w:l00000003", "person_id": "w:p0000003",
            "features": {"lang": "hi", "city_tier": "1", "age_band": "60+", "value_band": "low"}}
    call = {"type": "call", "ts": "2027-01-04T10:07:00+05:30", "workflow_id": "w", "campaign_id": "w:c0001",
            "lead_id": "w:l00000003", "person_id": "w:p0000003", "attempt_no": 1, "campaign_day_index": 0,
            "config_version": 1, "cohort_id": "default",
            "variant": {"welcome_message": "W1", "voice": "V1", "caller_id": "D1", "prompt_version": "P1"},
            "connected": True, "engaged": True, "disposition": "concluded_interested",
            "qa": {"hallucination_flag": False, "flow_adherence": 0.9}}
    out = []
    eid = 0
    for n in range(3):
        lines = []
        for ev in ([lead] if n == 0 else []) + [call]:
            eid += 1
            lines.append(json.dumps({**ev, "event_id": eid}))
        out.append({"meta": {"chunk": n, "kind": "events", "events": len(lines), "first_event_id": eid - len(lines) + 1,
                             "last_event_id": eid, "until_minute": 600}, "text": "\n".join(lines) + "\n"})
    return out


def test_ingest_is_idempotent_and_crash_safe(tmp_path, monkeypatch):
    store = Store(tmp_path / "s.db")
    ing = Ingestor(store, FakeSim(_chunks()))
    # crash in the middle of chunk 1's transaction: nothing of chunk 1 may persist
    orig = store.tx

    class Boom(Exception):
        pass
    calls = {"n": 0}

    def tx_then_crash():
        t = orig()
        calls["n"] += 1
        if calls["n"] == 2:
            class T:
                def __enter__(s):
                    return t.__enter__()

                def __exit__(s, *a):
                    t.__exit__(Boom, Boom(), None)
                    raise Boom()
            return T()
        return t
    monkeypatch.setattr(store, "tx", tx_then_crash)
    with pytest.raises(Boom):
        ing.ingest_all()
    monkeypatch.setattr(store, "tx", orig)
    assert store.one("SELECT SUM(dials) FROM agg_hour")[0] == 1          # only chunk 0 committed
    # restart: a new ingestor resumes from the store and finishes; running it again is a no-op
    ing2 = Ingestor(store, FakeSim(_chunks()))
    ing2.ingest_all()
    ing2.ingest_all()
    assert store.one("SELECT SUM(dials), SUM(eng) FROM agg_hour")[0] == 3
    assert store.one("SELECT COUNT(*) FROM calls")[0] == 3
    assert store.one("SELECT COUNT(*) FROM chunks_done")[0] == 3
    assert ing2.leads["w"].code[3] != 0xFFFF and ing2.leads["w"].done[3] == 1


def test_ts_to_minute():
    assert ts_to_minute("2027-01-04T00:00:00+05:30") == 0
    assert ts_to_minute("2027-01-07T14:00:00+05:30") == 3 * 1440 + 14 * 60
    assert ts_to_minute("2026-12-31T23:45:00+05:30") == -4 * 1440 + 23 * 60 + 45


# ------------------------------------------------------------------ false-positive rates of the triggers
def test_hallucination_trigger_false_positive_rate_is_low():
    """Healthy prompt (flag rate = baseline): probability of a false incident in one 4h window."""
    from controller.incidents import Incidents
    rng = np.random.default_rng(5)
    base, n = 0.015, 450
    fp = 0
    for _ in range(4000):
        k = int(rng.binomial(n, base))
        p = binom_sf(k, n, base * 1.2)
        fp += n >= S.HALLUC_MIN_CONN and p < S.HALLUC_P and k / n >= S.HALLUC_MIN_RATIO * base
    assert fp / 4000 < 0.001
    assert Incidents  # imported for coverage of module import


def test_hallucination_trigger_catches_a_bad_prompt_within_two_hours():
    rng = np.random.default_rng(6)
    base, bad, n = 0.015, 0.053, 220  # ~2h of connected calls on a 400 dials/h workflow
    hits = 0
    for _ in range(2000):
        k = int(rng.binomial(n, bad))
        hits += binom_sf(k, n, base * 1.2) < S.HALLUC_P and k / n >= S.HALLUC_MIN_RATIO * base
    assert hits / 2000 > 0.5
