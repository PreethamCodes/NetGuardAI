"""Ground-truth congestion labels (pure functions; no I/O).

Definition (``configs/labeling.yaml``):

    cond[t]   = utilisation[t] >= 90 %  AND  (queue_delay[t] >= X  OR  loss[t] > 1 %)
    congested = member of a run of >= 3 consecutive seconds with cond == True
    onset     = first second of such a run

    current_congestion[t]  = t is congested
    time_to_congestion[t]  = (next onset strictly after t) - t     [evaluation helper]
    future_congestion[t]   = a NEW onset occurs in (t, t + 30]

A second that is already inside a congested run therefore asks for the *next, separate*
episode: the current episode does not make ``future_congestion`` trivially true.

Missing measurements are never invented. ``cond`` is True / False / unknown (None);
every label is evaluated twice, treating unknowns as False (pessimistic) and as True
(optimistic), and is only reported when both agree, else it is null.

Final-horizon rule: the last ``horizon`` seconds of a run cannot see a complete
horizon, so their ``future_congestion`` is null and ``future_label_available`` is
False (they must be excluded when training the prediction model). Their
``time_to_congestion`` is still reported when an onset exists later in the run (the
value is exact); otherwise it is null (no onset observed before the run ended).

Labels are computed with future observations, features must never be: nothing here
is a feature, and feature builders must only use seconds <= t.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from netguard.experiments.config import LabelingConfig
from netguard.exceptions import ConfigError

LABEL_COLUMNS = (
    "scenario_label", "current_congestion", "future_congestion", "time_to_congestion",
    "future_label_available", "congestion_condition", "labels_status",
)

Cond = Optional[bool]


def condition_series(
    utilisation_pct: Sequence[float], queue_delay_ms: Sequence[float], loss_pct: Sequence[float],
    util_threshold: float, queue_threshold_ms: float, loss_threshold: float,
) -> List[Cond]:
    """Per-second ``cond`` with three-valued logic (None = cannot be decided)."""
    out: List[Cond] = []
    for u, q, l in zip(utilisation_pct, queue_delay_ms, loss_pct):
        if _nan(u):
            out.append(None)
            continue
        if u < util_threshold:
            out.append(False)  # decided regardless of the other inputs
            continue
        high_queue = None if _nan(q) else q >= queue_threshold_ms
        high_loss = None if _nan(l) else l > loss_threshold
        if high_queue or high_loss:
            out.append(True)
        elif high_queue is None or high_loss is None:
            out.append(None)
        else:
            out.append(False)
    return out


def _nan(x: float) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or bool(pd.isna(x))


def _runs(flags: Sequence[bool], min_len: int) -> List[Tuple[int, int]]:
    """Inclusive ``(start, end)`` of every run of >= ``min_len`` consecutive True values."""
    runs, start = [], None
    for i, flag in enumerate(list(flags) + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start >= min_len:
                runs.append((start, i - 1))
            start = None
    return runs


def label_series(cond: Sequence[Cond], horizon: int, min_consecutive: int) -> pd.DataFrame:
    """Compute ``current_congestion``, ``future_congestion``, ``time_to_congestion`` from ``cond``."""
    n = len(cond)
    pess = [c is True for c in cond]
    opt = [c is not False for c in cond]
    runs_p, runs_o = _runs(pess, min_consecutive), _runs(opt, min_consecutive)
    in_p, in_o = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    for a, b in runs_p:
        in_p[a:b + 1] = True
    for a, b in runs_o:
        in_o[a:b + 1] = True
    onsets_p = [a for a, _ in runs_p]
    onsets_o = [a for a, _ in runs_o]

    current: List[Optional[bool]] = []
    for t in range(n):
        if in_p[t]:
            current.append(True)
        elif not in_o[t]:
            current.append(False)
        else:
            current.append(None)

    future: List[Optional[bool]] = []
    ttc: List[float] = []
    available: List[bool] = []
    for t in range(n):
        nxt_p = next((o for o in onsets_p if o > t), None)
        nxt_o = next((o for o in onsets_o if o > t), None)
        ttc.append(float(nxt_p - t) if nxt_p is not None and nxt_p == nxt_o else math.nan)
        complete = t + horizon <= n - 1
        available.append(complete)
        if not complete:
            future.append(None)
        elif any(t < o <= t + horizon for o in onsets_p):
            future.append(True)
        elif any(t < o <= t + horizon for o in onsets_o):
            future.append(None)  # an onset depends on a missing measurement
        else:
            future.append(False)
    return pd.DataFrame({
        "current_congestion": pd.array(current, dtype="boolean"),
        "future_congestion": pd.array(future, dtype="boolean"),
        "time_to_congestion": pd.array(ttc, dtype="float64"),
        "future_label_available": pd.array(available, dtype="boolean"),
    })


def label_observations(obs: pd.DataFrame, labeling: LabelingConfig, scenario_label: str) -> pd.DataFrame:
    """Add the label columns to one experiment's per-second table (rows must be ordered by ``second``)."""
    threshold = labeling.queue_delay_threshold_ms
    if threshold is None:
        raise ConfigError("queue_delay_threshold_ms (X) is not calibrated yet; run 'experiment calibrate' first")
    out = obs.copy()
    cond = condition_series(
        out["utilisation_pct"], out["queue_delay_ms"], out["loss_pct"],
        labeling.utilisation_pct, threshold, labeling.loss_pct,
    )
    labels = label_series(cond, labeling.horizon_s, labeling.min_consecutive_s)
    labels.index = out.index
    out["scenario_label"] = scenario_label
    out["congestion_condition"] = pd.array(cond, dtype="boolean")
    for column in ("current_congestion", "future_congestion", "time_to_congestion", "future_label_available"):
        out[column] = labels[column]
    out["labels_status"] = "labelled"
    return out


def unlabelled(obs: pd.DataFrame, scenario_label: str) -> pd.DataFrame:
    """Observation table with null labels, used until X has been calibrated."""
    out = obs.copy()
    out["scenario_label"] = scenario_label
    out["congestion_condition"] = pd.array([None] * len(out), dtype="boolean")
    out["current_congestion"] = pd.array([None] * len(out), dtype="boolean")
    out["future_congestion"] = pd.array([None] * len(out), dtype="boolean")
    out["time_to_congestion"] = pd.array([math.nan] * len(out), dtype="float64")
    out["future_label_available"] = pd.array([None] * len(out), dtype="boolean")
    out["labels_status"] = "pending_calibration"
    return out
