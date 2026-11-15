# Phase 3 preparation report

- split scheme: `three_way`, X = 29.0 ms
- runs included: 15, excluded: 2
- feature rows: 4500, features: 105

## Runs

| experiment | scenario | rep | status | rows | included | problems |
|---|---|---|---|---|---|---|
| 20261115T150802Z-degraded-r1-c2cb4c | degraded | 1 | success | 300 | yes | - |
| 20261115T151355Z-degraded-r2-1fd54e | degraded | 2 | success | 300 | yes | - |
| 20261115T151955Z-degraded-r3-5a3abe | degraded | 3 | success | 300 | yes | - |
| 20261115T143506Z-flash_crowd-r1-51bb23 | flash_crowd | 1 | success | 300 | yes | - |
| 20261115T144035Z-flash_crowd-r2-254c50 | flash_crowd | 2 | success | 300 | yes | - |
| 20261115T144606Z-flash_crowd-r3-ef9f19 | flash_crowd | 3 | success | 300 | yes | - |
| 20261115T134955Z-normal-r1-7b068c | normal | 1 | success | 300 | yes | - |
| 20261115T135525Z-normal-r2-7becf1 | normal | 2 | success | 300 | yes | - |
| 20261115T140055Z-normal-r3-0f2d9f | normal | 3 | success | 300 | yes | - |
| 20261115T140625Z-ramp-r1-e357bb | ramp | 1 | failed | 0 | NO | experiment status is 'failed'; processed table is missing |
| 20261115T141815Z-ramp-r1-47e977 | ramp | 1 | success | 300 | yes | - |
| 20261115T142353Z-ramp-r2-b8dbc1 | ramp | 2 | failed | 300 | NO | experiment status is 'failed' |
| 20261115T154036Z-ramp-r2-0aa9a4 | ramp | 2 | success | 300 | yes | - |
| 20261115T142932Z-ramp-r3-94f8ac | ramp | 3 | success | 300 | yes | - |
| 20261115T145136Z-udp_flood-r1-2c0f7e | udp_flood | 1 | success | 300 | yes | - |
| 20261115T145705Z-udp_flood-r2-c1ae60 | udp_flood | 2 | success | 300 | yes | - |
| 20261115T150233Z-udp_flood-r3-28be3b | udp_flood | 3 | success | 300 | yes | - |

## Splits (by run)

| split | runs | rows | complete | prediction rows | positives | positives (not yet congested) |
|---|---|---|---|---|---|---|
| test | 5 | 1500 | 1350 | 1200 | 168 | 92 |
| train | 5 | 1500 | 1350 | 1200 | 180 | 95 |
| val | 5 | 1500 | 1350 | 1200 | 150 | 65 |

## Labels by scenario

| scenario | runs | rows | congested seconds | future-label rows | future positives |
|---|---|---|---|---|---|
| DEGRADED | 3 | 900 | 0 | 810 | 0 |
| FLASH_CROWD | 3 | 900 | 3 | 810 | 30 |
| NORMAL | 3 | 900 | 0 | 810 | 0 |
| RAMP | 3 | 900 | 714 | 810 | 270 |
| UDP_FLOOD | 3 | 900 | 195 | 810 | 198 |

## Notes

- Windows are trailing (past + current second); lags are positive shifts; NaN marks seconds without a full window or lag (the first 30 s of each run) and is never interpolated.
- VM CPU/RAM are in the data but excluded from the model inputs.
- syn_ratio = SYN packets of flows started in the second / TCP packets of that second (NFStream flows).
- congested_causal uses only seconds <= t (ground-truth current_congestion needs the next 2 s).
