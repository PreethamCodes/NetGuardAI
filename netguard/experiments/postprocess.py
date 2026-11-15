"""Calibration and (re)labelling of stored experiments."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from netguard.exceptions import NetGuardError
from netguard.experiments import dataset as ds
from netguard.experiments.calibration import (
    compute_threshold, effective_labeling, load_calibration, write_calibration,
)
from netguard.experiments.config import LabelingConfig
from netguard.experiments.labels import label_observations

logger = logging.getLogger(__name__)

LABEL_COLUMNS = ("scenario_label", "congestion_condition", "current_congestion", "future_congestion",
                 "time_to_congestion", "future_label_available", "labels_status")


def calibrate(data_dir: Path, labeling: LabelingConfig, *, campaign: Optional[str] = None,
              force: bool = False) -> Dict[str, Any]:
    """Derive X from the successful NORMAL experiments and store it in ``calibration.json``."""
    settings = labeling.calibration
    metas = [m for m in ds.list_metadata(data_dir)
             if m["scenario"] == settings.source_scenario and m["status"] == ds.STATUS_SUCCESS
             and (campaign is None or m["campaign"] == campaign)]
    if not metas:
        raise NetGuardError(f"no successful '{settings.source_scenario}' experiments to calibrate from")
    q, u = [], []
    for m in metas:
        frame = ds.read_parquet(data_dir / "processed" / f"{m['experiment_id']}.parquet")
        q += frame["queue_delay_ms"].tolist()
        u += frame["utilisation_pct"].tolist()
    result = compute_threshold(q, u, settings)
    path = write_calibration(data_dir, result, [m["experiment_id"] for m in metas], settings, force=force)
    logger.info("calibrated X = %.0f ms (raw p%.1f = %.3f ms over %d s) -> %s", result["queue_delay_threshold_ms"],
                settings.percentile, result["raw_percentile_ms"], result["samples_used"], path)
    return result


def relabel_experiment(data_dir: Path, experiment_id: str, labeling: LabelingConfig) -> None:
    """Recompute the label columns of one stored experiment with the (fixed) threshold."""
    meta = ds.load_metadata(ds.metadata_path(data_dir, experiment_id))
    path = data_dir / "processed" / f"{experiment_id}.parquet"
    if not path.is_file():
        return
    frame = ds.read_parquet(path)
    frame = frame.drop(columns=[c for c in LABEL_COLUMNS if c in frame.columns])
    labelled = label_observations(frame, labeling, meta["scenario_label"])
    ds.write_parquet(labelled, path, {"experiment": experiment_id, "status": meta["status"],
                                      "scenario": meta["scenario"], "seed": meta["seed"]})
    meta["labels"] = {"status": "labelled", **labeling.to_dict()}
    meta["files"]["processed"] = str(path)
    ds.metadata_path(data_dir, experiment_id).write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")


def relabel_all(data_dir: Path, labeling: LabelingConfig) -> List[str]:
    labeling = effective_labeling(labeling, data_dir)
    if labeling.queue_delay_threshold_ms is None:
        raise NetGuardError("X is not calibrated: run 'experiment calibrate' first")
    done = []
    for m in ds.list_metadata(data_dir):
        if m["status"] in (ds.STATUS_RUNNING,):
            continue
        relabel_experiment(data_dir, m["experiment_id"], labeling)
        done.append(m["experiment_id"])
    return done


def calibrate_and_label(data_dir: Path, labeling: LabelingConfig, *, campaign: Optional[str] = None) -> Dict[str, Any]:
    result = calibrate(data_dir, labeling, campaign=campaign)
    relabel_all(data_dir, labeling)
    return result
