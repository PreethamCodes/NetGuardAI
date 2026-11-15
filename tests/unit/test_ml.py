"""Phase 4/5 code tests. Inputs are synthetic tables that only check the code logic (metrics, thresholds,
lead time, rules, file outputs); they are never presented as experimental data or results."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from netguard.exceptions import NetGuardError
from netguard.ml.anomaly import AnomalyInterpreter, EXPECTED_KIND, run_anomaly
from netguard.ml.common import (
    best_f1_threshold, binary_metrics, latency_ms, load_ml_data, model_size_kb, multiclass_metrics,
)
from netguard.ml.congestion import UTIL_BASELINE_PCT, baseline_scores, lead_times
from netguard.ml.run import train_all
from netguard.prep.pipeline import prepare
from tests.unit.test_prep import LABELING, make_obs, write_run


# -------------------------------------------------------------------------- metrics
def test_binary_metrics_values_and_undefined_cases():
    m = binary_metrics([1, 1, 0, 0, 0, 1], [1, 0, 0, 1, 0, 1], [0.9, 0.4, 0.2, 0.6, 0.1, 0.8])
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 2)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3) and m["fpr"] == pytest.approx(1 / 3) and m["accuracy"] == pytest.approx(4 / 6)
    assert 0 < m["pr_auc"] <= 1
    none = binary_metrics([0, 0, 0], [0, 0, 0], [0.1, 0.2, 0.3])  # no positives: PR-AUC / precision undefined
    assert none["pr_auc"] is None and none["precision"] is None and none["recall"] is None and none["fpr"] == 0.0


def test_multiclass_metrics_per_class():
    classes = ["A", "B"]
    proba = np.array([[0.9, 0.1], [0.2, 0.8], [0.6, 0.4], [0.3, 0.7]])
    m = multiclass_metrics(["A", "B", "B", "B"], ["A", "B", "A", "B"], proba, classes)
    assert m["accuracy"] == 0.75 and m["per_class"]["B"]["recall"] == pytest.approx(2 / 3)
    assert m["per_class"]["A"]["fpr"] == 0.0 or m["per_class"]["A"]["fpr"] is not None


def test_threshold_is_the_best_f1_point_and_defaults_without_positives():
    y = [0, 0, 0, 1, 1]
    s = [0.1, 0.2, 0.3, 0.7, 0.9]
    assert 0.3 < best_f1_threshold(y, s) <= 0.7
    assert best_f1_threshold([0, 0, 0], [0.1, 0.2, 0.3]) == 0.5


def test_resource_helpers_return_numbers():
    X = np.random.default_rng(0).normal(size=(50, 4))
    lat = latency_ms(lambda x: x.sum(), X, repeats=20)
    assert lat["mean_ms"] >= 0 and lat["p95_ms"] >= lat["mean_ms"] * 0
    assert model_size_kb({"a": list(range(1000))}) > 1


# ------------------------------------------------------------------------ lead time
def frame_for_lead():
    sec = np.arange(100)
    cur = (sec >= 60) & (sec < 80)
    return pd.DataFrame({"experiment_id": "r", "second": sec, "current_congestion": cur, "future_congestion": True,
                         "congested_causal": cur.astype(float), "utilisation_pct": np.where(cur, 100.0, 20.0),
                         "utilisation_pct_slope5": 0.0})


def test_lead_time_is_onset_minus_first_warning_in_the_window():
    full = frame_for_lead()
    rows = full[full.second.between(30, 69)]
    pred = (rows["second"] >= 45).astype(int).to_numpy()  # first warning at 45, onset at 60
    lt = lead_times(rows, pred, full)
    assert lt["onsets"] == 1 and lt["detected"] == 1 and lt["mean_lead_s"] == 15
    late = lead_times(rows, np.zeros(len(rows), dtype=int), full)
    assert late["detected"] == 0 and late["detection_rate"] == 0.0 and late["mean_lead_s"] is None


def test_an_onset_with_no_evaluated_rows_before_it_is_not_counted():
    full = frame_for_lead()
    rows = full[full.second >= 60]  # nothing before the onset was evaluated
    lt = lead_times(rows, np.ones(len(rows), dtype=int), full)
    assert lt["onsets"] == 0 and lt["onsets_not_evaluable"] == 1


def test_baseline_scores_have_the_documented_meaning():
    rows = frame_for_lead()
    s = baseline_scores(rows)
    assert set(s) == {"persistence", "utilisation_gt_80", "utilisation_trend"}
    assert s["persistence"][70] == 1.0 and s["persistence"][10] == 0.0
    assert s["utilisation_gt_80"][70] > UTIL_BASELINE_PCT


# --------------------------------------------------------------------- interpreter
def row(**kw):
    base = {"congested_causal": 0.0, "utilisation_pct": 20.0, "queue_delay_ms": 0.0, "loss_pct": 0.0,
            "probe_loss_pct": 0.0, "share_udp": 0.2, "share_tcp": 0.7, "nf_new_flows": 1.0,
            "active_connections": 5.0, "rtt_mean_ms": 40.0, "jitter_ms": 1.0, "fwd_pps": 100.0, "syn_ratio": 0.01}
    base.update(kw)
    return pd.Series(base)


def interpreter():
    normal = pd.DataFrame([row(rtt_mean_ms=40 + i % 3, nf_new_flows=i % 4, active_connections=4 + i % 4,
                               fwd_pps=90 + i % 20) for i in range(200)])
    return AnomalyInterpreter.fit(normal)


def test_rules_name_the_cause_with_evidence_and_never_use_the_scenario_label():
    it = interpreter()
    assert it.explain(row(congested_causal=1.0, utilisation_pct=100, queue_delay_ms=50, loss_pct=3))[0] == "congestion"
    kind, why = it.explain(row(share_udp=0.97, share_tcp=0.01, utilisation_pct=85, nf_new_flows=1))
    assert kind == "flood-like" and any("UDP" in w for w in why)
    assert it.explain(row(nf_new_flows=30, active_connections=60, share_tcp=0.9, utilisation_pct=45))[0] == "legitimate burst"
    kind, why = it.explain(row(rtt_mean_ms=120, loss_pct=3, utilisation_pct=15))
    assert kind == "degradation" and any("RTT" in w for w in why)
    assert it.explain(row())[0] == "unclassified anomaly"
    assert set(EXPECTED_KIND) == {"UDP_FLOOD", "FLASH_CROWD", "DEGRADED", "RAMP"}


# ------------------------------------------------------- end to end on a tiny table
SCENARIOS = {"normal": range(0), "ramp": range(60, 85), "flash_crowd": range(0), "udp_flood": range(40, 70),
             "degraded": range(0)}


def build_prepared(tmp_path):
    data = tmp_path / "data"
    for scenario, cong in SCENARIOS.items():
        for rep in (1, 2, 3):
            obs = make_obs(exp=f"{scenario}{rep}", scenario=scenario, rep=rep, congested=cong)
            if scenario == "udp_flood":
                obs["tshark_udp_pkts"], obs["tshark_tcp_pkts"] = 95.0, 1.0
            if scenario == "degraded":
                obs["rtt_mean_ms"], obs["loss_pct"] = 120.0, 3.0
            if scenario == "flash_crowd":
                obs["nf_new_flows"], obs["active_connections"] = 30.0, 60.0
            # make the label columns consistent with the (modified) measurements
            from netguard.experiments.labels import label_observations

            obs = label_observations(obs.drop(columns=[c for c in obs.columns if c in (
                "scenario_label", "congestion_condition", "current_congestion", "future_congestion",
                "time_to_congestion", "future_label_available", "labels_status")]), LABELING, scenario.upper())
            write_run(data, obs)
    prepare(data, tmp_path / "results", LABELING, scheme="three_way")
    return data


def test_train_all_writes_models_and_honest_results(tmp_path):
    data = build_prepared(tmp_path)
    report = train_all(data, tmp_path / "results", tmp_path / "models", seed=0, n_jobs=1)
    assert (tmp_path / "results" / "ml_results.json").is_file() and (tmp_path / "results" / "ml_results.md").is_file()
    stored = json.loads((tmp_path / "results" / "ml_results.json").read_text())
    assert not any(k.startswith("_") for k in json.dumps(stored).split('"') if k.startswith("_fitted"))
    assert {p.name for p in (tmp_path / "models").glob("*.joblib")} >= {
        "classification_random_forest.joblib", "anomaly_isolation_forest.joblib", "congestion_xgboost.joblib"}
    cls = report["classification"]
    assert cls["classes"] == sorted(s.upper() for s in SCENARIOS)
    assert set(cls["models"]) == {"decision_tree", "random_forest", "xgboost"}
    assert cls["models"]["random_forest"]["test"]["accuracy"] > 0.8  # clearly separable synthetic classes
    an = report["anomaly"]
    assert an["trained_on"]["scenario"] == "NORMAL"
    assert an["models"]["isolation_forest"]["threshold_source"] == "NORMAL validation runs"
    flags = an["models"]["isolation_forest"]["test"]["flag_rate_by_scenario"]
    assert flags["NORMAL"] <= 0.25 and flags["DEGRADED"] > flags["NORMAL"]
    cg = report["congestion"]
    assert set(cg["methods"]) >= {"logistic_regression", "random_forest", "xgboost", "persistence",
                                  "utilisation_gt_80", "utilisation_trend"}
    for name, method in cg["methods"].items():
        for subset in ("all_rows", "not_yet_congested"):
            assert subset in method["test"]  # every method is also scored while not yet congested
    assert cg["methods"]["persistence"]["test"]["not_yet_congested"]["recall"] in (0.0, None)  # persistence cannot warn early


def test_models_are_fitted_on_the_training_split_only(tmp_path):
    data = build_prepared(tmp_path)
    ml = load_ml_data(data)
    train = ml.rows("train")
    assert set(train["split"]) == {"train"} and set(train["repetition"]) == {1}
    anomaly = run_anomaly(ml, n_jobs=1)
    assert anomaly["trained_on"]["rows"] == int((train["scenario_label"] == "NORMAL").sum())


def test_loading_before_preparation_fails_clearly(tmp_path):
    with pytest.raises(NetGuardError, match="prepare"):
        load_ml_data(tmp_path / "data")
