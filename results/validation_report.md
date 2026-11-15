# Phase 2 validation report - campaign `phase2-primary`

Generated from `/home/preetham/Desktop/netguardAI/NetGuardAI/data` (stored outputs only).

## Run status

- experiments recorded: 17
- success: 15, failed: 2, invalid: 0, still marked running: 0

## Queue-delay threshold X

- X = **29 ms** (p99.9 = 28.301 ms over 900 NORMAL seconds with utilisation < 90%; floor applied: False)
- calibrated from: 20261115T134955Z-normal-r1-7b068c, 20261115T135525Z-normal-r2-7becf1, 20261115T140055Z-normal-r3-0f2d9f

## Experiments

| experiment | scenario | rep | seed | status | rows | cpu max % | congested s | onsets | labelled | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 20261115T150802Z-degraded-r1-c2cb4c | degraded | 1 | 42 | success | 300 | 42.7 | 0 | 0 | labelled |  |
| 20261115T151355Z-degraded-r2-1fd54e | degraded | 2 | 43 | success | 300 | 49.8 | 0 | 0 | labelled |  |
| 20261115T151955Z-degraded-r3-5a3abe | degraded | 3 | 44 | success | 300 | 32.4 | 0 | 0 | labelled |  |
| 20261115T143506Z-flash_crowd-r1-51bb23 | flash_crowd | 1 | 42 | success | 300 | 26.6 | 3 | 1 | labelled |  |
| 20261115T144035Z-flash_crowd-r2-254c50 | flash_crowd | 2 | 43 | success | 300 | 15.4 | 0 | 0 | labelled |  |
| 20261115T144606Z-flash_crowd-r3-ef9f19 | flash_crowd | 3 | 44 | success | 300 | 34.4 | 0 | 0 | labelled |  |
| 20261115T134955Z-normal-r1-7b068c | normal | 1 | 42 | success | 300 | 42.6 | 0 | 0 | labelled |  |
| 20261115T135525Z-normal-r2-7becf1 | normal | 2 | 43 | success | 300 | 100.0 | 0 | 0 | labelled |  |
| 20261115T140055Z-normal-r3-0f2d9f | normal | 3 | 44 | success | 300 | 67.5 | 0 | 0 | labelled |  |
| 20261115T140625Z-ramp-r1-e357bb | ramp | 1 | 42 | failed | n/a | n/a | n/a | n/a | n/a | interrupted by the user |
| 20261115T141815Z-ramp-r1-47e977 | ramp | 1 | 42 | success | 300 | 56.1 | 238 | 3 | labelled |  |
| 20261115T142353Z-ramp-r2-b8dbc1 | ramp | 2 | 43 | failed | 300 | 48.4 | 238 | 2 | labelled | 1 traffic event(s) did not complete: ['ramp-0215: failed client still running 10s after its planned end'] |
| 20261115T154036Z-ramp-r2-0aa9a4 | ramp | 2 | 43 | success | 300 | 9.2 | 238 | 3 | labelled |  |
| 20261115T142932Z-ramp-r3-94f8ac | ramp | 3 | 44 | success | 300 | 47.1 | 238 | 3 | labelled |  |
| 20261115T145136Z-udp_flood-r1-2c0f7e | udp_flood | 1 | 42 | success | 300 | 11.9 | 64 | 2 | labelled |  |
| 20261115T145705Z-udp_flood-r2-c1ae60 | udp_flood | 2 | 43 | success | 300 | 21.6 | 63 | 2 | labelled |  |
| 20261115T150233Z-udp_flood-r3-28be3b | udp_flood | 3 | 44 | success | 300 | 9.9 | 68 | 3 | labelled |  |

## Metrics with real data (best fraction of non-null seconds, successful runs)

| metric | degraded | flash_crowd | normal | ramp | udp_flood |
|---|---|---|---|---|---|
| NFStream flows | 100% | 100% | 100% | 100% | 100% |
| RTT (ping probe) | 100% | 100% | 100% | 100% | 100% |
| TCP retransmissions (ss) | 100% | 100% | 100% | 100% | 100% |
| TShark packets | 100% | 100% | 100% | 100% | 100% |
| VM CPU (psutil) | 100% | 100% | 100% | 100% | 100% |
| VM RAM (psutil) | 100% | 100% | 100% | 100% | 100% |
| active connections (ss) | 100% | 100% | 100% | 100% | 100% |
| bandwidth utilisation | 100% | 100% | 100% | 100% | 100% |
| jitter (ping probe) | 100% | 100% | 100% | 100% | 100% |
| packets per second | 100% | 100% | 100% | 100% | 100% |
| probe loss | 100% | 100% | 100% | 100% | 100% |
| queue backlog (tc -s qdisc) | 100% | 100% | 100% | 100% | 100% |
| queue delay | 100% | 100% | 100% | 100% | 100% |
| tc loss / drops | 100% | 100% | 100% | 100% | 100% |
| throughput (ip -s link) | 100% | 100% | 100% | 100% | 100% |

## Labelling definition

- congested second: utilisation >= 90% and (queue delay >= X or loss > 1%) for >= 3 consecutive seconds
- future_congestion: a new onset within the next 30 s; the last 30 s of every run have null future labels and `future_label_available = False`

