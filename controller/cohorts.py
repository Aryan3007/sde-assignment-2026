"""Lead features and the ONE shared cohort object.

A lead's four features are packed into a 16-bit code (4 bits per feature), so every aggregate
is keyed by code and any cohort can be evaluated against any aggregate. Detection, experiments,
the schedule, the supervisor view and the config all use the same `Cohort` with the same id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FEATS = ("lang", "city_tier", "age_band", "value_band")
_SHIFT = {"lang": 12, "city_tier": 8, "age_band": 4, "value_band": 0}
UNKNOWN = 0xFFFF
MANAGED_PREFIX = "fc_"  # cohorts the controller owns. Anything else (supervisor-made) is left alone.


class Vocab:
    """Feature value <-> small int, learned from `lead` events and persisted (so codes are stable)."""

    def __init__(self, data: dict | None = None):
        self.values: dict[str, list[str]] = {f: list((data or {}).get(f, [])) for f in FEATS}
        self._idx = {f: {v: i for i, v in enumerate(vs)} for f, vs in self.values.items()}
        self.dirty = False

    def encode(self, features: dict) -> int:
        code = 0
        for f in FEATS:
            v = str(features.get(f, "?"))
            i = self._idx[f].get(v)
            if i is None:
                i = len(self.values[f])
                if i >= 15:
                    raise ValueError(f"too many values for feature {f}")
                self.values[f].append(v)
                self._idx[f][v] = i
                self.dirty = True
            code |= i << _SHIFT[f]
        return code

    def decode(self, code: int) -> dict[str, str]:
        return {f: self.value_of(code, f) for f in FEATS}

    def value_of(self, code: int, feat: str) -> str:
        if code == UNKNOWN:
            return "?"
        i = (code >> _SHIFT[feat]) & 0xF
        vs = self.values[feat]
        return vs[i] if i < len(vs) else "?"

    def to_json(self) -> dict:
        return self.values


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower().replace("+", "p"))


@dataclass(frozen=True)
class Cohort:
    """One population of leads. `where` uses the simulator's semantics: AND across features, OR within."""
    id: str
    where: dict = field(default_factory=dict)
    origin: str = "schedule"     # schedule | carveout | supervisor

    @staticmethod
    def of(where: dict, origin: str = "schedule") -> "Cohort":
        w = {f: sorted(where[f]) for f in FEATS if f in where}
        parts = [f"{f.split('_')[0]}_{'_'.join(_slug(v) for v in vals)}" for f, vals in w.items()]
        return Cohort(MANAGED_PREFIX + ("__".join(parts) or "all"), w, origin)

    def matches(self, feats: dict[str, str]) -> bool:
        return all(feats.get(f) in vals for f, vals in self.where.items())

    def matches_code(self, code: int, vocab: Vocab) -> bool:
        return all(vocab.value_of(code, f) in vals for f, vals in self.where.items())

    def label(self) -> str:
        if not self.where:
            return "everyone"
        return " & ".join(f"{f}={'/'.join(v)}" for f, v in self.where.items())

    def to_config(self) -> dict:
        return {"id": self.id, "where": {f: list(v) for f, v in self.where.items()}}

    def to_json(self) -> dict:
        return {"id": self.id, "where": self.where, "origin": self.origin}

    @staticmethod
    def from_json(d: dict) -> "Cohort":
        return Cohort(d["id"], {k: list(v) for k, v in d["where"].items()}, d.get("origin", "schedule"))


def assign(cohorts: list[Cohort], feats: dict[str, str]) -> str:
    """First-match, exactly like the simulator. Returns 'default' when nothing matches."""
    for c in cohorts:
        if c.matches(feats):
            return c.id
    return "default"


def partition(feats: tuple[str, ...], vocab: Vocab, present_codes: set[int]) -> list[Cohort]:
    """Cohorts covering every combination of `feats` values that actually occurs."""
    combos = sorted({tuple(vocab.value_of(c, f) for f in feats) for c in present_codes})
    return [Cohort.of({f: [v] for f, v in zip(feats, combo)}, "schedule") for combo in combos]


def segments(vocab: Vocab, present_codes: set[int]) -> list[Cohort]:
    """Single-feature segments (lang=ta, age_band=60+, ...) used for harm checks on promotions."""
    out = []
    for f in FEATS:
        for v in sorted({vocab.value_of(c, f) for c in present_codes}):
            out.append(Cohort.of({f: [v]}, "carveout"))
    return out
