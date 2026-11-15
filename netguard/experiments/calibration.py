"""Calibration of the queue-delay threshold X from NORMAL experiments.

Method (approved before implementation):

1. Take all *successful* experiments of the calibration scenario (NORMAL).
2. Pool every second whose utilisation is below ``max_utilisation_pct`` (default 90 %) and whose
   queue delay was measured; the queue delay of a second is the worse of the two directions.
3. ``X = ceil(percentile_99.9(pooled queue delay))`` in whole milliseconds, never below
   ``min_threshold_ms`` (default 1 ms, so the condition cannot degenerate to "always true").

The result is written once to ``data/metadata/calibration.json`` together with the
inputs (experiment IDs, sample counts, the raw percentile) and then stays fixed:
:func:`write_calibration` refuses to overwrite it unless ``force`` is given.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from netguard.exceptions import ConfigError, NetGuardError
from netguard.experiments.config import CalibrationSettings, LabelingConfig

CALIBRATION_FILE = "calibration.json"


def compute_threshold(
    queue_delay_ms: Sequence[float], utilisation_pct: Sequence[float], settings: CalibrationSettings,
) -> Dict[str, Any]:
    """Pure calculation of X from pooled per-second values."""
    q = pd.Series(list(queue_delay_ms), dtype="float64")
    u = pd.Series(list(utilisation_pct), dtype="float64")
    keep = q.notna() & u.notna() & (u < settings.max_utilisation_pct)
    pooled = q[keep].to_numpy()
    if pooled.size < 100:
        raise NetGuardError(
            f"calibration needs more NORMAL data: only {pooled.size} usable seconds (need at least 100)"
        )
    raw = float(np.percentile(pooled, settings.percentile))
    threshold = max(float(math.ceil(raw)), settings.min_threshold_ms)
    return {
        "queue_delay_threshold_ms": threshold,
        "raw_percentile_ms": raw,
        "percentile": settings.percentile,
        "samples_used": int(pooled.size),
        "queue_delay_ms_max": float(pooled.max()),
        "queue_delay_ms_mean": float(pooled.mean()),
        "floor_applied": threshold > math.ceil(raw),
    }


def calibration_path(data_dir: Path) -> Path:
    return data_dir / "metadata" / CALIBRATION_FILE


def load_calibration(data_dir: Path) -> Optional[Dict[str, Any]]:
    path = calibration_path(data_dir)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_calibration(data_dir: Path, result: Dict[str, Any], experiment_ids: List[str],
                      settings: CalibrationSettings, force: bool = False) -> Path:
    path = calibration_path(data_dir)
    if path.exists() and not force:
        raise NetGuardError(f"{path} already exists: the threshold is fixed once calibrated (use --force to replace)")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **result,
        "method": ("X = ceil(percentile of per-second bottleneck queue delay (max of both directions) "
                   "over seconds with utilisation below max_utilisation_pct, pooled over the experiments)"),
        "source_scenario": settings.source_scenario,
        "max_utilisation_pct": settings.max_utilisation_pct,
        "min_threshold_ms": settings.min_threshold_ms,
        "experiment_ids": experiment_ids,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def effective_labeling(labeling: LabelingConfig, data_dir: Path) -> LabelingConfig:
    """``labeling`` with X filled in from the YAML, else from the stored calibration."""
    if labeling.queue_delay_threshold_ms is not None:
        return labeling
    stored = load_calibration(data_dir)
    if stored is None:
        return labeling
    return labeling.with_threshold(float(stored["queue_delay_threshold_ms"]))
