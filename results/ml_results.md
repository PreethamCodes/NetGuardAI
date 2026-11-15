# Phase 4/5 model results

All numbers come from runs held out by experiment (see prep_report.md). Where a value is `n/a` the metric is undefined for the data available.

## Traffic classification

| model | split | accuracy | macro F1 | macro PR-AUC | size KB | latency ms |
|---|---|---|---|---|---|---|
| decision_tree | val | 0.891 | 0.891 | 0.897 | 4 | 0.14 |
| decision_tree | test | 0.893 | 0.887 | 0.871 | 4 | 0.14 |
| random_forest | val | 0.894 | 0.894 | 0.935 | 1352 | 100.79 |
| random_forest | test | 0.908 | 0.906 | 0.951 | 1352 | 100.79 |
| xgboost | val | 0.892 | 0.892 | 0.934 | 690 | 0.44 |
| xgboost | test | 0.901 | 0.896 | 0.946 | 690 | 0.44 |

## Anomaly detection (positive = any non-NORMAL second)

| model | split | precision | recall | F1 | PR-AUC | FPR | size KB | latency ms |
|---|---|---|---|---|---|---|---|---|
| isolation_forest | val | 0.987 | 0.999 | 0.993 | 1.000 | 0.052 | 2556 | 18.72 |
| isolation_forest | test | 0.970 | 1.000 | 0.985 | 0.998 | 0.122 | 2556 | 18.72 |
| lof | val | 0.987 | 0.969 | 0.978 | 0.998 | 0.052 | 269 | 19.66 |
| lof | test | 0.960 | 0.967 | 0.964 | 0.993 | 0.159 | 269 | 19.66 |

Flag rate by scenario (test):

| model | DEGRADED | FLASH_CROWD | NORMAL | RAMP | UDP_FLOOD |
|---|---|---|---|---|---|
| isolation_forest | 1.000 | 1.000 | 0.122 | 1.000 | 1.000 |
| lof | 1.000 | 0.941 | 0.159 | 1.000 | 0.926 |

## Congestion prediction (next 30 s)


| method | split | subset | precision | recall | F1 | PR-AUC | FPR | onsets detected | mean lead s |
|---|---|---|---|---|---|---|---|---|---|
| logistic_regression | val | all_rows | 0.402 | 0.700 | 0.511 | 0.565 | 0.149 | 4/5 | 28.8 |
| logistic_regression | val | not_yet_congested | 0.400 | 0.523 | 0.453 | 0.562 | 0.059 | 4/5 | 28.8 |
| logistic_regression | test | all_rows | 0.513 | 0.595 | 0.551 | 0.629 | 0.092 | 5/6 | 29.6 |
| logistic_regression | test | not_yet_congested | 0.873 | 0.522 | 0.653 | 0.727 | 0.008 | 5/6 | 29.6 |
| random_forest | val | all_rows | 0.505 | 0.613 | 0.554 | 0.629 | 0.086 | 4/5 | 26.2 |
| random_forest | val | not_yet_congested | 0.507 | 0.523 | 0.515 | 0.610 | 0.038 | 4/5 | 26.2 |
| random_forest | test | all_rows | 0.412 | 0.321 | 0.361 | 0.490 | 0.075 | 4/6 | 27.5 |
| random_forest | test | not_yet_congested | 0.472 | 0.370 | 0.415 | 0.576 | 0.046 | 4/6 | 27.5 |
| xgboost | val | all_rows | 0.656 | 0.560 | 0.604 | 0.673 | 0.042 | 4/5 | 24.0 |
| xgboost | val | not_yet_congested | 0.850 | 0.523 | 0.648 | 0.648 | 0.007 | 4/5 | 24.0 |
| xgboost | test | all_rows | 0.727 | 0.286 | 0.410 | 0.549 | 0.017 | 4/6 | 26.8 |
| xgboost | test | not_yet_congested | 1.000 | 0.359 | 0.528 | 0.653 | 0.000 | 4/6 | 26.8 |
| persistence | val | all_rows | 0.326 | 0.567 | 0.414 | 0.239 | 0.168 | 3/5 | 30.0 |
| persistence | val | not_yet_congested | n/a | 0.000 | n/a | 0.070 | 0.000 | 3/5 | 30.0 |
| persistence | test | all_rows | 0.273 | 0.429 | 0.333 | 0.197 | 0.186 | 4/6 | 26.0 |
| persistence | test | not_yet_congested | n/a | 0.000 | n/a | 0.100 | 0.000 | 4/6 | 26.0 |
| utilisation_gt_80 | val | all_rows | 0.328 | 0.693 | 0.445 | 0.276 | 0.203 | 5/5 | 21.2 |
| utilisation_gt_80 | val | not_yet_congested | 0.413 | 0.292 | 0.342 | 0.242 | 0.031 | 5/5 | 21.2 |
| utilisation_gt_80 | test | all_rows | 0.308 | 0.583 | 0.403 | 0.247 | 0.213 | 6/6 | 20.8 |
| utilisation_gt_80 | test | not_yet_congested | 0.524 | 0.239 | 0.328 | 0.311 | 0.024 | 6/6 | 20.8 |
| utilisation_trend | val | all_rows | 0.164 | 0.820 | 0.273 | 0.146 | 0.599 | 5/5 | 29.8 |
| utilisation_trend | val | not_yet_congested | 0.087 | 0.708 | 0.156 | 0.144 | 0.556 | 5/5 | 29.8 |
| utilisation_trend | test | all_rows | 0.162 | 0.702 | 0.263 | 0.226 | 0.593 | 6/6 | 29.5 |
| utilisation_trend | test | not_yet_congested | 0.110 | 0.652 | 0.188 | 0.232 | 0.583 | 6/6 | 29.5 |

No claim of successful 30 s-ahead prediction is made unless the `not_yet_congested` rows and the detected-onset counts above support it.
