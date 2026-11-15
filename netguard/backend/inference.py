"""Model inference for the backend: features -> classification, anomaly and congestion predictions.

* Models come from ``models/*.joblib`` written by ``python -m netguard train``; decision thresholds from
  ``results/ml_results.json`` (chosen on validation runs, never on test data).
* A prediction is only produced for seconds with a **complete** feature vector. The first 30 s of a
  stream/run have no lag features, so they return ``available = False`` with a reason - nothing is
  guessed or filled in.
* ``time_to_congestion`` is an evaluation helper and is never predicted or shown as a prediction.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

from netguard.exceptions import NetGuardError
from netguard.ml.anomaly import anomaly_score

logger = logging.getLogger(__name__)

HORIZON_S = 30
SEVERITY = {"congestion": "high", "flood-like": "high", "degradation": "medium", "legitimate burst": "low",
            "unclassified anomaly": "low"}


def _clean(value: Any) -> Any:
    """JSON-safe scalar: NaN/inf/NA -> None, numpy/pandas scalars -> python."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    return value


@dataclass
class ModelChoice:
    classifier: str = "xgboost"
    anomaly: str = "isolation_forest"
    congestion: str = "logistic_regression"


class Predictor:
    def __init__(self, models_dir: Path, results_dir: Path, choice: Optional[ModelChoice] = None) -> None:
        self.choice = choice or ModelChoice()
        self.models_dir, self.results_dir = models_dir, results_dir
        self.classifier = self._load(f"classification_{self.choice.classifier}")
        self.anomaly = self._load(f"anomaly_{self.choice.anomaly}")
        self.congestion = self._load(f"congestion_{self.choice.congestion}")
        self.columns: List[str] = self.classifier["columns"]
        for bundle in (self.anomaly, self.congestion):
            if bundle["columns"] != self.columns:
                raise NetGuardError("model bundles were trained on different feature columns")
        self.classes = self._classes()
        self.congestion_threshold = self._congestion_threshold()
        self.anomaly_threshold = float(self.anomaly["threshold"])
        self.interpreter = self.anomaly["interpreter"]

    def _load(self, name: str) -> Dict[str, Any]:
        path = self.models_dir / f"{name}.joblib"
        if not path.is_file():
            raise NetGuardError(f"model file {path} not found: run 'python -m netguard train'")
        return joblib.load(path)

    def _results(self) -> Dict[str, Any]:
        path = self.results_dir / "ml_results.json"
        if not path.is_file():
            raise NetGuardError(f"{path} not found: run 'python -m netguard train'")
        return json.loads(path.read_text())

    def _classes(self) -> List[str]:
        return list(self._results()["classification"]["classes"])

    def _congestion_threshold(self) -> float:
        methods = self._results()["congestion"]["methods"]
        entry = methods[self.choice.congestion]
        for split in ("val", "test"):
            if split in entry:
                return float(entry[split]["threshold"])
        return 0.5

    # ------------------------------------------------------------------ prediction
    def predict(self, features: pd.DataFrame) -> List[Dict[str, Any]]:
        """One prediction dict per row of ``features`` (a table with the model input columns)."""
        n = len(features)
        X = features[self.columns].to_numpy(dtype="float64")
        complete = ~np.isnan(X).any(axis=1)
        out: List[Dict[str, Any]] = [
            {"available": False, "reason": "warming up: fewer than 30 s of history or a missing measurement"}
            for _ in range(n)
        ]
        if not complete.any():
            return out
        idx = np.flatnonzero(complete)
        Xc = X[idx]
        scaled = self.classifier["scaler"].transform(Xc)
        class_proba = self.classifier["model"].predict_proba(Xc)  # classification models were trained unscaled
        score = anomaly_score(self.anomaly["model"], scaled)
        cong_proba = self.congestion["model"].predict_proba(scaled)[:, 1]
        for j, i in enumerate(idx):
            row = features.iloc[i]
            flagged = bool(score[j] > self.anomaly_threshold)
            kind: Optional[str] = None
            reasons: List[str] = []
            if flagged:
                kind, reasons = self.interpreter.explain(row)
            prob = float(cong_proba[j])
            out[i] = {
                "available": True,
                "classification": {
                    "label": self.classes[int(np.argmax(class_proba[j]))],
                    "probabilities": {c: float(p) for c, p in zip(self.classes, class_proba[j])},
                },
                "anomaly": {
                    "score": float(score[j]), "threshold": self.anomaly_threshold, "flagged": flagged,
                    "kind": kind, "severity": SEVERITY.get(kind) if kind else None, "reasons": reasons,
                    "note": "An anomaly score shows unusual traffic, not that it is malicious.",
                },
                "congestion": {
                    "probability": prob, "threshold": self.congestion_threshold,
                    "warning": bool(prob >= self.congestion_threshold),
                    "horizon_s": HORIZON_S, "window": [1, HORIZON_S],
                    "currently_congested": _clean(row.get("congested_causal")) == 1.0,
                },
            }
        return out

    def describe(self) -> Dict[str, Any]:
        return {
            "classifier": self.choice.classifier, "anomaly": self.choice.anomaly, "congestion": self.choice.congestion,
            "classes": self.classes, "feature_count": len(self.columns),
            "anomaly_threshold": self.anomaly_threshold, "congestion_threshold": self.congestion_threshold,
        }
