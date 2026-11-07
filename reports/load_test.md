# Load test: public_03_load (12 workflows)

Total wall clock, controller start to scenario end (7 history days + 5 sim days): 105s on Darwin arm64, 10 CPUs.

Measured from the controller's own `metrics` table (one row per tick). `wall` includes the simulator's own time to generate the day (it runs on the same machine), so it is an upper bound on controller time.

| sim day | ticks | events ingested | ingest s | ingest ev/s | decide s | wall s (incl. simulator) | max decision lag s |
|---|---|---|---|---|---|---|---|
| history+D0 start | 53 | 6,364,085 | 27.2 | 233,673 | 4.2 | 12.8 | 23.21 |
| D1 | 52 | 840,038 | 5.1 | 164,006 | 5.6 | 15.7 | 0.94 |
| D2 | 52 | 840,023 | 5.1 | 163,971 | 6.2 | 16.3 | 1.04 |
| D3 | 52 | 840,016 | 5.2 | 162,447 | 7.4 | 17.6 | 1.07 |
| D4 | 52 | 840,016 | 5.2 | 161,542 | 9.2 | 19.4 | 1.11 |
| D5 | 1 | 13,500 | 0.1 | 142,105 | 0.1 | 0.3 | 0.22 |

Peak RSS (controller): 1269.8 MB. Store size: 301 MB (3,240,000 raw call rows kept for the audit trail). Simulator HTTP calls: 5,201.
