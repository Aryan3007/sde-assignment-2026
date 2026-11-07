"""Every tunable the controller uses, in one place, with the reason for its value.

Changing a number here changes behaviour on every workflow. The values were chosen from the
reasoning in SUBMISSION.md section 5, not tuned against the public scenarios' hidden truth.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------- loop
TICK_MINUTES = 15                 # one sim tick; react within 15 sim-minutes
ANALYSIS_EVERY_MIN = 60           # experiment / schedule analysis cadence

# ---------------------------------------------------------------- experiments (welcome_message, voice)
EXPLORE_SHARE_EACH = 0.15         # traffic per challenger arm while exploring
EXPLORE_SHARE_TOTAL_MAX = 0.45    # incumbent always keeps >= 55%
MIN_LIVE_HOURS_BEFORE_DECIDE = 20  # cover most of one dialling day before any winner call
MIN_CONN_PER_ARM_PROMOTE = 300    # connected calls on the challenger before it can win
Z_PROMOTE = 3.0                   # one-sided, see SUBMISSION 5.3 for the false-winner simulation
Z_DROP_LOSER = -2.0               # dropping an arm is cheap and reduces exposure: lower bar
MIN_CONN_PER_ARM_DROP = 150
MAX_EXPLORE_DAYS = 6              # stop an inconclusive test rather than run it forever
HOLDOUT_SHARE = 0.15              # control kept after promotion, for decay detection
DECAY_WINDOW_HOURS = 24
DECAY_Z_WORSE = -2.33             # winner now clearly worse than control (hourly looks: needs a high bar)
DECAY_Z_DROP_FROM_PROMO = -2.0    # recent lift significantly below the lift we promoted on...
DECAY_KEEP_FRACTION = 0.25        # ...and less than a quarter of it is left
FADE_WINDOW_HOURS = 48           # daily fade check: last 2 days vs everything before
FADE_Z_DROP = -2.5                # drop an exploring arm whose recent lift fell this far below its earlier lift
TREND_Z_HOLD = -1.64              # don't promote if the last 24h is significantly below the earlier period
DECAY_MIN_CONTROL_CONN = 120
SEGMENT_HARM_Z = -2.33            # carve a segment out of a promotion if it is hurt
SEGMENT_HARM_MIN_REL = 0.05
SEGMENT_MIN_CONN = 100
MAX_CARVEOUTS_PER_DIM = 3
PIN_AFTER_SUPERVISOR_HOURS = 72   # we do not touch a dim a supervisor edited for 3 days

# ---------------------------------------------------------------- caller ID
DID_WINDOW_HOURS = 6              # dialling hours of evidence for health checks
DID_EVAL_EVERY_MIN = 30
DID_MIN_DIALS = 250
DID_QUARANTINE_RATIO = 0.70       # pickup below 70% of the other numbers' pickup...
DID_QUARANTINE_Z = -3.0           # ...very unlikely to be noise...
DID_OWN_DROP_RATIO = 0.80         # ...and (if it has a track record) below 80% of its OWN recent pickup
DID_OWN_DROP_Z = -2.5
DID_GRADUATE_RATIO = 0.85         # a probed number needs >= 85% of the others' pickup to get a full share
DID_PROBE_SHARE_APPROVAL = 0.20   # same, where caller-ID changes need an approval each time
DID_PROBE_SHARE = 0.10            # max share for a number we have no evidence on (bounds the cost of a bad one)
DID_MIN_CHANGE_TV = 0.05          # ignore re-balancing smaller than this

# ---------------------------------------------------------------- schedule (call time, per cohort)
SCHED_SHRINK_K = 150              # pseudo-dials toward the cohort's flat profile
SCHED_GAMMAS = (0.0, 1.0, 2.0, 3.0)
SCHED_WEIGHT_CLIP = (0.2, 4.0)
SCHED_MIN_GAIN = 0.01             # only change schedule if dry run predicts >= +1% pickup
SCHED_MAX_COHORTS = 12

# ---------------------------------------------------------------- hallucination incidents
HALLUC_MIN_CONN = 150
HALLUC_P = 1e-3                   # one-sided binomial vs 1.2x baseline flag rate
HALLUC_P_FAST = 1e-2              # ...when the new prompt ALSO failed an independent validation run
HALLUC_MIN_CONN_FAST = 100
HALLUC_MIN_RATIO = 2.0            # and at least 2x the baseline flag rate
HALLUC_WINDOW_HOURS = 4
VALIDATION_N = 200                # 2 credits. One run, no re-rolls (re-running until it passes is p-hacking)
INCIDENT_MAX_CREDITS = 14
CREDIT_RESERVE = 10               # never spend below this outside an incident
VERIFY_MIN_CONN = 150

# ---------------------------------------------------------------- governance
URGENT_KINDS = ("incident", "rollback")
REJECT_COOLDOWN_HOURS = 6
AUTO_MIN_GAP_MIN = 60             # max_shift is per change; we also rate-limit auto steps
ROUTINE_RESERVE_SLOTS = 1         # keep one request slot per 24h for incidents/rollbacks


def preferences() -> dict:
    """Per-workflow soft preferences from client memos (stricter than policy, never looser)."""
    p = Path(os.environ.get("CTRL_PREFS", Path(__file__).with_name("preferences.json")))
    return json.loads(p.read_text()) if p.exists() else {}
