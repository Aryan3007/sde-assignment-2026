# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.370 | +57.3% | 0.248 | +36.8% | 0.509 | +8.8% | 36% | 76 (348) |
| nw_kyc | 0.489 | +30.7% | 0.303 | +29.2% | 0.608 | +0.0% | 0% | 29 (29) |
| nw_renewal | 0.479 | +10.6% | 0.291 | +11.0% | 0.549 | -1.1% | 16% | 64 (54) |
| ALL | 0.432 | +30.5% | 0.273 | +24.6% | 0.545 | +2.3% | 25% | 169 (431) |

## Changes applied by the controller

26 applied — neutral: 13, beneficial: 13

- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.014 → **neutral**
- nw_renewal v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.011 didΔ=+0.000 → **beneficial**
- nw_kyc v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.135 → **beneficial**
- nw_renewal v4 (controller_approved) dims=['voice'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- nw_renewal v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.045 → **beneficial**
- nw_renewal v6 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['welcome_message'] engageΔ=-0.007 didΔ=+0.000 → **neutral**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.215 → **beneficial**
- ks_cancel v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.177 → **beneficial**
- ks_cancel v6 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.032 → **beneficial**
- ks_cancel v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.009 → **neutral**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.098 → **beneficial**
- ks_cancel v9 (controller_approved) dims=['prompt_version'] engageΔ=+0.102 didΔ=+0.000 → **beneficial**
- ks_cancel v10 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.061 → **beneficial**
- ks_cancel v11 (controller_auto) dims=['welcome_message', 'caller_id'] engageΔ=+0.000 didΔ=+0.018 → **neutral**
- ks_cancel v12 (controller_auto) dims=['welcome_message', 'caller_id'] engageΔ=+0.000 didΔ=+0.018 → **neutral**
- ks_cancel v13 (controller_auto) dims=['welcome_message', 'caller_id'] engageΔ=+0.000 didΔ=+0.018 → **neutral**
- ks_cancel v14 (controller_auto) dims=['welcome_message', 'caller_id'] engageΔ=+0.000 didΔ=+0.017 → **neutral**
- ks_cancel v15 (controller_auto) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v16 (controller_approved) dims=['welcome_message'] engageΔ=+0.007 didΔ=+0.000 → **neutral**
- nw_renewal v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- ks_cancel v17 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v9 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v18 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v10 (controller_approved) dims=['voice'] engageΔ=-0.007 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 13,
  "decisions": {
    "approved": 12,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 11,
  "auto_applied": 15,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 2,
    "nw_kyc": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.193,
      "3": 0.0,
      "4": 0.0,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0,
      "8": 0.0,
      "9": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 135,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "2": 0.146,
    "3": 0.159,
    "4": 0.141,
    "5": 0.027,
    "6": 0.0,
    "7": 0.0,
    "8": 0.0,
    "9": 0.0
  }
}
```

Credits used: 4 · validation runs: 2 · LLM fix calls: 0