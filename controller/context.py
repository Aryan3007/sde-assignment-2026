"""Per-workflow context handed to every module, and the Intent a module returns.

A module never talks to the simulator directly. It reads aggregates from the store, updates its
own persisted state, and says what it wants the config to look like (an Intent). The governance
planner alone decides whether and how that intent reaches the simulator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .cohorts import Cohort, Vocab
from .store import Store


@dataclass
class Ctx:
    wf: str
    spec: dict                 # GET /workflows/{id}
    store: Store
    vocab: Vocab
    ingest: object             # Ingestor (lead index), typed loosely to avoid an import cycle
    now: int                   # sim minute
    config: dict               # current config (clean)
    version: int
    credits: float = 0.0
    sim: object = None         # client.Sim. Only the incident module uses it (LLM/validation)

    @property
    def day(self) -> int:
        return self.now // 1440

    @property
    def hour(self) -> int:
        return (self.now % 1440) // 60

    @property
    def primary(self) -> str:
        return self.spec["objective"]["primary"]

    @property
    def guardrail(self) -> tuple[str, float]:
        g = self.spec["objective"].get("guardrail") or {}
        for k, v in g.items():
            return k.replace("_max_drop", ""), float(v)
        return ("", 1.0)

    @property
    def window(self) -> tuple[int, int]:
        lo, hi = self.spec["dialling_window"]
        return int(lo), int(hi)

    @property
    def gov(self) -> dict:
        return self.spec["governance"]

    def in_dialling(self) -> bool:
        lo, hi = self.window
        return lo <= self.hour < hi


@dataclass
class Intent:
    """What one module wants changed, and everything governance needs to ask for it."""
    module: str
    dims: list[str]
    apply: Callable[[dict], None]            # mutates a config in place
    summary: str
    rationale: str
    version: int                             # bumps whenever the module's decision changes
    priority: str = "routine"                # routine | rollback | incident
    material: bool = True                    # worth spending an approval request on by itself
    auto_ok: bool = True                     # module allows auto-apply where the policy permits
    safety: bool = False                     # risk-reducing (rollback, quarantine, incident fix)
    expected_rel_gain: float = 0.0           # on the workflow's primary KPI, honest point estimate
    dry_run: dict = field(default_factory=dict)
    affected: list[Cohort] | None = None     # None = every active lead
    evidence: dict | None = None
    safe_arm: str | None = None              # for risk-reducing allocation moves: the only arm allowed to gain share
    impact: float | None = None              # explicit impact score override (incidents, rollbacks)
