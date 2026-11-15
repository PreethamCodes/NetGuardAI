"""Dataset validation: run status, duplicates, timestamps, missing values and label consistency.

Nothing here modifies data; it returns findings that the pipeline writes to the preparation report
and uses to *exclude* runs (never to repair them).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from netguard.experiments.config import LabelingConfig
from netguard.experiments.labels import condition_series, label_series

REQUIRED_COLUMNS = (
    "experiment_id", "scenario", "repetition", "seed", "second", "timestamp_s",
    "throughput_mbps", "utilisation_pct", "queue_delay_ms", "loss_pct", "rtt_mean_ms", "jitter_ms",
    "scenario_label", "current_congestion", "future_congestion", "time_to_congestion", "future_label_available",
)


@dataclass
class RunCheck:
    experiment_id: str
    scenario: str
    repetition: int
    status: str
    rows: int = 0
    included: bool = False
    problems: List[str] = field(default_factory=list)  # reasons the run is excluded
    warnings: List[str] = field(default_factory=list)  # noted, run stays

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def check_run(meta: Dict[str, Any], obs: Optional[pd.DataFrame], labeling: LabelingConfig,
              expected_rows: Optional[int] = None) -> RunCheck:
    """Validate one run; ``included`` is True only if nothing disqualifies it."""
    check = RunCheck(meta["experiment_id"], meta["scenario"], meta["repetition"], meta["status"])
    if meta["status"] != "success":
        check.problems.append(f"experiment status is '{meta['status']}'")
    if obs is None:
        check.problems.append("processed table is missing")
        return check
    check.rows = len(obs)
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in obs.columns]
    if missing_cols:
        check.problems.append(f"missing columns: {missing_cols}")
        return check
    if expected_rows is not None and len(obs) != expected_rows:
        check.problems.append(f"{len(obs)} rows, expected {expected_rows}")
    if obs["experiment_id"].nunique() != 1 or obs["experiment_id"].iloc[0] != meta["experiment_id"]:
        check.problems.append("experiment_id in the table does not match the metadata")
    if obs.duplicated(["experiment_id", "second"]).any():
        check.problems.append(f"{int(obs.duplicated(['experiment_id', 'second']).sum())} duplicate (experiment_id, second) rows")
    if obs.duplicated().any():
        check.problems.append(f"{int(obs.duplicated().sum())} fully duplicated rows")
    seconds = obs["second"].tolist()
    if seconds != list(range(len(seconds))):
        check.problems.append("'second' is not a gap-free 0..n-1 sequence")
    steps = obs["timestamp_s"].diff().dropna().round(3)
    if not (steps == 1.0).all():
        check.problems.append("timestamps are not strictly 1 s apart")
    if obs["labels_status"].ne("labelled").any():
        check.problems.append("labels are not computed (calibration pending)")
    else:
        check.problems += verify_labels(obs, labeling)
    # warnings only
    metrics = ["throughput_mbps", "utilisation_pct", "queue_delay_ms", "loss_pct", "rtt_mean_ms", "jitter_ms"]
    for col in metrics:
        frac = float(obs[col].isna().mean())
        if frac > 0:
            check.warnings.append(f"{col}: {frac:.1%} missing")
    check.included = not check.problems
    return check


def verify_labels(obs: pd.DataFrame, labeling: LabelingConfig) -> List[str]:
    """Recompute the labels from the stored measurements and compare with the stored label columns."""
    problems: List[str] = []
    x = labeling.queue_delay_threshold_ms
    if x is None:
        return ["no queue-delay threshold X available to verify labels"]
    obs = obs.sort_values("second")
    cond = condition_series(obs["utilisation_pct"], obs["queue_delay_ms"], obs["loss_pct"],
                            labeling.utilisation_pct, x, labeling.loss_pct)
    expected = label_series(cond, labeling.horizon_s, labeling.min_consecutive_s)
    for col in ("current_congestion", "future_congestion", "future_label_available"):
        stored = obs[col].reset_index(drop=True).astype("boolean")
        want = expected[col].astype("boolean")
        same = (stored.isna() & want.isna()) | (stored == want).fillna(False)
        if not same.all():
            problems.append(f"{col} differs from the recomputed label in {int((~same).sum())} second(s)")
    stored_ttc = obs["time_to_congestion"].reset_index(drop=True).astype("float64")
    ttc_same = (stored_ttc.isna() & expected["time_to_congestion"].isna()) | np.isclose(
        stored_ttc.fillna(-1), expected["time_to_congestion"].astype("float64").fillna(-1))
    if not ttc_same.all():
        problems.append(f"time_to_congestion differs from the recomputed label in {int((~ttc_same).sum())} second(s)")
    n = len(obs)
    unavailable = int((~obs["future_label_available"].astype(bool)).sum())
    if unavailable != min(n, labeling.horizon_s):
        problems.append(f"{unavailable} rows without a future label, expected {min(n, labeling.horizon_s)}")
    tail_labels = obs.sort_values("second").tail(labeling.horizon_s)["future_congestion"]
    if tail_labels.notna().any():
        problems.append("a future_congestion label exists inside the final horizon (must be unavailable)")
    return problems


def label_summary(frame: pd.DataFrame) -> Dict[str, Any]:
    """Class counts used in the report (per scenario)."""
    out: Dict[str, Any] = {}
    for scenario, grp in frame.groupby("scenario_label"):
        usable = grp[grp["future_label_available"].astype(bool)]
        out[str(scenario)] = {
            "rows": int(len(grp)),
            "current_congestion_seconds": int(grp["current_congestion"].fillna(False).astype(bool).sum()),
            "future_label_rows": int(len(usable)),
            "future_congestion_positive": int(usable["future_congestion"].fillna(False).astype(bool).sum()),
            "runs": int(grp["experiment_id"].nunique()),
        }
    return out
