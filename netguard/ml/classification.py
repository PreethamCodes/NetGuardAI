"""Traffic classification: which scenario does this second of traffic look like?

Decision Tree (baseline), Random Forest and XGBoost on the Phase 3 features. Trained on the
training runs, reported on the validation and test runs. Hyper-parameters are fixed and small
(no search) so the comparison stays reproducible and cheap on a CPU.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import numpy as np

from netguard.ml.common import MLData, SPLITS, model_size_kb, multiclass_metrics, resource_profile

logger = logging.getLogger(__name__)


def make_models(seed: int = 0, n_jobs: int = 2) -> Dict[str, Any]:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.tree import DecisionTreeClassifier
    from xgboost import XGBClassifier

    return {
        "decision_tree": DecisionTreeClassifier(max_depth=8, class_weight="balanced", random_state=seed),
        "random_forest": RandomForestClassifier(n_estimators=150, max_depth=12, class_weight="balanced",
                                                n_jobs=n_jobs, random_state=seed),
        "xgboost": XGBClassifier(n_estimators=150, max_depth=4, learning_rate=0.1, subsample=0.9,
                                 n_jobs=n_jobs, random_state=seed, eval_metric="mlogloss"),
    }


def run_classification(data: MLData, seed: int = 0, n_jobs: int = 2) -> Dict[str, Any]:
    train = data.rows("train")
    classes: List[str] = sorted(train["scenario_label"].unique())
    index = {c: i for i, c in enumerate(classes)}
    y_train = train["scenario_label"].map(index).to_numpy()
    X_train = data.X(train)
    results: Dict[str, Any] = {"classes": classes, "train_rows": int(len(train)), "models": {}, "note": (
        "Rows with an incomplete feature window (first 30 s of each run) are excluded, not imputed.")}
    models: Dict[str, Any] = {}
    for name, model in make_models(seed, n_jobs).items():
        model.fit(X_train, y_train)
        models[name] = model
        entry: Dict[str, Any] = {}
        for split in SPLITS:
            rows = data.rows(split)
            if rows.empty:
                continue
            X = data.X(rows)
            proba = model.predict_proba(X)
            pred = np.asarray(classes)[np.argmax(proba, axis=1)]
            known = [c for c in classes]
            entry[split] = multiclass_metrics(rows["scenario_label"].to_numpy(), pred, proba, known)
            entry[split]["unseen_classes_in_split"] = sorted(set(rows["scenario_label"]) - set(classes))
        entry["model_size_kb"] = model_size_kb(model)
        entry["resources"] = resource_profile(model.predict_proba, data.X(data.rows("test") if len(data.rows("test")) else train)[:300])
        entry["top_features"] = _top_features(model, data.columns)
        results["models"][name] = entry
    results["_fitted"] = models
    return results


def _top_features(model: Any, columns: List[str], k: int = 8) -> List[Dict[str, float]]:
    importance = getattr(model, "feature_importances_", None)
    if importance is None:
        return []
    order = np.argsort(importance)[::-1][:k]
    return [{"feature": columns[i], "importance": float(importance[i])} for i in order]
