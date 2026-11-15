"""Parquet schema / integrity, metadata files and the Mininet lifecycle helpers."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_environment
from netguard.exceptions import NetGuardError, TopologyError
from netguard.experiments import dataset as ds
from netguard.experiments.lifecycle import MininetLifecycle


def table(n=5, exp="e1") -> pd.DataFrame:
    return pd.DataFrame({
        "experiment_id": exp, "scenario": "normal", "repetition": 1, "seed": 42, "second": range(n),
        "timestamp_s": [1000.0 + k for k in range(n)],
        "fwd_throughput_mbps": [1.0, np.nan, 3.0, 4.0, 5.0][:n], "flag": pd.array([True, None, False, True, True][:n], dtype="boolean"),
    })


def test_parquet_roundtrip_preserves_values_nulls_and_dtypes(tmp_path):
    frame = table()
    path = ds.write_parquet(frame, tmp_path / "x" / "t.parquet", {"scenario": "normal", "seed": 42})
    back = ds.read_parquet(path)
    pd.testing.assert_frame_equal(back, frame)
    assert back["fwd_throughput_mbps"].isna().tolist() == [False, True, False, False, False]  # nulls stay nulls
    assert str(back["flag"].dtype) == "boolean" and back["flag"].isna().tolist()[1]
    assert ds.read_file_metadata(path) == {"scenario": "normal", "seed": 42}
    assert not list(tmp_path.rglob("*.tmp"))  # atomic write leaves no temp file


def test_schema_validation_accepts_a_good_table_and_reports_problems():
    assert ds.validate_observation_schema(table(), 5) == []
    assert "expected 6 rows" in ds.validate_observation_schema(table(), 6)[0]
    gap = table().drop(index=2).reset_index(drop=True)
    assert any("gap-free" in p for p in ds.validate_observation_schema(gap))
    assert any("missing column" in p for p in ds.validate_observation_schema(table().drop(columns=["seed"])))
    skew = table()
    skew.loc[3, "timestamp_s"] = 2000.0
    assert any("1 s apart" in p for p in ds.validate_observation_schema(skew))
    mixed = pd.concat([table(exp="a"), table(exp="b")], ignore_index=True)
    assert any("more than one" in p for p in ds.validate_observation_schema(mixed))


def test_summary_columns_take_the_worse_direction_and_keep_missing_values_missing():
    frame = pd.DataFrame({
        "fwd_throughput_mbps": [5.0, 2.0, np.nan], "rev_throughput_mbps": [1.0, 4.0, 4.0],
        "fwd_utilisation_pct": [50.0, 20.0, np.nan], "rev_utilisation_pct": [10.0, 40.0, 40.0],
        "fwd_queue_delay_ms": [0.0, 7.0, 1.0], "rev_queue_delay_ms": [3.0, 1.0, 1.0],
        "fwd_tc_loss_pct": [0.0, 0.0, 2.0], "rev_tc_loss_pct": [0.5, 0.0, 0.0],
    })
    out = ds.add_summary_columns(frame)
    assert out["utilisation_pct"].tolist()[:2] == [50.0, 40.0] and np.isnan(out["utilisation_pct"][2])
    assert out["queue_delay_ms"].tolist() == [3.0, 7.0, 1.0]
    assert out["loss_pct"].tolist() == [0.5, 0.0, 2.0]


def test_missing_fraction_counts_absent_columns_as_fully_missing():
    frame = pd.DataFrame({"a": [1.0, np.nan, 3.0, np.nan]})
    assert ds.missing_fraction(frame, ["a", "b"]) == {"a": 0.5, "b": 1.0}


def test_metadata_is_written_atomically_and_listed(tmp_path):
    meta = ds.ExperimentMetadata("exp-1", "camp", "ramp", "RAMP", 2, 43, 300)
    meta.errors.append("boom")
    path = ds.write_metadata(tmp_path, meta)
    loaded = ds.load_metadata(path)
    assert loaded["seed"] == 43 and loaded["status"] == "running" and loaded["errors"] == ["boom"]
    (tmp_path / "metadata" / "calibration.json").write_text("{}")
    assert [m["experiment_id"] for m in ds.list_metadata(tmp_path)] == ["exp-1"]  # calibration file is not an experiment


def test_build_dataset_refuses_when_nothing_succeeded(tmp_path):
    ds.write_metadata(tmp_path, ds.ExperimentMetadata("e", "c", "normal", "NORMAL", 1, 1, 5, status="failed"))
    with pytest.raises(NetGuardError, match="no successful"):
        ds.build_dataset(tmp_path)


def test_build_dataset_detects_a_missing_processed_file(tmp_path):
    ds.write_metadata(tmp_path, ds.ExperimentMetadata("e", "c", "normal", "NORMAL", 1, 1, 5, status="success"))
    with pytest.raises(NetGuardError, match="missing"):
        ds.build_dataset(tmp_path)


# ------------------------------------------------------------------------ lifecycle
def test_lifecycle_cleans_before_and_after_and_reports(tmp_path):
    env = load_environment(DEFAULT_CONFIG_DIR)
    calls = []

    @__import__("contextlib").contextmanager
    def net_ctx(topology, links):
        calls.append("start")
        yield object()
        calls.append("stop")

    lc = MininetLifecycle(env, cleanup=lambda: calls.append("clean"), leftovers=lambda: [], network_context=net_ctx)
    with lc.network():
        calls.append("work")
    assert calls == ["clean", "start", "work", "stop", "clean"]
    assert lc.last_report.clean


def test_lifecycle_cleans_up_after_an_error_inside_the_run():
    env = load_environment(DEFAULT_CONFIG_DIR)
    calls = []
    lc = MininetLifecycle(env, cleanup=lambda: calls.append("clean"), leftovers=lambda: [])
    lc._network_context = lambda t, l: (_ for _ in ()).throw(RuntimeError("cannot start"))
    with pytest.raises(RuntimeError):
        with lc.network():
            pass  # pragma: no cover
    assert calls == ["clean", "clean"]


def test_lifecycle_refuses_to_start_on_a_dirty_host_and_reports_failed_cleanup():
    env = load_environment(DEFAULT_CONFIG_DIR)
    lc = MininetLifecycle(env, cleanup=lambda: None, leftovers=lambda: ["ovs bridge s1"])
    with pytest.raises(TopologyError, match="clean Mininet state"):
        with lc.network():
            pass  # pragma: no cover

    def explode():
        raise OSError("no permission")

    lc = MininetLifecycle(env, cleanup=explode, leftovers=lambda: [])
    report = lc.cleanup()
    assert not report.ran and not report.clean
