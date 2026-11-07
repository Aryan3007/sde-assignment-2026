# Load test: public_03_load (12 workflows)

Total wall clock, controller start to scenario end (7 history days + 5 sim days): 108s on Darwin arm64, 10 CPUs.

Measured from the controller's own `metrics` table (one row per tick). `wall` includes the simulator's own time to generate the day (it runs on the same machine), so it is an upper bound on controller time.

| sim day | ticks | events ingested | ingest s | ingest ev/s | decide s | wall s (incl. simulator) | max decision lag s |
|---|---|---|---|---|---|---|---|
| history+D0 start | 53 | 6,364,090 | 26.9 | 236,153 | 4.4 | 12.9 | 22.91 |
| D1 | 52 | 840,035 | 5.1 | 163,590 | 5.8 | 15.8 | 0.97 |
| D2 | 52 | 840,017 | 5.2 | 162,416 | 7.3 | 17.4 | 1.06 |
| D3 | 52 | 840,014 | 5.2 | 161,479 | 8.9 | 19.0 | 1.09 |
| D4 | 52 | 840,014 | 5.2 | 162,102 | 9.9 | 19.9 | 1.12 |
| D5 | 1 | 13,500 | 0.1 | 136,364 | 0.1 | 0.3 | 0.24 |

Peak RSS (controller): 1050.4 MB. Store size: 304 MB (3,240,000 raw call rows kept for the audit trail). Simulator HTTP calls: 5,134.
