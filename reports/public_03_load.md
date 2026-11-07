# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.396 | +17.4% | 0.266 | +9.3% | 0.527 | +6.2% | 43% | 409 (409) |
| arav_kyc_1 | 0.476 | +32.6% | 0.277 | +28.6% | 0.592 | -1.5% | 0% | 433 (352) |
| arav_renewal_0 | 0.475 | +17.2% | 0.288 | +9.1% | 0.583 | +6.4% | 39% | 419 (408) |
| kest_cancel_2 | 0.400 | +18.7% | 0.269 | +10.9% | 0.526 | +5.3% | 42% | 471 (418) |
| kest_kyc_1 | 0.478 | +34.5% | 0.276 | +28.9% | 0.597 | -0.0% | 0% | 443 (370) |
| kest_renewal_0 | 0.474 | +17.8% | 0.287 | +8.5% | 0.584 | +7.3% | 38% | 501 (403) |
| nort_cancel_2 | 0.401 | +18.2% | 0.268 | +9.8% | 0.531 | +6.5% | 42% | 479 (368) |
| nort_kyc_1 | 0.476 | +33.8% | 0.276 | +28.2% | 0.594 | -0.3% | 0% | 473 (318) |
| nort_renewal_0 | 0.476 | +17.3% | 0.288 | +9.1% | 0.586 | +7.0% | 38% | 469 (419) |
| tide_cancel_2 | 0.400 | +16.4% | 0.267 | +9.3% | 0.529 | +5.5% | 45% | 439 (401) |
| tide_kyc_1 | 0.461 | +29.3% | 0.269 | +25.2% | 0.594 | -0.8% | 0% | 435 (376) |
| tide_renewal_0 | 0.478 | +18.0% | 0.288 | +9.2% | 0.587 | +7.6% | 40% | 490 (425) |
| ALL | 0.449 | +22.5% | 0.277 | +14.8% | 0.570 | +4.4% | 26% | 5461 (4667) |

## Changes applied by the controller

49 applied — neutral: 15, beneficial: 34

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- nort_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- arav_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- tide_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- nort_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.068 → **beneficial**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.068 → **beneficial**
- kest_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- arav_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.068 → **beneficial**
- arav_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- tide_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.068 → **beneficial**
- tide_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- nort_cancel_2 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- tide_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- tide_cancel_2 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.207 → **beneficial**
- kest_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- tide_cancel_2 v5 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- arav_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.223 → **beneficial**
- kest_cancel_2 v5 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v5 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v6 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.238 → **beneficial**
- nort_cancel_2 v5 (controller_auto) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.252 → **beneficial**
- nort_renewal_0 v5 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_cancel_2 v6 (controller_approved) dims=['voice'] engageΔ=+0.059 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v6 (controller_approved) dims=['voice'] engageΔ=+0.060 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.093 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v7 (controller_approved) dims=['voice'] engageΔ=+0.060 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v6 (controller_approved) dims=['voice'] engageΔ=+0.060 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.117 → **beneficial**
- nort_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.092 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.093 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.093 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.123 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.124 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.103 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['voice'] engageΔ=-0.001 didΔ=+0.000 → **neutral**
- tide_cancel_2 v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.021 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.021 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 31,
  "decisions": {
    "approved": 29,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 28,
  "auto_applied": 21,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_kyc_1": 1,
    "nort_cancel_2": 1,
    "kest_kyc_1": 1,
    "arav_kyc_1": 1,
    "tide_kyc_1": 1,
    "nort_renewal_0": 3,
    "kest_renewal_0": 2,
    "kest_cancel_2": 1,
    "arav_renewal_0": 3,
    "arav_cancel_2": 2,
    "tide_renewal_0": 2,
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
  "nort_renewal_0:welcome_message=W3": {
    "3": 0.851,
    "4": 0.85
  },
  "kest_renewal_0:welcome_message=W3": {
    "3": 0.851,
    "4": 0.85
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 0.853,
    "4": 0.851
  },
  "tide_renewal_0:welcome_message=W3": {
    "3": 0.851,
    "4": 0.849
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0