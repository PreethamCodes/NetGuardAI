"""Hybrid anomaly detection.

Stage 1 - *unusual?*  Isolation Forest (primary) and Local Outlier Factor (comparison) are fitted on
NORMAL training traffic only. A second is flagged when its anomaly score exceeds a threshold taken
from the NORMAL *validation* runs (the ``target_fpr`` quantile of their scores); without a validation
run the NORMAL training scores are used and the report says so.

Stage 2 - *what kind of unusual?*  Transparent rules (:class:`AnomalyInterpreter`) read a flagged
second and name the most likely cause with the evidence. An anomaly score alone does not establish
that traffic is malicious, so the rules only say "flood-like", "legitimate burst", "degradation" or
"congestion", with the feature values that triggered them. The rules use quantiles of NORMAL training
traffic as reference and never look at the scenario label.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from netguard.ml.common import MLData, SPLITS, binary_metrics, model_size_kb, resource_profile

logger = logging.getLogger(__name__)

NORMAL = "NORMAL"
#: what each non-NORMAL scenario is expected to be called by the interpreter (for evaluation only)
EXPECTED_KIND = {"UDP_FLOOD": "flood-like", "FLASH_CROWD": "legitimate burst", "DEGRADED": "degradation",
                 "RAMP": "congestion"}


@dataclass
class AnomalyInterpreter:
    """Rule based second stage. ``ref`` holds NORMAL-traffic reference quantiles."""

    ref: Dict[str, float]

    @classmethod
    def fit(cls, normal_rows: pd.DataFrame) -> "AnomalyInterpreter":
        q = lambda col, p: float(normal_rows[col].quantile(p))  # noqa: E731
        return cls({
            "rtt_p99": q("rtt_mean_ms", 0.99), "jitter_p99": q("jitter_ms", 0.99),
            "new_flows_p99": q("nf_new_flows", 0.99), "conn_p99": q("active_connections", 0.99),
            "pps_p99": q("fwd_pps", 0.99), "util_p99": q("utilisation_pct", 0.99),
            "syn_p99": q("syn_ratio", 0.99) if normal_rows["syn_ratio"].notna().any() else float("nan"),
        })

    def explain(self, row: pd.Series) -> Tuple[str, List[str]]:
        r = self.ref
        why: List[str] = []
        if row.get("congested_causal") == 1.0:
            return "congestion", [f"utilisation {row['utilisation_pct']:.0f}% with queue delay "
                                  f"{row['queue_delay_ms']:.0f} ms / loss {row['loss_pct']:.1f}% for 3+ s"]
        udp_heavy = row["share_udp"] >= 0.8
        high_load = row["utilisation_pct"] >= max(50.0, r["util_p99"])
        if udp_heavy and high_load and row["nf_new_flows"] <= max(r["new_flows_p99"], 1):
            return "flood-like", [f"UDP is {row['share_udp']:.0%} of packets", f"utilisation {row['utilisation_pct']:.0f}% "
                                  f"(NORMAL p99 {r['util_p99']:.0f}%)", f"few new flows ({row['nf_new_flows']:.0f}): "
                                  "sustained high-rate stream, not many clients"]
        many_new = row["nf_new_flows"] > r["new_flows_p99"] or row["active_connections"] > r["conn_p99"]
        if many_new and row["share_tcp"] >= 0.5 and row["loss_pct"] <= 1.0:
            return "legitimate burst", [f"{row['nf_new_flows']:.0f} new flows (NORMAL p99 {r['new_flows_p99']:.0f})",
                                        f"{row['active_connections']:.0f} connections (NORMAL p99 {r['conn_p99']:.0f})",
                                        "mostly TCP and no sustained loss: many clients, not a single stream"]
        slow = row["rtt_mean_ms"] > r["rtt_p99"] or row["jitter_ms"] > r["jitter_p99"] or row["loss_pct"] > 1.0 \
            or row["probe_loss_pct"] > 1.0
        if slow and row["utilisation_pct"] < 50.0:
            if row["rtt_mean_ms"] > r["rtt_p99"]:
                why.append(f"RTT {row['rtt_mean_ms']:.0f} ms (NORMAL p99 {r['rtt_p99']:.0f} ms)")
            if row["loss_pct"] > 1.0 or row["probe_loss_pct"] > 1.0:
                why.append(f"loss {max(row['loss_pct'], row['probe_loss_pct']):.1f}%")
            if row["jitter_ms"] > r["jitter_p99"]:
                why.append(f"jitter {row['jitter_ms']:.1f} ms")
            return "degradation", why + [f"while utilisation is only {row['utilisation_pct']:.0f}%"]
        return "unclassified anomaly", ["unusual feature combination without a matching rule"]


def make_detectors(seed: int = 0, n_jobs: int = 2) -> Dict[str, Any]:
    from sklearn.ensemble import IsolationForest
    from sklearn.neighbors import LocalOutlierFactor

    return {
        "isolation_forest": IsolationForest(n_estimators=200, contamination="auto", random_state=seed, n_jobs=n_jobs),
        "lof": LocalOutlierFactor(n_neighbors=20, novelty=True, n_jobs=n_jobs),
    }


def anomaly_score(model: Any, X: np.ndarray) -> np.ndarray:
    """Higher = more unusual (sklearn's decision_function is higher = more normal)."""
    return -model.decision_function(X)


def run_anomaly(data: MLData, seed: int = 0, n_jobs: int = 2, target_fpr: float = 0.05) -> Dict[str, Any]:
    train = data.rows("train")
    normal_train = train[train["scenario_label"] == NORMAL]
    if normal_train.empty:
        raise ValueError("no NORMAL training rows to fit the anomaly detectors on")
    X_fit = data.X(normal_train, scaled=True)
    interpreter = AnomalyInterpreter.fit(normal_train)
    results: Dict[str, Any] = {
        "trained_on": {"scenario": NORMAL, "rows": int(len(normal_train))},
        "target_fpr_on_normal": target_fpr, "interpreter_reference": interpreter.ref, "models": {},
    }
    fitted: Dict[str, Any] = {}
    val_normal = data.rows("val")
    val_normal = val_normal[val_normal["scenario_label"] == NORMAL]
    for name, model in make_detectors(seed, n_jobs).items():
        model.fit(X_fit)
        fitted[name] = model
        calib = val_normal if len(val_normal) else normal_train
        threshold = float(np.quantile(anomaly_score(model, data.X(calib, scaled=True)), 1.0 - target_fpr))
        entry: Dict[str, Any] = {
            "threshold": threshold,
            "threshold_source": "NORMAL validation runs" if len(val_normal) else "NORMAL training rows (no validation run)",
        }
        for split in SPLITS:
            rows = data.rows(split)
            if rows.empty:
                continue
            score = anomaly_score(model, data.X(rows, scaled=True))
            flagged = score > threshold
            y = (rows["scenario_label"] != NORMAL).astype(int).to_numpy()
            entry[split] = {
                "binary_vs_normal": binary_metrics(y, flagged.astype(int), score),
                "flag_rate_by_scenario": {s: float(flagged[(rows["scenario_label"] == s).to_numpy()].mean())
                                          for s in sorted(rows["scenario_label"].unique())},
                "interpretation": _interpretation(rows, flagged, interpreter),
            }
        entry["model_size_kb"] = model_size_kb(model)
        sample = data.X(data.rows("test") if len(data.rows("test")) else train, scaled=True)[:300]
        entry["resources"] = resource_profile(lambda x, m=model: anomaly_score(m, x), sample)
        results["models"][name] = entry
    results["_fitted"] = fitted
    results["_interpreter"] = interpreter
    results["notes"] = [
        "FLASH_CROWD is legitimate traffic: a high flag rate there is a false-positive risk at stage 1; stage 2 "
        "should then call it a 'legitimate burst'.",
        "The anomaly score alone does not say that traffic is malicious.",
    ]
    return results


def _interpretation(rows: pd.DataFrame, flagged: np.ndarray, interpreter: AnomalyInterpreter) -> Dict[str, Any]:
    kinds: Dict[str, Dict[str, int]] = {}
    correct = total = 0
    for (_, row), is_flag in zip(rows.iterrows(), flagged):
        if not is_flag:
            continue
        kind, _ = interpreter.explain(row)
        scenario = row["scenario_label"]
        kinds.setdefault(scenario, {}).setdefault(kind, 0)
        kinds[scenario][kind] += 1
        if scenario in EXPECTED_KIND:
            total += 1
            correct += int(kind == EXPECTED_KIND[scenario])
    return {"kinds_by_scenario": kinds, "rule_agreement_on_anomalous_scenarios": (correct / total) if total else None,
            "flagged_rows_judged": total}
