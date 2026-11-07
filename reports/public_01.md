# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.354 | +3.7% | 0.247 | +0.8% | 0.505 | +2.7% | 6% | 51 (57) |
| nw_kyc | 0.435 | +18.8% | 0.286 | +18.3% | 0.590 | -1.1% | 0% | 29 (21) |
| nw_renewal | 0.471 | +13.2% | 0.286 | +8.3% | 0.558 | +3.0% | 8% | 49 (54) |
| ALL | 0.410 | +10.4% | 0.268 | +6.8% | 0.542 | +2.4% | 5% | 129 (132) |

## Changes applied by the controller

10 applied — neutral: 5, beneficial: 5

- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.007 → **neutral**
- nw_renewal v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.147 → **beneficial**
- nw_renewal v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.015 → **neutral**
- nw_renewal v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.033 → **beneficial**
- ks_cancel v3 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.105 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v5 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 5,
  "decisions": {
    "approved": 4,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 4,
  "auto_applied": 6,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "3": 0.148,
    "4": 0.137,
    "5": 0.151,
    "6": 0.159,
    "7": 0.159
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0