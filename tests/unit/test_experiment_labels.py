"""Ground-truth congestion labels, calibration and leakage safeguards."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from netguard.exceptions import ConfigError, NetGuardError
from netguard.experiments.calibration import (
    compute_threshold, effective_labeling, load_calibration, write_calibration,
)
from netguard.experiments.config import CalibrationSettings, LabelingConfig
from netguard.experiments.labels import condition_series, label_observations, label_series, unlabelled

NAN = math.nan


def cond_of(pattern: str):
    """'TTFTN' -> [True, True, False, True, None]."""
    return [{"T": True, "F": False, "N": None}[c] for c in pattern]


def labels(pattern: str, horizon: int = 30, min_consecutive: int = 3) -> pd.DataFrame:
    return label_series(cond_of(pattern), horizon, min_consecutive)


# ----------------------------------------------------------------------- condition
def test_condition_needs_utilisation_and_queue_or_loss():
    cond = condition_series(
        utilisation_pct=[95, 95, 95, 50, 95], queue_delay_ms=[10, 1, 1, 99, 1], loss_pct=[0, 2, 0, 5, 1.0],
        util_threshold=90, queue_threshold_ms=5, loss_threshold=1.0,
    )
    # queue>=X | loss>1 | neither | util too low | loss exactly 1% is NOT > 1%
    assert cond == [True, True, False, False, False]


def test_condition_boundaries_are_inclusive_for_utilisation_and_queue():
    cond = condition_series([90.0], [5.0], [0.0], 90, 5.0, 1.0)
    assert cond == [True]


def test_condition_unknown_inputs_are_not_guessed():
    nan = float("nan")
    assert condition_series([nan], [10], [0], 90, 5, 1) == [None]
    assert condition_series([95], [nan], [0], 90, 5, 1) == [None]  # queue unknown, loss says no
    assert condition_series([95], [nan], [3], 90, 5, 1) == [True]  # loss alone decides
    assert condition_series([50], [nan], [nan], 90, 5, 1) == [False]  # low utilisation decides


# -------------------------------------------------------------------- persistence
def test_two_seconds_are_not_congestion_but_three_are():
    assert not labels("FFTTFFF", horizon=1)["current_congestion"].any()
    cur = labels("FFTTTFF", horizon=1)["current_congestion"].tolist()
    assert cur == [False, False, True, True, True, False, False]


def test_every_second_of_a_run_is_congested_not_only_the_third():
    cur = labels("TTTT", horizon=1)["current_congestion"].tolist()
    assert cur == [True, True, True, True]


# ---------------------------------------------------------------- future / horizon
def test_future_label_uses_next_onset_within_horizon():
    pattern = "F" * 10 + "TTT" + "F" * 40  # onset at second 10, 53 seconds total
    out = labels(pattern, horizon=5)
    fut = out["future_congestion"].tolist()
    ttc = out["time_to_congestion"].tolist()
    assert fut[4] is False  # onset in 6 s, outside the 5 s horizon
    assert fut[5] is True and fut[9] is True  # onsets at 5+5=10 .. 9+5
    assert ttc[5] == 5 and ttc[9] == 1
    assert fut[10] is False  # the current episode is not its own future
    assert ttc[10] != ttc[10]  # no later onset: censored, not invented


def test_current_episode_does_not_predict_itself_but_the_next_one_does():
    pattern = "TTT" + "F" * 5 + "TTT" + "F" * 20
    out = labels(pattern, horizon=10)
    assert out["current_congestion"][0] and out["current_congestion"][1]
    assert bool(out["future_congestion"][0]) is True  # second episode starts at 8, within 10 s
    assert out["time_to_congestion"][0] == 8
    assert out["time_to_congestion"][1] == 7


def test_horizon_is_exactly_the_configured_number_of_seconds():
    pattern = "F" * 31 + "TTT" + "F" * 40  # onset at index 31
    out = labels(pattern, horizon=30)
    assert bool(out["future_congestion"][0]) is False  # 31 s ahead: outside t + 30
    assert bool(out["future_congestion"][1]) is True  # exactly 30 s ahead: inside
    assert out["time_to_congestion"][0] == 31 and out["time_to_congestion"][1] == 30


def test_final_horizon_seconds_have_no_future_label_but_raw_rows_are_kept():
    out = labels("F" * 60 + "TTT" + "F" * 37, horizon=30)
    assert len(out) == 100  # nothing is dropped
    available = out["future_label_available"].tolist()
    assert all(available[:70]) and not any(available[70:])  # last 30 s: t + 30 > n - 1
    assert out["future_congestion"][70:].isna().all()
    assert out["future_congestion"][:70].notna().all()
    # an onset that lies ahead of a final-horizon row is still reported exactly
    out2 = labels("F" * 90 + "TTT" + "F" * 7, horizon=30)
    assert not out2["future_label_available"][89]
    assert out2["time_to_congestion"][89] == 1


def test_run_shorter_than_horizon_has_no_future_labels_at_all():
    out = labels("F" * 20, horizon=30)
    assert out["future_congestion"].isna().all()
    assert not out["future_label_available"].any()


def test_unknown_measurements_never_become_labels():
    # one unknown second inside a candidate run: it could be True, so the run may or may not exist
    out = labels("FFTNTFF", horizon=1)
    assert pd.isna(out["current_congestion"][2])
    assert bool(out["current_congestion"][0]) is False
    # unknowns that cannot form a run do not hide a definite False
    assert labels("FNFFF", horizon=1)["current_congestion"].tolist() == [False] * 5
    # a definite run is still a definite run
    assert labels("TTTNF", horizon=1)["current_congestion"][:3].tolist() == [True, True, True]


def test_unknown_second_inside_horizon_makes_future_label_null_unless_onset_is_certain():
    pattern = "FF" + "TN" + "T" + "F" * 40  # onset at 2 only if the unknown is True
    out = labels(pattern, horizon=10)
    assert pd.isna(out["future_congestion"][0])
    certain = labels("FF" + "TTT" + "F" * 40, horizon=10)
    assert bool(certain["future_congestion"][0]) is True


# --------------------------------------------------------------------- whole table
def make_obs(util, queue, loss) -> pd.DataFrame:
    n = len(util)
    return pd.DataFrame({
        "experiment_id": "e", "second": range(n), "utilisation_pct": util, "queue_delay_ms": queue, "loss_pct": loss,
    })


def test_label_observations_adds_documented_columns_and_keeps_all_rows():
    util = [10.0] * 5 + [100.0] * 5 + [10.0] * 60
    queue = [0.0] * 5 + [50.0] * 5 + [0.0] * 60
    obs = make_obs(util, queue, [0.0] * 70)
    out = label_observations(obs, LabelingConfig(queue_delay_threshold_ms=10.0), "RAMP")
    assert len(out) == len(obs)
    for column in ("scenario_label", "current_congestion", "future_congestion", "time_to_congestion"):
        assert column in out.columns
    assert (out["scenario_label"] == "RAMP").all()
    assert out["current_congestion"][5:10].all() and not out["current_congestion"][:5].any()
    assert bool(out["future_congestion"][0]) is True
    assert out["time_to_congestion"][0] == 5
    assert (out["labels_status"] == "labelled").all()


def test_labels_do_not_change_the_feature_columns():
    obs = make_obs([100.0] * 40, [20.0] * 40, [0.0] * 40)
    labelled = label_observations(obs, LabelingConfig(queue_delay_threshold_ms=5.0), "UDP_FLOOD")
    pd.testing.assert_frame_equal(labelled[list(obs.columns)], obs)


def test_label_may_use_the_future_but_the_observation_of_second_t_does_not():
    obs_a = make_obs([10.0] * 40 + [100.0] * 5, [20.0] * 45, [0.0] * 45)
    obs_b = make_obs([10.0] * 45, [20.0] * 45, [0.0] * 45)  # the future differs
    cfg = LabelingConfig(queue_delay_threshold_ms=5.0, horizon_s=5)
    a, b = label_observations(obs_a, cfg, "X"), label_observations(obs_b, cfg, "X")
    assert bool(a["future_congestion"][35]) is True and bool(b["future_congestion"][35]) is False
    assert obs_a.loc[:39].equals(obs_b.loc[:39])  # features up to t are identical


def test_labelling_requires_a_calibrated_threshold():
    with pytest.raises(ConfigError, match="not calibrated"):
        label_observations(make_obs([1.0], [1.0], [1.0]), LabelingConfig(), "NORMAL")


def test_unlabelled_table_is_explicit_about_pending_labels():
    out = unlabelled(make_obs([1.0] * 3, [0.0] * 3, [0.0] * 3), "NORMAL")
    assert (out["labels_status"] == "pending_calibration").all()
    assert out["current_congestion"].isna().all() and out["future_congestion"].isna().all()


# ------------------------------------------------------------------- calibration
def test_threshold_is_percentile_of_low_utilisation_seconds_rounded_up():
    queue = list(np.linspace(0.0, 3.0, 1000))  # p99.9 = 2.997
    result = compute_threshold(queue, [20.0] * 1000, CalibrationSettings())
    assert result["queue_delay_threshold_ms"] == 3.0
    assert result["raw_percentile_ms"] == pytest.approx(2.997, abs=1e-3)


def test_calibration_ignores_congested_and_unmeasured_seconds():
    queue = [1.0] * 500 + [60.0] * 100 + [NAN] * 10
    util = [30.0] * 500 + [99.0] * 100 + [30.0] * 10
    result = compute_threshold(queue, util, CalibrationSettings())
    assert result["samples_used"] == 500
    assert result["queue_delay_threshold_ms"] == 1.0


def test_calibration_has_a_floor_and_needs_enough_data():
    result = compute_threshold([0.0] * 300, [10.0] * 300, CalibrationSettings(min_threshold_ms=1.0))
    assert result["queue_delay_threshold_ms"] == 1.0 and result["floor_applied"]
    with pytest.raises(NetGuardError, match="more NORMAL data"):
        compute_threshold([1.0] * 10, [10.0] * 10, CalibrationSettings())


def test_calibration_file_is_fixed_once_written(tmp_path):
    result = compute_threshold([1.0] * 200, [10.0] * 200, CalibrationSettings())
    write_calibration(tmp_path, result, ["e1"], CalibrationSettings())
    assert load_calibration(tmp_path)["queue_delay_threshold_ms"] == 1.0
    with pytest.raises(NetGuardError, match="already exists"):
        write_calibration(tmp_path, result, ["e1"], CalibrationSettings())
    write_calibration(tmp_path, result, ["e1"], CalibrationSettings(), force=True)
    assert effective_labeling(LabelingConfig(), tmp_path).queue_delay_threshold_ms == 1.0
    # an explicit value in labeling.yaml wins over the stored one
    assert effective_labeling(LabelingConfig(queue_delay_threshold_ms=7.0), tmp_path).queue_delay_threshold_ms == 7.0
