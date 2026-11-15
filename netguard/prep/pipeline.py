"""Phase 3 pipeline: validate real runs -> features -> run-based splits -> scaler -> report.

Reads what Phase 2 wrote (``data/metadata``, ``data/processed``, ``data/raw/<id>/flows.parquet``) and writes

    data/features/features.parquet     one row per second of every *included* run, with a ``split`` column
    data/features/scaler.joblib        StandardScaler fitted on the training rows only
    data/features/feature_columns.json model input column names (labels / IDs excluded)
    results/prep_report.json|md        what was included, excluded, missing, and the class balance
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from netguard.exceptions import NetGuardError
from netguard.experiments import dataset as ds
from netguard.experiments.calibration import effective_labeling
from netguard.experiments.config import LabelingConfig
from netguard.prep.features import TARGET_COLUMNS, build_features, feature_columns
from netguard.prep.splits import assign_splits, check_no_leakage
from netguard.prep.validate import RunCheck, check_run, label_summary

logger = logging.getLogger(__name__)

LAG_MARK = "_lag"


@dataclass
class PrepResult:
    features_path: Path
    scaler_path: Optional[Path]
    columns_path: Path
    report: Dict[str, Any]


def _load_flows(data_dir: Path, experiment_id: str) -> Optional[pd.DataFrame]:
    path = data_dir / "raw" / experiment_id / "flows.parquet"
    if not path.is_file():
        return None
    return pd.read_parquet(path, columns=["protocol", "bidirectional_first_seen_ms", "bidirectional_syn_packets"])


def fit_scaler(train_features: pd.DataFrame, columns: List[str]) -> Any:
    """StandardScaler fitted on the *training* rows only (complete rows)."""
    try:
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise NetGuardError("scikit-learn is required for scaling (pip install -r requirements.txt)") from exc
    if train_features.empty:
        raise NetGuardError("no complete training rows to fit the scaler on")
    return StandardScaler().fit(train_features[columns].to_numpy(dtype="float64"))


def prepare(data_dir: Path, results_dir: Path, labeling: LabelingConfig, *, scheme: str = "three_way",
            expected_rows: Optional[int] = None) -> PrepResult:
    labeling = effective_labeling(labeling, data_dir)
    if labeling.queue_delay_threshold_ms is None:
        raise NetGuardError("the queue-delay threshold X is not calibrated: run 'experiment calibrate' first")
    metas = ds.list_metadata(data_dir)
    if not metas:
        raise NetGuardError(f"no experiment metadata in {data_dir / 'metadata'}")

    checks: List[RunCheck] = []
    tables: List[pd.DataFrame] = []
    for meta in sorted(metas, key=lambda m: (m["scenario"], m["repetition"], m["attempt"])):
        path = data_dir / "processed" / f"{meta['experiment_id']}.parquet"
        obs = ds.read_parquet(path) if path.is_file() else None
        check = check_run(meta, obs, labeling, expected_rows)
        checks.append(check)
        if not check.included:
            logger.warning("excluding %s (%s r%s): %s", meta["experiment_id"], meta["scenario"], meta["repetition"],
                           "; ".join(check.problems))
            continue
        flows = _load_flows(data_dir, meta["experiment_id"])
        if flows is None:
            check.warnings.append("flows.parquet missing: syn_ratio is NaN")
        tables.append(build_features(
            obs, flows=flows, anchor_wall=float(meta["anchor_wall_s"]),
            util_pct=labeling.utilisation_pct, queue_ms=labeling.queue_delay_threshold_ms,
            loss_pct=labeling.loss_pct, min_consecutive=labeling.min_consecutive_s))

    if not tables:
        raise NetGuardError("no valid runs: every run was excluded (see the report)")
    features = pd.concat(tables, ignore_index=True)

    # one retained attempt per (scenario, repetition): the latest successful one
    latest = features.groupby(["scenario", "repetition"])["experiment_id"].max()
    features = features[features["experiment_id"].isin(set(latest))].reset_index(drop=True)

    runs = features[["experiment_id", "scenario", "repetition"]].drop_duplicates()
    runs = assign_splits(runs, scheme)
    features = features.merge(runs[["experiment_id", "split"]], on="experiment_id", how="left")
    leaks = check_no_leakage(features)
    if leaks:
        raise NetGuardError("split leakage: " + "; ".join(leaks))

    cols = feature_columns(features.drop(columns=["split"]))
    nolag = [c for c in cols if LAG_MARK not in c]
    features["features_complete"] = features[cols].notna().all(axis=1)
    features["features_complete_nolag"] = features[nolag].notna().all(axis=1)
    features["valid_for_prediction"] = (
        features["features_complete"] & features["future_label_available"].astype(bool)
        & features["future_congestion"].notna()
    )

    out_dir = data_dir / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    features_path = ds.write_parquet(
        features, out_dir / "features.parquet", {"scheme": scheme, "x_ms": labeling.queue_delay_threshold_ms})
    columns_path = out_dir / "feature_columns.json"
    columns_path.write_text(json.dumps({"all": cols, "no_lag": nolag, "targets": list(TARGET_COLUMNS)}, indent=2) + "\n")

    scaler_path: Optional[Path] = None
    train_rows = features[(features["split"] == "train") & features["features_complete"]]
    if not train_rows.empty:
        import joblib

        scaler_path = out_dir / "scaler.joblib"
        joblib.dump({"scaler": fit_scaler(train_rows, cols), "columns": cols, "fitted_on": "train"}, scaler_path)

    report = build_report(checks, features, cols, scheme, labeling)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "prep_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    (results_dir / "prep_report.md").write_text(report_markdown(report))
    return PrepResult(features_path, scaler_path, columns_path, report)


def build_report(checks: List[RunCheck], features: pd.DataFrame, cols: List[str], scheme: str,
                 labeling: LabelingConfig) -> Dict[str, Any]:
    split_summary: Dict[str, Any] = {}
    for split, grp in features.groupby("split"):
        usable = grp[grp["valid_for_prediction"]]
        split_summary[str(split)] = {
            "runs": int(grp["experiment_id"].nunique()), "rows": int(len(grp)),
            "complete_feature_rows": int(grp["features_complete"].sum()),
            "prediction_rows": int(len(usable)),
            "prediction_positive": int(usable["future_congestion"].astype(bool).sum()),
            "prediction_positive_when_not_congested": int(
                usable[~usable["current_congestion"].fillna(False).astype(bool)]["future_congestion"].astype(bool).sum()),
            "scenarios": sorted(grp["scenario"].unique().tolist()),
        }
    missing = features[cols].isna().mean().sort_values(ascending=False)
    return {
        "scheme": scheme,
        "queue_delay_threshold_ms": labeling.queue_delay_threshold_ms,
        "runs": [c.to_dict() for c in checks],
        "runs_included": sum(c.included for c in checks),
        "runs_excluded": sum(not c.included for c in checks),
        "feature_count": len(cols),
        "rows": int(len(features)),
        "splits": split_summary,
        "labels_by_scenario": label_summary(features),
        "worst_missing_features": {k: round(float(v), 4) for k, v in missing.head(10).items()},
        "notes": [
            "Windows are trailing (past + current second); lags are positive shifts; NaN marks seconds without a full "
            "window or lag (the first 30 s of each run) and is never interpolated.",
            "VM CPU/RAM are in the data but excluded from the model inputs.",
            "syn_ratio = SYN packets of flows started in the second / TCP packets of that second (NFStream flows).",
            "congested_causal uses only seconds <= t (ground-truth current_congestion needs the next 2 s).",
        ],
    }


def report_markdown(report: Dict[str, Any]) -> str:
    lines = ["# Phase 3 preparation report", "",
             f"- split scheme: `{report['scheme']}`, X = {report['queue_delay_threshold_ms']} ms",
             f"- runs included: {report['runs_included']}, excluded: {report['runs_excluded']}",
             f"- feature rows: {report['rows']}, features: {report['feature_count']}", "", "## Runs", "",
             "| experiment | scenario | rep | status | rows | included | problems |", "|---|---|---|---|---|---|---|"]
    for r in report["runs"]:
        lines.append(f"| {r['experiment_id']} | {r['scenario']} | {r['repetition']} | {r['status']} | {r['rows']} | "
                     f"{'yes' if r['included'] else 'NO'} | {'; '.join(r['problems']) or '-'} |")
    lines += ["", "## Splits (by run)", "", "| split | runs | rows | complete | prediction rows | positives | positives (not yet congested) |",
              "|---|---|---|---|---|---|---|"]
    for name, s in report["splits"].items():
        lines.append(f"| {name} | {s['runs']} | {s['rows']} | {s['complete_feature_rows']} | {s['prediction_rows']} | "
                     f"{s['prediction_positive']} | {s['prediction_positive_when_not_congested']} |")
    lines += ["", "## Labels by scenario", "", "| scenario | runs | rows | congested seconds | future-label rows | future positives |",
              "|---|---|---|---|---|---|"]
    for name, s in report["labels_by_scenario"].items():
        lines.append(f"| {name} | {s['runs']} | {s['rows']} | {s['current_congestion_seconds']} | "
                     f"{s['future_label_rows']} | {s['future_congestion_positive']} |")
    lines += ["", "## Notes", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"
