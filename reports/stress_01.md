# Grade report — stress_01

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.349 | +6.8% | 0.239 | +3.8% | 0.504 | +1.5% | 8% | 48 (57) |
| nw_kyc | 0.414 | +123.1% | 0.260 | +95.1% | 0.616 | +1.9% | 9% | 24 (17) |
| nw_renewal | 0.470 | +7.7% | 0.290 | +11.9% | 0.546 | -3.8% | -63% | 95 (165) |
| ALL | 0.403 | +20.9% | 0.261 | +17.6% | 0.541 | +0.7% | -7% | 167 (239) |

## Changes applied by the controller

13 applied — beneficial: 9, harmful: 1, neutral: 3

- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.011 didΔ=+0.000 → **beneficial**
- nw_renewal v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=-0.001 didΔ=+0.044 → **beneficial**
- nw_kyc v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.022 didΔ=+0.157 → **beneficial**
- nw_renewal v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- nw_renewal v5 (controller_approved) dims=['prompt_version'] engageΔ=-0.047 didΔ=+0.000 → **harmful**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+1.115 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['voice'] engageΔ=+0.023 didΔ=+0.000 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v5 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.012 didΔ=+0.000 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v6 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.116 → **beneficial**
- ks_cancel v7 (controller_approved) dims=['voice'] engageΔ=+0.049 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 13,
  "decisions": {
    "approved": 12,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 11,
  "auto_applied": 2,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_kyc": 2,
    "nw_renewal": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D2 14:00",
    "mttr_minutes": 1455,
    "fixed_by": "controller_approved"
  },
  {
    "type": "did_flagged",
    "workflow": "nw_kyc",
    "at": "D3 11:00",
    "flagged_did_share_by_day": {
      "3": 0.702,
      "4": 0.699,
      "5": 0.449,
      "6": 0.223,
      "7": 0.0,
      "8": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D4 10:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W2": {
    "3": 0.152,
    "4": 0.151,
    "5": 0.154,
    "6": 0.062,
    "7": 0.0,
    "8": 0.0
  }
}
```

Credits used: 4 · validation runs: 2 · LLM fix calls: 0