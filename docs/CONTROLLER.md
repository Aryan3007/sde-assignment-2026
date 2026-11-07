# Controller: run, debug, extend

For the engineer who gets paged. Design rationale lives in `SUBMISSION.md`; this is the operator's manual.

## Run

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt   # or python3 -m venv + pip
.venv/bin/python -m sim serve --scenario scenarios/public_02_incidents.yaml --run-dir runs/mine02 --force &
./run.sh http://127.0.0.1:8700                 # runs to the end of the scenario; state in state/<scenario>.db

scripts/run_scenario.sh public_02_incidents    # serve + run + grade in one go -> reports/public_02_incidents.md
scripts/crash_test.sh public_02_incidents 6    # 6 random SIGKILLs + invariant checks -> reports/crash_recovery_*.txt
scripts/load_test.sh                           # public_03_load -> reports/load_test.md
.venv/bin/python scripts/false_winner_sim.py   # false-winner rate / power of the experiment rule
.venv/bin/python -m pytest -q tests
```

Only dependency beyond the standard library: `httpx` (plus `numpy` in the test/sim scripts). Storage is one SQLite
file. Set `CTRL_DB` to choose it; `CTRL_LOG=DEBUG` for verbose logs; `CTRL_STORE_CALLS=0` to skip the raw call table.

**Restart = run it again.** The store remembers everything; `open_store` refuses to resume against a different
simulator world (new run, clock went backwards, versions we applied are missing) and moves the old file aside.

## Look at it

```bash
.venv/bin/python -m controller status                                  # loop health + open alerts with runbooks
.venv/bin/python -m controller view --workflow ks_cancel               # the supervisor's one screen
.venv/bin/python -m controller why --workflow ks_cancel --at "D3 15:00"   # which config, who approved, why, what next
.venv/bin/python -m controller customer --lead ks_cancel:l00012345     # what this person heard on each call, and why
.venv/bin/python -m controller decisions --workflow ks_cancel --kind experiment_promote
sqlite3 state/public_02_incidents.db "select * from proposals where wf='ks_cancel'"
```

## Code map (one loop, four modules, one gate)

| file | owns |
|---|---|
| `loop.py` | tick order: ingest → reconcile → supervisor edits → observe modules → apply approved → plan → advance |
| `ingest.py` | chunk → aggregates, one transaction per chunk incl. the cursor (`chunks_done`) |
| `cohorts.py` | feature codes + the shared `Cohort` object (`fc_*` ids are ours; others are the client's) |
| `experiments.py` | welcome/voice tests: explore, drop losers/faders, promote with 15% holdout, decay rollback, segment carve-outs |
| `callerid.py` | number health, quarantine, probe → graduate, even spread |
| `schedule.py` | per-cohort hour weights, 2-fold out-of-sample dry run with fixed capacity |
| `incidents.py` | hallucination detect → known-good / LLM candidate → validate → propose → verify |
| `governance.py` | the ONLY code that changes config: routing (auto vs approval), budgets, timing, lifecycle, reconcile |
| `configlib.py` | changed-dims / max-shift exactly as the contract (tested against the simulator) |
| `settings.py` | every threshold, with its reason |
| `report.py` | read-only views over the store |

A module never calls the simulator to change anything. It returns an `Intent` (what it wants, why, how risky,
dry run). To add a capability: write a module with `observe()` + `intents()`, add it to `Controller.step`, and
say in `Intent` whether it is risk-reducing (`safety`, `safe_arm`) and auto-eligible (`auto_ok`).

## Alerts and what to do

| key | severity | meaning | action |
|---|---|---|---|
| `halluc:<wf>` | page | hallucination incident open | `view` the workflow; a validated fix should be pending within 1h. If not, see `halluc_nofix`/`halluc_credits` |
| `halluc_nofix:<wf>` | page | no prompt passed validation | a human picks a prompt with the client; nothing automatic will happen |
| `halluc_credits:<wf>`, `credits_low` | page | can't afford validation | raise the budget; incidents stall otherwise |
| `ingest_lag` | page | we are deciding on stale data | check logs for chunk fetch errors / simulator health |
| `step_error:<wf>` | page | a workflow's step raised | bug; that workflow is frozen, others continue. Traceback in logs |
| `did_quarantine:<wf>:<did>` | ticket | a caller ID was rotated out | carrier reputation check; clear `quarantined` in `module_state` to re-enable |
| `kpi_drift:<wf>:<kpi>` | ticket | a KPI fell >10% vs the prior 3 days | check `decisions` for supervisor edits / quarantines / prompt changes in the window |
| `reject_bug:<pid>`, `proposal_error:*` | ticket | approver/API refused for a fixable reason | read `proposals.request` and `last_error`; it is our bug |
| `lapsed:<pid>` | ticket | approved change not applied in time | decision lag or a deliberate hold; check metrics |
| `fix_engagement:<wf>` | ticket | hallucination fixed but engagement dropped | safe-but-boring prompt; discuss the next candidate with the client |

Not paged by design: experiment decisions, rejections by approver judgement, stale applies after supervisor
edits (all handled automatically and visible in `view`).

## Known sharp edges

- One process, one SQLite writer. Don't run two controllers on one store.
- `CTRL_STORE_CALLS=1` (default) keeps every D0+ call row for the compliance lookup: ~100 bytes/call.
- Thresholds were reasoned, then checked on public scenarios; `scripts/false_winner_sim.py` re-derives the
  experiment error rates if you change them.
