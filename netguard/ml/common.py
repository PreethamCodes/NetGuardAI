"""Shared helpers for the Phase 4/5 models: data access, metrics and resource measurements.

Rules
-----
* Models only ever see the columns listed in ``feature_columns.json["all"]``.
* Rows are split by run (``split`` column written by the Phase 3 pipeline); nothing is re-split here.
* Scaling uses the scaler fitted on the training rows in Phase 3.
* Rows with missing features are *excluded*, never imputed.
"""

from __future__ import annotations

import json
import os
import pickle
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from netguard.exceptions import NetGuardError

SPLITS = ("train", "val", "test")


@dataclass
class MLData:
    frame: pd.DataFrame
    columns: List[str]
    scaler: Any
    scheme: str = ""

    def rows(self, split: str, *, need: str = "features_complete") -> pd.DataFrame:
        """Rows of ``split`` that satisfy ``need`` (a boolean column of the feature table)."""
        sub = self.frame[self.frame["split"] == split]
        return sub[sub[need].astype(bool)]

    def X(self, rows: pd.DataFrame, scaled: bool = False) -> np.ndarray:
        values = rows[self.columns].to_numpy(dtype="float64")
        return self.scaler.transform(values) if scaled else values


def load_ml_data(data_dir: Path) -> MLData:
    """Load ``data/features`` written by ``python -m netguard prepare``."""
    import joblib

    folder = data_dir / "features"
    path = folder / "features.parquet"
    if not path.is_file():
        raise NetGuardError(f"{path} not found: run 'python -m netguard prepare' first")
    frame = pd.read_parquet(path)
    columns = json.loads((folder / "feature_columns.json").read_text())["all"]
    bundle = joblib.load(folder / "scaler.joblib")
    if bundle["columns"] != columns:
        raise NetGuardError("scaler columns do not match feature_columns.json")
    return MLData(frame, columns, bundle["scaler"])


# ---------------------------------------------------------------------------- metrics
def binary_metrics(y_true: Sequence[int], y_pred: Sequence[int], score: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """Accuracy, precision, recall, F1, false-positive rate and (if scores given) PR-AUC. Undefined values are None."""
    from sklearn.metrics import average_precision_score

    y = np.asarray(y_true).astype(int)
    p = np.asarray(y_pred).astype(int)
    tp = int(((y == 1) & (p == 1)).sum())
    fp = int(((y == 0) & (p == 1)).sum())
    fn = int(((y == 1) & (p == 0)).sum())
    tn = int(((y == 0) & (p == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
    out: Dict[str, Any] = {
        "n": int(len(y)), "positives": int(y.sum()), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": (tp + tn) / len(y) if len(y) else None, "precision": prec, "recall": rec, "f1": f1,
        "fpr": fp / (fp + tn) if fp + tn else None,
    }
    if score is not None and y.sum() > 0 and y.sum() < len(y):
        out["pr_auc"] = float(average_precision_score(y, np.asarray(score)))
    else:
        out["pr_auc"] = None  # undefined without both classes
    return out


def multiclass_metrics(y_true: Sequence[str], y_pred: Sequence[str], proba: Optional[np.ndarray],
                       classes: Sequence[str]) -> Dict[str, Any]:
    from sklearn.metrics import average_precision_score

    y = np.asarray(y_true)
    p = np.asarray(y_pred)
    per_class: Dict[str, Any] = {}
    for i, c in enumerate(classes):
        yt, yp = (y == c).astype(int), (p == c).astype(int)
        per_class[c] = binary_metrics(yt, yp, proba[:, i] if proba is not None else None)
    macro = lambda key: float(np.mean([m[key] for m in per_class.values() if m[key] is not None])) if any(  # noqa: E731
        m[key] is not None for m in per_class.values()) else None
    return {
        "n": int(len(y)), "accuracy": float((y == p).mean()) if len(y) else None,
        "macro_precision": macro("precision"), "macro_recall": macro("recall"), "macro_f1": macro("f1"),
        "macro_pr_auc": macro("pr_auc"), "macro_fpr": macro("fpr"), "per_class": per_class,
    }


def best_f1_threshold(y_true: Sequence[int], score: Sequence[float]) -> float:
    """Score threshold with the best F1 on the given (validation) data; 0.5 if there is no positive."""
    from sklearn.metrics import precision_recall_curve

    y = np.asarray(y_true).astype(int)
    if y.sum() == 0 or y.sum() == len(y):
        return 0.5
    prec, rec, thr = precision_recall_curve(y, np.asarray(score))
    f1 = 2 * prec[:-1] * rec[:-1] / np.maximum(prec[:-1] + rec[:-1], 1e-12)
    return float(thr[int(np.argmax(f1))])


# --------------------------------------------------------------------------- resources
def model_size_kb(model: Any) -> float:
    return len(pickle.dumps(model)) / 1024.0


def latency_ms(predict: Callable[[np.ndarray], Any], X: np.ndarray, repeats: int = 200) -> Dict[str, float]:
    """Single-row prediction latency (what a live monitor does once per second)."""
    row = X[:1]
    predict(row)  # warm-up
    times = []
    for i in range(min(repeats, max(len(X), 1) * 5)):
        r = X[i % len(X)][None, :]
        t0 = time.perf_counter()
        predict(r)
        times.append((time.perf_counter() - t0) * 1000.0)
    arr = np.asarray(times)
    return {"mean_ms": float(arr.mean()), "p95_ms": float(np.percentile(arr, 95)), "max_ms": float(arr.max())}


def resource_profile(predict: Callable[[np.ndarray], Any], X: np.ndarray) -> Dict[str, Any]:
    """Latency plus CPU time and peak Python memory while predicting the whole table row by row."""
    tracemalloc.start()
    cpu0, wall0 = time.process_time(), time.perf_counter()
    for i in range(len(X)):
        predict(X[i:i + 1])
    cpu, wall = time.process_time() - cpu0, time.perf_counter() - wall0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        **latency_ms(predict, X),
        "cpu_ms_per_prediction": cpu / max(len(X), 1) * 1000.0,
        "cpu_utilisation_during_predictions_pct": (cpu / wall * 100.0) if wall > 0 else None,
        "peak_python_memory_kb": peak / 1024.0,
        "process_rss_mb": _rss_mb(),
    }


def _rss_mb() -> Optional[float]:
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / 1e6
    except Exception:  # noqa: BLE001
        return None
