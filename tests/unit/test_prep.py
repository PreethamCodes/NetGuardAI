"""Phase 3 code tests. The tables here are synthetic inputs that only check the code logic;
they are never presented as experimental data."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from netguard.exceptions import ConfigError, NetGuardError
from netguard.experiments import dataset as ds
from netguard.experiments.config import LabelingConfig
from netguard.experiments.labels import label_observations
from netguard.prep.features import (
    TARGET_COLUMNS, build_features, causal_congestion, feature_columns, syn_per_second, trailing_features,
)
from netguard.prep.pipeline import prepare
from netguard.prep.splits import SCHEMES, assign_splits, check_no_leakage
from netguard.prep.validate import check_run, verify_labels

X = 5.0
LABELING = LabelingConfig(queue_delay_threshold_ms=X)


def make_obs(n=100, exp="e1", scenario="ramp", rep=1, seed=1, congested=range(60, 80), anchor=1000.0, status="success",
             labelled=True) -> pd.DataFrame:
    sec = np.arange(n)
    util = np.where(np.isin(sec, list(congested)), 100.0, 20.0)
    frame = pd.DataFrame({
        "experiment_id": exp, "scenario": scenario, "repetition": rep, "seed": seed, "second": sec,
        "timestamp_s": anchor + sec, "experiment_status": status,
        "throughput_mbps": util / 10, "fwd_pps": util * 3.0, "utilisation_pct": util,
        "rtt_mean_ms": 40.0 + sec % 3, "jitter_ms": 1.0, "probe_loss_pct": 0.0, "loss_pct": 0.0,
        "fwd_queue_backlog_bytes": np.where(util > 90, 60000.0, 0.0), "rev_queue_backlog_bytes": 0.0,
        "queue_delay_ms": np.where(util > 90, 48.0, 0.0),
        "fwd_queue_drops_per_s": 0.0, "rev_queue_drops_per_s": 0.0,
        "active_connections": 5.0, "tcp_retransmissions": 0.0, "nf_active_flows": 8.0, "nf_new_flows": 1.0,
        "tshark_pkts": 100.0, "tshark_tcp_pkts": 60.0, "tshark_udp_pkts": 30.0, "tshark_icmp_pkts": 10.0,
        "tshark_dns_pkts": 5.0, "tshark_http_pkts": 20.0, "cpu_percent": 10.0, "ram_percent": 30.0,
    })
    if labelled:
        frame = label_observations(frame, LABELING, scenario.upper())
    return frame


def flows_for(anchor=1000.0):
    return pd.DataFrame({
        "protocol": [6, 6, 17], "bidirectional_first_seen_ms": [(anchor + 3.2) * 1000, (anchor + 3.9) * 1000,
                                                                (anchor + 3.5) * 1000],
        "bidirectional_syn_packets": [2, 3, 0],
    })


def feats(obs=None, **kw) -> pd.DataFrame:
    obs = make_obs() if obs is None else obs
    return build_features(obs, flows=flows_for(), anchor_wall=1000.0, util_pct=90, queue_ms=X, loss_pct=1.0,
                          min_consecutive=3, **kw)


# ------------------------------------------------------------------- leakage rules
def test_features_at_second_t_do_not_depend_on_later_seconds():
    base = make_obs()
    altered = base.copy()
    altered.loc[70:, ["throughput_mbps", "utilisation_pct", "queue_delay_ms", "rtt_mean_ms", "loss_pct"]] = 999.0
    altered.loc[70:, "fwd_queue_backlog_bytes"] = 1e9
    a, b = feats(base), feats(altered)
    cols = feature_columns(a)
    pd.testing.assert_frame_equal(a.loc[:69, cols], b.loc[:69, cols])  # seconds <= 69 are untouched by changes from 70
    assert not a.loc[70:, cols].equals(b.loc[70:, cols])  # ... and later seconds do change


def test_labels_and_ids_are_never_model_inputs():
    cols = feature_columns(feats())
    assert not set(cols) & set(TARGET_COLUMNS)
    for banned in ("experiment_id", "second", "timestamp_s", "time_to_congestion", "future_congestion",
                   "current_congestion", "scenario", "cpu_percent", "ram_percent"):
        assert banned not in cols
    assert "congested_causal" in cols  # the causal stand-in is allowed


def test_incomplete_windows_and_lags_are_nan_and_never_filled():
    f = feats()
    assert f["throughput_mbps_mean5"].iloc[:4].isna().all() and f["throughput_mbps_mean5"].iloc[4:].notna().all()
    assert f["utilisation_pct_lag5"].iloc[:5].isna().all() and f["utilisation_pct_lag5"].iloc[5:].notna().all()
    assert f["utilisation_pct_lag30"].iloc[:30].isna().all() and f["utilisation_pct_lag30"].iloc[30:].notna().all()


def test_window_statistics_and_lags_have_the_documented_meaning():
    obs = make_obs()
    obs["throughput_mbps"] = np.arange(100, dtype=float)  # 0, 1, 2, ...
    f = feats(obs)
    assert f.loc[10, "throughput_mbps"] == 10
    assert f.loc[10, "throughput_mbps_mean5"] == pytest.approx(8.0)  # seconds 6..10
    assert f.loc[10, "throughput_mbps_max5"] == 10
    assert f.loc[10, "throughput_mbps_std5"] == pytest.approx(math.sqrt(2.0))
    assert f.loc[10, "throughput_mbps_slope5"] == pytest.approx(1.0)  # 1 Mbit/s per second
    assert f.loc[40, "throughput_mbps_lag5"] == 35 and f.loc[40, "throughput_mbps_lag10"] == 30
    assert f.loc[40, "throughput_mbps_lag30"] == 10


def test_a_gap_in_a_window_gives_nan_not_an_interpolated_value():
    obs = make_obs()
    obs.loc[20, "throughput_mbps"] = np.nan
    f = feats(obs)
    assert f.loc[20:24, "throughput_mbps_mean5"].isna().all() and f.loc[25, "throughput_mbps_mean5"] == pytest.approx(
        obs.loc[21:25, "throughput_mbps"].mean())
    assert f.loc[20:24, "throughput_mbps_slope5"].isna().all()


def test_runs_are_independent_nothing_is_computed_across_experiments():
    with pytest.raises(ValueError, match="exactly one experiment"):
        feats(pd.concat([make_obs(exp="a"), make_obs(exp="b")], ignore_index=True))
    a, b = feats(make_obs(exp="a")), feats(make_obs(exp="b", anchor=5000.0))
    assert b["throughput_mbps_lag5"].iloc[:5].isna().all()  # a new run does not inherit the previous run's tail


# ---------------------------------------------------------------- derived signals
def test_protocol_shares_and_queue_summary():
    f = feats()
    assert f.loc[0, "share_tcp"] == pytest.approx(0.6) and f.loc[0, "share_dns"] == pytest.approx(0.05)
    assert f.loc[65, "queue_backlog_bytes"] == 60000.0


def test_idle_seconds_have_zero_shares_not_nan():
    obs = make_obs()
    obs.loc[3, ["tshark_pkts", "tshark_tcp_pkts", "tshark_udp_pkts", "tshark_icmp_pkts", "tshark_dns_pkts",
                "tshark_http_pkts"]] = 0.0
    assert feats(obs).loc[3, "share_tcp"] == 0.0


def test_syn_ratio_uses_flows_started_in_that_second_only():
    syn = syn_per_second(flows_for(), 10, 1000.0)
    assert syn.tolist() == [0, 0, 0, 5, 0, 0, 0, 0, 0, 0]  # the UDP flow and other seconds contribute nothing
    f = feats()
    assert f.loc[3, "syn_ratio"] == pytest.approx(5 / 60) and f.loc[4, "syn_ratio"] == 0.0
    no_flows = build_features(make_obs(), flows=None, anchor_wall=1000.0, util_pct=90, queue_ms=X, loss_pct=1.0,
                              min_consecutive=3)
    assert no_flows["syn_ratio"].isna().all()  # unknown is NaN, not 0


def test_causal_congestion_needs_three_past_seconds_and_ignores_the_future():
    obs = make_obs(congested=range(60, 80))
    state = causal_congestion(obs, 90, X, 1.0, 3)
    assert state.iloc[2:62].eq(0).all() and state.iloc[62] == 1.0  # first true at 62 (third congested second)
    assert state.iloc[59] == 0.0 and state.iloc[61] == 0.0
    assert state.iloc[:2].isna().all()  # window not full yet
    assert state.iloc[80] == 0.0  # ends right after the episode


# ----------------------------------------------------------------------- splits
def test_split_is_by_run_and_covers_every_scenario():
    runs = pd.DataFrame([(f"{s}{r}", s, r) for s in ("normal", "ramp") for r in (1, 2, 3)],
                        columns=["experiment_id", "scenario", "repetition"])
    out = assign_splits(runs, "three_way")
    assert out.groupby("split")["experiment_id"].nunique().to_dict() == {"test": 2, "train": 2, "val": 2}
    assert set(out[out.split == "train"]["scenario"]) == {"normal", "ramp"}
    out2 = assign_splits(runs, "train2_test1")
    assert sorted(out2.split.unique()) == ["test", "train"] and (out2[out2.repetition < 3].split == "train").all()
    with pytest.raises(ConfigError, match="not covered"):
        assign_splits(runs.assign(repetition=9), "three_way")
    with pytest.raises(ConfigError, match="unknown split scheme"):
        assign_splits(runs, "random")


def test_leakage_check_detects_a_run_in_two_splits():
    frame = pd.DataFrame({"experiment_id": ["a", "a", "b"], "split": ["train", "test", "test"]})
    assert "a" in check_no_leakage(frame)[0]
    assert check_no_leakage(pd.DataFrame({"experiment_id": ["a", "b"], "split": ["train", "test"]})) == []


# -------------------------------------------------------------------- validation
def meta_for(obs, status="success", **kw):
    m = {"experiment_id": obs["experiment_id"].iloc[0], "scenario": obs["scenario"].iloc[0],
         "repetition": int(obs["repetition"].iloc[0]), "status": status, "attempt": 1, "anchor_wall_s": 1000.0}
    m.update(kw)
    return m


def test_good_run_is_included():
    obs = make_obs()
    check = check_run(meta_for(obs), obs, LABELING, expected_rows=100)
    assert check.included and not check.problems


@pytest.mark.parametrize("mutate,expected", [
    (lambda o: pd.concat([o, o.iloc[[5]]], ignore_index=True), "duplicate"),
    (lambda o: o.drop(index=10).reset_index(drop=True), "gap-free"),
    (lambda o: o.assign(timestamp_s=o["timestamp_s"] * 1.01), "1 s apart"),
    (lambda o: o.assign(labels_status="pending_calibration"), "calibration pending"),
    (lambda o: o.drop(columns=["rtt_mean_ms"]), "missing columns"),
])
def test_bad_runs_are_excluded_with_a_reason(mutate, expected):
    obs = make_obs()
    check = check_run(meta_for(obs), mutate(obs), LABELING)
    assert not check.included and any(expected in p for p in check.problems)


def test_failed_or_invalid_runs_and_missing_tables_are_excluded():
    obs = make_obs()
    assert not check_run(meta_for(obs, status="invalid"), obs, LABELING).included
    assert not check_run(meta_for(obs, status="failed"), obs, LABELING).included
    assert "missing" in check_run(meta_for(obs), None, LABELING).problems[0]
    assert any("expected 300" in p for p in check_run(meta_for(obs), obs, LABELING, expected_rows=300).problems)


def test_tampered_labels_are_detected():
    obs = make_obs()
    assert verify_labels(obs, LABELING) == []
    bad = obs.copy()
    bad.loc[10, "current_congestion"] = True
    assert any("current_congestion" in p for p in verify_labels(bad, LABELING))
    bad2 = obs.copy()
    bad2.loc[95, "future_congestion"] = False  # a label inside the final 30 s must not exist
    assert verify_labels(bad2, LABELING)


# ------------------------------------------------------------------- full pipeline
def write_run(data_dir, obs, status="success"):
    meta = ds.ExperimentMetadata(obs["experiment_id"].iloc[0], "camp", obs["scenario"].iloc[0],
                                 obs["scenario"].iloc[0].upper(), int(obs["repetition"].iloc[0]),
                                 int(obs["seed"].iloc[0]), len(obs), status=status, anchor_wall_s=1000.0)
    ds.write_metadata(data_dir, meta)
    ds.write_parquet(obs, data_dir / "processed" / f"{meta.experiment_id}.parquet")
    ds.write_parquet(flows_for(), data_dir / "raw" / meta.experiment_id / "flows.parquet")


def test_pipeline_end_to_end_excludes_bad_runs_and_fits_the_scaler_on_train_only(tmp_path):
    data = tmp_path / "data"
    for scenario in ("normal", "ramp"):
        for rep in (1, 2, 3):
            cong = range(60, 80) if scenario == "ramp" else range(0)
            write_run(data, make_obs(exp=f"{scenario}{rep}", scenario=scenario, rep=rep, congested=cong))
    write_run(data, make_obs(exp="bad", scenario="ramp", rep=3), status="invalid")
    result = prepare(data, tmp_path / "results", LABELING, scheme="three_way")
    frame = ds.read_parquet(result.features_path)
    assert set(frame["experiment_id"]) == {f"{s}{r}" for s in ("normal", "ramp") for r in (1, 2, 3)}  # 'bad' is out
    assert result.report["runs_excluded"] == 1 and result.report["runs_included"] == 6
    assert frame.groupby("experiment_id")["split"].nunique().eq(1).all()
    assert set(frame[frame.split == "train"]["repetition"]) == {1} and set(frame[frame.split == "test"]["repetition"]) == {3}
    # prediction rows exclude the first 30 s (lags) and last 30 s (no future label)
    usable = frame[frame.valid_for_prediction]
    assert usable.groupby("experiment_id")["second"].min().eq(30).all()
    assert usable.groupby("experiment_id")["second"].max().eq(69).all()
    assert usable["future_congestion"].notna().all()
    # scaler: fitted on training rows only
    import joblib

    bundle = joblib.load(result.scaler_path)
    train = frame[(frame.split == "train") & frame.features_complete]
    cols = bundle["columns"]
    assert bundle["fitted_on"] == "train"
    assert np.allclose(bundle["scaler"].mean_, train[cols].mean().to_numpy())
    assert (tmp_path / "results" / "prep_report.md").is_file()
    assert json.loads(result.columns_path.read_text())["targets"] == list(TARGET_COLUMNS)


def test_pipeline_refuses_without_a_calibrated_threshold_or_valid_runs(tmp_path):
    with pytest.raises(NetGuardError, match="not calibrated"):
        prepare(tmp_path / "data", tmp_path / "r", LabelingConfig())
    data = tmp_path / "d2"
    write_run(data, make_obs(), status="failed")
    with pytest.raises(NetGuardError, match="no valid runs"):
        prepare(data, tmp_path / "r", LABELING)
