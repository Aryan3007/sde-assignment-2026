# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.375 | +11.3% | 0.249 | +2.3% | 0.535 | +7.8% | 48% | 393 (409) |
| arav_kyc_1 | 0.469 | +30.6% | 0.272 | +26.5% | 0.594 | -1.1% | 0% | 462 (352) |
| arav_renewal_0 | 0.480 | +18.4% | 0.288 | +9.3% | 0.588 | +7.4% | 39% | 483 (408) |
| kest_cancel_2 | 0.373 | +10.7% | 0.250 | +3.3% | 0.526 | +5.5% | 44% | 408 (418) |
| kest_kyc_1 | 0.471 | +32.6% | 0.273 | +27.4% | 0.596 | -0.3% | 0% | 424 (370) |
| kest_renewal_0 | 0.469 | +16.7% | 0.287 | +8.5% | 0.574 | +5.4% | 32% | 440 (403) |
| nort_cancel_2 | 0.372 | +9.5% | 0.249 | +2.2% | 0.528 | +6.0% | 40% | 408 (368) |
| nort_kyc_1 | 0.472 | +32.7% | 0.275 | +27.6% | 0.593 | -0.4% | 0% | 426 (318) |
| nort_renewal_0 | 0.448 | +10.5% | 0.284 | +7.5% | 0.561 | +2.4% | 11% | 473 (419) |
| tide_cancel_2 | 0.376 | +9.6% | 0.250 | +2.1% | 0.533 | +6.3% | 51% | 380 (401) |
| tide_kyc_1 | 0.452 | +26.8% | 0.265 | +23.5% | 0.590 | -1.3% | 0% | 409 (376) |
| tide_renewal_0 | 0.475 | +17.5% | 0.289 | +9.3% | 0.583 | +6.8% | 40% | 445 (425) |
| ALL | 0.436 | +18.9% | 0.269 | +11.8% | 0.568 | +4.1% | 23% | 5151 (4667) |

## Changes applied by the controller

47 applied — neutral: 15, beneficial: 32

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.051 → **beneficial**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.051 → **beneficial**
- arav_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.051 → **beneficial**
- tide_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.051 → **beneficial**
- tide_cancel_2 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- kest_cancel_2 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- arav_cancel_2 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.155 → **beneficial**
- nort_cancel_2 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- arav_cancel_2 v3 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- arav_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.167 → **beneficial**
- arav_cancel_2 v4 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_cancel_2 v3 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v4 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- arav_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.179 → **beneficial**
- tide_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v5 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v3 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.189 → **beneficial**
- kest_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v3 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v6 (controller_approved) dims=['voice'] engageΔ=+0.059 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v4 (controller_approved) dims=['voice'] engageΔ=+0.060 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- arav_renewal_0 v5 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.093 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.170 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.093 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.021 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v5 (controller_approved) dims=['voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.180 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.136 → **beneficial**
- kest_cancel_2 v4 (controller_approved) dims=['voice'] engageΔ=+0.059 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.094 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.154 → **beneficial**
- arav_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.034 → **beneficial**
- arav_cancel_2 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.021 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.021 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v7 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- nort_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.093 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v8 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 40,
  "decisions": {
    "approved": 35,
    "rejected": 5,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 5
  },
  "applied_via_approval": 32,
  "auto_applied": 15,
  "auto_apply_rejected": 0,
  "apply_conflicts": 3,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_renewal_0": 1,
    "nort_kyc_1": 1,
    "nort_cancel_2": 1,
    "kest_renewal_0": 3,
    "kest_kyc_1": 1,
    "kest_cancel_2": 1,
    "arav_renewal_0": 2,
    "arav_kyc_1": 1,
    "arav_cancel_2": 1,
    "tide_renewal_0": 2,
    "tide_kyc_1": 1,
    "tide_cancel_2": 2
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
  "kest_renewal_0:welcome_message=W3": {
    "3": 0.852,
    "4": 0.851
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 0.852,
    "4": 0.848
  },
  "tide_renewal_0:welcome_message=W3": {
    "3": 0.848,
    "4": 0.851
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0