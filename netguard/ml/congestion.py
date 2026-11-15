"""Congestion prediction: will a new congestion episode start within the next 30 s?

Models: Logistic Regression, Random Forest, XGBoost. Baselines: persistence (currently congested),
utilisation above 80 %, and an upward utilisation trend. The decision threshold of every
score-based method is chosen on the validation runs (best F1) and then applied unchanged to the
test runs; without a validation run 0.5 / the documented default is used and the report says so.

Evaluation uses only rows with a complete feature window and a valid 30 s label
(``valid_for_prediction``). Because a model that merely notices that congestion is already
happening is not predicting anything, every method is also scored on the rows where the network is
**not** congested at that second (ground-truth ``current_congestion`` is False) - the honest test.

Warning lead time: for every congestion onset in a test run, the first second within the 30 s
before the onset at which the method raised a warning; ``lead = onset - that second``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from netguard.ml.common import (
    MLData, SPLITS, best_f1_threshold, binary_metrics, model_size_kb, resource_profile,
)

logger = logging.getLogger(__name__)

HORIZON_S = 30
UTIL_BASELINE_PCT = 80.0


def make_models(seed: int = 0, n_jobs: int = 2, scale_pos_weight: float = 1.0) -> Dict[str, Any]:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from xgboost import XGBClassifier

    return {
        "logistic_regression": LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed),
        "random_forest": RandomForestClassifier(n_estimators=150, max_depth=10, class_weight="balanced_subsample",
                                                n_jobs=n_jobs, random_state=seed),
        "xgboost": XGBClassifier(n_estimators=150, max_depth=3, learning_rate=0.1, subsample=0.9,
                                 scale_pos_weight=scale_pos_weight, n_jobs=n_jobs, random_state=seed,
                                 eval_metric="logloss"),
    }


# ---------------------------------------------------------------------------- baselines
def baseline_scores(rows: pd.DataFrame) -> Dict[str, np.ndarray]:
    """Raw scores of the three baselines (higher = more likely congestion soon)."""
    return {
        "persistence": rows["congested_causal"].fillna(0.0).to_numpy(dtype="float64"),
        "utilisation_gt_80": rows["utilisation_pct"].to_numpy(dtype="float64"),
        "utilisation_trend": rows["utilisation_pct_slope5"].to_numpy(dtype="float64"),
    }


# ------------------------------------------------------------------------ lead time
def lead_times(rows: pd.DataFrame, pred: np.ndarray, full: pd.DataFrame, horizon: int = HORIZON_S) -> Dict[str, Any]:
    """Per congestion onset: was it warned about, and how many seconds ahead?

    ``rows`` are the evaluated rows (aligned with ``pred``); ``full`` is the complete feature table, used
    to find onsets of the ground-truth ``current_congestion`` label in the same runs.
    """
    warned = rows.assign(_pred=pred.astype(int))
    onsets: List[Dict[str, Any]] = []
    for exp, run in full[full["experiment_id"].isin(rows["experiment_id"].unique())].groupby("experiment_id"):
        run = run.sort_values("second")
        cur = run["current_congestion"].fillna(False).astype(bool).to_numpy()
        starts = np.flatnonzero(cur & ~np.r_[False, cur[:-1]])
        w = warned[warned["experiment_id"] == exp].set_index("second")["_pred"]
        for s in starts:
            window = [t for t in range(max(0, s - horizon), s) if t in w.index]
            hits = [t for t in window if w[t] == 1]
            evaluable = len(window) > 0
            onsets.append({"experiment_id": exp, "onset_second": int(s), "evaluable": evaluable,
                           "detected": bool(hits), "lead_s": int(s - min(hits)) if hits else None})
    ev = [o for o in onsets if o["evaluable"]]
    leads = [o["lead_s"] for o in ev if o["detected"]]
    return {
        "onsets": len(ev), "onsets_not_evaluable": len(onsets) - len(ev), "detected": sum(o["detected"] for o in ev),
        "detection_rate": (sum(o["detected"] for o in ev) / len(ev)) if ev else None,
        "mean_lead_s": float(np.mean(leads)) if leads else None,
        "median_lead_s": float(np.median(leads)) if leads else None, "details": onsets,
    }


# ------------------------------------------------------------------------- evaluation
def evaluate_method(rows: pd.DataFrame, score: np.ndarray, threshold: float, full: pd.DataFrame) -> Dict[str, Any]:
    pred = (score >= threshold).astype(int) if score.dtype != bool else score.astype(int)
    y = rows["future_congestion"].astype(int).to_numpy()
    calm = ~rows["current_congestion"].fillna(False).astype(bool).to_numpy()
    out = {
        "threshold": float(threshold),
        "all_rows": binary_metrics(y, pred, score),
        "not_yet_congested": binary_metrics(y[calm], pred[calm], score[calm]),
        "lead_time": lead_times(rows, pred, full),
    }
    out["lead_time"].pop("details", None)
    return out


def run_congestion(data: MLData, seed: int = 0, n_jobs: int = 2) -> Dict[str, Any]:
    full = data.frame
    train = data.rows("train", need="valid_for_prediction")
    val = data.rows("val", need="valid_for_prediction")
    y_train = train["future_congestion"].astype(int).to_numpy()
    results: Dict[str, Any] = {
        "horizon_s": HORIZON_S, "train_rows": int(len(train)), "train_positives": int(y_train.sum()),
        "methods": {}, "notes": [],
    }
    if y_train.sum() == 0 or y_train.sum() == len(y_train):
        results["notes"].append("training data contains a single class: models were not trained")
        return results
    if val.empty or val["future_congestion"].astype(int).sum() == 0:
        results["notes"].append("no positive validation rows: thresholds fall back to defaults (0.5 / 80 % / slope 0)")
    spw = float((y_train == 0).sum() / max(y_train.sum(), 1))
    X_train = data.X(train, scaled=True)
    fitted: Dict[str, Any] = {}

    def thr_for(score_val: Optional[np.ndarray], default: float) -> float:
        if val.empty or val["future_congestion"].astype(int).sum() == 0 or score_val is None:
            return default
        return best_f1_threshold(val["future_congestion"].astype(int).to_numpy(), score_val)

    # ML models
    for name, model in make_models(seed, n_jobs, spw).items():
        model.fit(X_train, y_train)
        fitted[name] = model
        proba = lambda rows, m=model: m.predict_proba(data.X(rows, scaled=True))[:, 1]  # noqa: E731
        threshold = thr_for(proba(val) if len(val) else None, 0.5)
        entry: Dict[str, Any] = {"type": "model", "model_size_kb": model_size_kb(model)}
        for split in ("val", "test"):
            rows = data.rows(split, need="valid_for_prediction")
            if rows.empty:
                continue
            entry[split] = evaluate_method(rows, proba(rows), threshold, full)
        test_rows = data.rows("test", need="valid_for_prediction")
        sample = data.X(test_rows if len(test_rows) else train, scaled=True)[:300]
        entry["resources"] = resource_profile(lambda x, m=model: m.predict_proba(x), sample)
        results["methods"][name] = entry

    # baselines (same rows, same threshold-selection rule)
    defaults = {"persistence": 0.5, "utilisation_gt_80": float(np.nextafter(UTIL_BASELINE_PCT, np.inf)),  # strictly > 80 %
                "utilisation_trend": 0.0}
    val_scores = baseline_scores(val) if len(val) else {}
    for name, default in defaults.items():
        threshold = default if name != "utilisation_trend" else thr_for(val_scores.get(name), default)
        entry = {"type": "baseline"}
        for split in ("val", "test"):
            rows = data.rows(split, need="valid_for_prediction")
            if rows.empty:
                continue
            entry[split] = evaluate_method(rows, baseline_scores(rows)[name], threshold, full)
        results["methods"][name] = entry
    results["_fitted"] = fitted
    return results
