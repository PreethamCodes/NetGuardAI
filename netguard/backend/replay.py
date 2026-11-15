"""Historical replay of recorded experiment runs (read from the Phase 2/3 Parquet files).

Everything returned here is marked ``source = "replay"`` / ``historical = true`` and carries the run's
ground-truth labels *separately* from the model predictions, so a viewer can compare them. Rows from
the training and validation runs are flagged because the models have seen (or been tuned on) them;
only ``split = "test"`` runs are unseen by the models.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from netguard.backend.inference import Predictor, _clean
from netguard.exceptions import NetGuardError
from netguard.prep.features import TARGET_COLUMNS

logger = logging.getLogger(__name__)

#: raw measurements shown on the dashboard (from data/processed/<id>.parquet)
DISPLAY_COLUMNS = (
    "throughput_mbps", "fwd_throughput_mbps", "rev_throughput_mbps", "fwd_pps", "utilisation_pct", "rtt_mean_ms",
    "jitter_ms", "probe_loss_pct", "loss_pct", "queue_delay_ms", "fwd_queue_backlog_bytes", "fwd_queue_backlog_packets",
    "active_connections", "tcp_retransmissions", "cpu_percent", "ram_percent", "nf_active_flows", "nf_new_flows",
    "tshark_pkts", "tshark_tcp_pkts", "tshark_udp_pkts", "tshark_icmp_pkts", "tshark_dns_pkts", "tshark_http_pkts",
)
GROUND_TRUTH = ("scenario_label", "current_congestion", "future_congestion", "time_to_congestion",
                "future_label_available")


class ReplayStore:
    def __init__(self, data_dir: Path, predictor: Predictor) -> None:
        self.data_dir = data_dir
        self.predictor = predictor
        path = data_dir / "features" / "features.parquet"
        if not path.is_file():
            raise NetGuardError(f"{path} not found: run 'python -m netguard prepare' first")
        self.features = pd.read_parquet(path)
        self._cache: Dict[str, Dict[str, Any]] = {}

    def runs(self) -> List[Dict[str, Any]]:
        g = self.features.groupby("experiment_id")
        out = []
        for exp, grp in g:
            first = grp.iloc[0]
            out.append({
                "experiment_id": exp, "scenario": str(first["scenario"]), "scenario_label": str(first["scenario_label"]),
                "repetition": int(first["repetition"]), "split": str(first["split"]), "seconds": int(len(grp)),
                "seen_by_models": str(first["split"]) != "test",
                "source": "replay", "historical": True,
            })
        return sorted(out, key=lambda r: (r["scenario"], r["repetition"]))

    def timeline(self, experiment_id: str) -> Dict[str, Any]:
        if experiment_id in self._cache:
            return self._cache[experiment_id]
        run = self.features[self.features["experiment_id"] == experiment_id].sort_values("second")
        if run.empty:
            raise KeyError(experiment_id)
        processed = pd.read_parquet(self.data_dir / "processed" / f"{experiment_id}.parquet").sort_values("second")
        preds = self.predictor.predict(run)
        records: List[Dict[str, Any]] = []
        alerts: List[Dict[str, Any]] = []
        for (_, f), (_, p), pred in zip(run.iterrows(), processed.iterrows(), preds):
            metrics = {c: _clean(p[c]) for c in DISPLAY_COLUMNS if c in processed.columns}
            rec = {
                "source": "replay", "historical": True, "experiment_id": experiment_id,
                "second": int(f["second"]), "timestamp_s": _clean(f["timestamp_s"]),
                "metrics": metrics, "predictions": pred,
                "ground_truth": {c: _clean(f[c]) for c in GROUND_TRUTH if c in run.columns},
            }
            records.append(rec)
            alerts += alerts_for(pred, rec["second"], rec["timestamp_s"])
        first = run.iloc[0]
        out = {
            "experiment_id": experiment_id, "scenario": str(first["scenario"]), "split": str(first["split"]),
            "repetition": int(first["repetition"]), "source": "replay", "historical": True,
            "seen_by_models": str(first["split"]) != "test", "records": records, "alerts": alerts,
        }
        self._cache[experiment_id] = out
        return out


def alerts_for(pred: Dict[str, Any], second: int, timestamp_s: Optional[float]) -> List[Dict[str, Any]]:
    """Alerts raised by one prediction: unusual traffic (with its explanation) and congestion warnings."""
    if not pred.get("available"):
        return []
    out: List[Dict[str, Any]] = []
    an, cg = pred["anomaly"], pred["congestion"]
    if an["flagged"]:
        out.append({"second": second, "timestamp_s": timestamp_s, "kind": "anomaly", "severity": an["severity"] or "low",
                    "title": f"Unusual traffic: {an['kind']}", "explanation": an["reasons"]})
    if cg["warning"] and not cg["currently_congested"]:
        out.append({"second": second, "timestamp_s": timestamp_s, "kind": "congestion", "severity": "high",
                    "title": f"Congestion expected within {cg['horizon_s']} s (probability {cg['probability']:.0%})",
                    "explanation": [f"model probability {cg['probability']:.2f} >= threshold {cg['threshold']:.2f}"]})
    return out
