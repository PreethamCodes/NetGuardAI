"""Train and evaluate all Phase 4 models and write ``results/ml_results.json|md`` and ``models/*.joblib``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

import joblib

from netguard.ml.anomaly import run_anomaly
from netguard.ml.classification import run_classification
from netguard.ml.common import load_ml_data
from netguard.ml.congestion import run_congestion

logger = logging.getLogger(__name__)


def _public(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _f(x: Any, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def train_all(data_dir: Path, results_dir: Path, models_dir: Path, seed: int = 0, n_jobs: int = 2) -> Dict[str, Any]:
    data = load_ml_data(data_dir)
    logger.info("loaded %d feature rows, %d features", len(data.frame), len(data.columns))
    classification = run_classification(data, seed, n_jobs)
    anomaly = run_anomaly(data, seed, n_jobs)
    congestion = run_congestion(data, seed, n_jobs)

    models_dir.mkdir(parents=True, exist_ok=True)
    for group, result in (("classification", classification), ("congestion", congestion)):
        for name, model in result.get("_fitted", {}).items():
            joblib.dump({"model": model, "columns": data.columns, "scaler": data.scaler}, models_dir / f"{group}_{name}.joblib")
    for name, model in anomaly["_fitted"].items():
        joblib.dump({"model": model, "columns": data.columns, "scaler": data.scaler,
                     "threshold": anomaly["models"][name]["threshold"], "interpreter": anomaly["_interpreter"]},
                    models_dir / f"anomaly_{name}.joblib")

    report = {"classification": _public(classification), "anomaly": _public(anomaly), "congestion": _public(congestion)}
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "ml_results.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    (results_dir / "ml_results.md").write_text(markdown(report))
    return report


def markdown(r: Dict[str, Any]) -> str:
    L = ["# Phase 4/5 model results", "", "All numbers come from runs held out by experiment (see prep_report.md). "
         "Where a value is `n/a` the metric is undefined for the data available.", "", "## Traffic classification", "",
         "| model | split | accuracy | macro F1 | macro PR-AUC | size KB | latency ms |", "|---|---|---|---|---|---|---|"]
    for name, m in r["classification"]["models"].items():
        for split in ("val", "test"):
            if split in m:
                L.append(f"| {name} | {split} | {_f(m[split]['accuracy'])} | {_f(m[split]['macro_f1'])} | "
                         f"{_f(m[split]['macro_pr_auc'])} | {_f(m['model_size_kb'], 0)} | {_f(m['resources']['mean_ms'], 2)} |")
    L += ["", "## Anomaly detection (positive = any non-NORMAL second)", "",
          "| model | split | precision | recall | F1 | PR-AUC | FPR | size KB | latency ms |", "|---|---|---|---|---|---|---|---|---|"]
    for name, m in r["anomaly"]["models"].items():
        for split in ("val", "test"):
            if split in m:
                b = m[split]["binary_vs_normal"]
                L.append(f"| {name} | {split} | {_f(b['precision'])} | {_f(b['recall'])} | {_f(b['f1'])} | {_f(b['pr_auc'])} | "
                         f"{_f(b['fpr'])} | {_f(m['model_size_kb'], 0)} | {_f(m['resources']['mean_ms'], 2)} |")
    L += ["", "Flag rate by scenario (test):", "", "| model | " + " | ".join(
        sorted({s for m in r["anomaly"]["models"].values() if "test" in m for s in m["test"]["flag_rate_by_scenario"]})) + " |"]
    scen = sorted({s for m in r["anomaly"]["models"].values() if "test" in m for s in m["test"]["flag_rate_by_scenario"]})
    L.append("|---|" + "---|" * len(scen))
    for name, m in r["anomaly"]["models"].items():
        if "test" in m:
            L.append(f"| {name} | " + " | ".join(_f(m["test"]["flag_rate_by_scenario"].get(s)) for s in scen) + " |")
    L += ["", "## Congestion prediction (next 30 s)", ""]
    for note in r["congestion"].get("notes", []):
        L.append(f"- {note}")
    L += ["", "| method | split | subset | precision | recall | F1 | PR-AUC | FPR | onsets detected | mean lead s |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in r["congestion"].get("methods", {}).items():
        for split in ("val", "test"):
            if split not in m:
                continue
            for subset in ("all_rows", "not_yet_congested"):
                b = m[split][subset]
                lt = m[split]["lead_time"]
                L.append(f"| {name} | {split} | {subset} | {_f(b['precision'])} | {_f(b['recall'])} | {_f(b['f1'])} | "
                         f"{_f(b['pr_auc'])} | {_f(b['fpr'])} | {lt['detected']}/{lt['onsets']} | {_f(lt['mean_lead_s'], 1)} |")
    L += ["", "No claim of successful 30 s-ahead prediction is made unless the `not_yet_congested` rows and the "
          "detected-onset counts above support it.", ""]
    return "\n".join(L)
