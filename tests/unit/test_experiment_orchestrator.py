"""Orchestrator behaviour with fake Mininet / traffic / collectors: status handling, cleanup, resume, metadata."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List

import pandas as pd
import pytest

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_environment
from netguard.exceptions import ConfigError
from netguard.experiments import dataset as ds
from netguard.experiments.collectors.base import Collector, Health
from netguard.experiments.collectors.probe import ProbeCollector
from netguard.experiments.config import ExperimentConfig, LabelingConfig
from netguard.experiments.lifecycle import CleanupReport, MininetLifecycle
from netguard.experiments.orchestrator import ExperimentOrchestrator, RunSpec, _Runtime, plan_runs
from netguard.traffic.executor import EventResult

DURATION = 40


def make_cfg(tmp_path: Path, **overrides: Any) -> ExperimentConfig:
    data = {
        "name": "t", "scenarios": ["normal"], "repetitions": 2, "seeds": [42, 43], "duration_s": DURATION,
        "check_ping": False,
        "capture": {"enabled": False, "nfstream": False},
        "output": {"data_dir": str(tmp_path / "data"), "results_dir": str(tmp_path / "results")},
    }
    data.update(overrides)
    return ExperimentConfig.from_mapping(data)


# ---------------------------------------------------------------------- fakes
class FakeNode:
    pid = 1234


class FakeNet:
    def get(self, name: str) -> FakeNode:
        return FakeNode()


class FakeLifecycle(MininetLifecycle):
    instances: List["FakeLifecycle"] = []

    def __init__(self, env, fail_start: bool = False, leftovers=()):
        self.env = env
        self.cleanup_calls = 0
        self.fail_start = fail_start
        self._left = list(leftovers)
        self.last_report = None
        FakeLifecycle.instances.append(self)

    def cleanup(self) -> CleanupReport:
        self.cleanup_calls += 1
        self.last_report = CleanupReport(True, list(self._left))
        return self.last_report

    @contextmanager
    def network(self):
        self.cleanup()
        try:
            if self.fail_start:
                raise RuntimeError("mininet exploded")
            yield FakeNet()
        finally:
            self.cleanup()


class FakeExecutor:
    last: "FakeExecutor"

    def __init__(self, net, plan, env, fail_events: int = 0):
        self.plan, self.fail_events = plan, fail_events
        self.t0 = None
        self.stopped = False
        FakeExecutor.last = self

    def run(self, t0_monotonic=None):
        self.t0 = t0_monotonic
        out = []
        for i, e in enumerate(self.plan.events):
            ok = i >= self.fail_events
            out.append(EventResult(e.event_id, "ok" if ok else "failed", error=None if ok else "client died"))
        return out

    def stop(self):
        self.stopped = True


class SyntheticCollector(Collector):
    """Returns canned raw samples instead of measuring anything."""

    def __init__(self, name, rows, healthy=True):
        self.name, self._rows, self._healthy = name, rows, healthy
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def raw_samples(self):
        return list(self._rows)

    def health(self):
        return Health(self.name, self.started, 10, 0 if self._healthy else 10, () if self._healthy else ("dead",))


class SyntheticProbe(ProbeCollector):
    def __init__(self, timeline, replies):
        super().__init__(timeline, lambda argv: None, ["ping"], 0.2)
        self._replies = replies
        self._summary = {"sent": len(replies), "received": len(replies)}

    def start(self):
        self._started = True

    def stop(self):
        pass


def synthetic_builder(*, util=lambda k: 20.0, queue_backlog=lambda k: 0, loss=lambda k: 0, cpu=lambda k: 10.0,
                      unhealthy=(), drop_collector=None, interface_drops=False) -> Callable[..., Dict[str, Collector]]:
    def build(timeline, net, env, devices):
        cap_mbps = env.links.bottleneck.bandwidth_mbps
        iface, queue, conn, res = [], [], [], []
        total = {"fwd": 0.0, "rev": 0.0}
        pk = {"fwd": 0, "rev": 0}
        drops = 0
        sent = 0
        for tick in timeline.ticks():
            ts = timeline.tick_time(tick)
            k = max(tick - 1, 0)
            for d in ("fwd", "rev"):
                total[d] += util(k) / 100 * cap_mbps * 1e6 / 8
                pk[d] += 100
                iface.append({"direction": d, "dev": devices[d], "tick": tick, "ts": ts, "tx_bytes": int(total[d]),
                              "tx_packets": pk[d], "tx_dropped": 0, "rx_bytes": 0, "rx_packets": 0, "rx_dropped": 0})
                drops += int(loss(k))
                queue.append({"direction": d, "dev": devices[d], "tick": tick, "ts": ts,
                              "tbf_backlog_bytes": queue_backlog(k), "tbf_qlen": queue_backlog(k) // 1500,
                              "tbf_drops": drops, "tbf_overlimits": 0, "tbf_packets": pk[d], "tbf_bytes": 0,
                              "tbf_rate_bytes_per_s": cap_mbps * 1e6 / 8, "netem_drops": drops,
                              "netem_packets": pk[d], "netem_backlog_bytes": 0})
            conn.append({"tick": tick, "ts": ts, "active_connections": 4, "tcp_sockets": 8, "retrans_new": 0})
            res.append({"tick": tick, "ts": ts, "cpu_percent": cpu(k), "ram_percent": 30.0, "ram_used_mb": 1000.0})
        replies = []
        for i in range(int(timeline.duration_s / 0.2) + 10):
            send = timeline.anchor_wall + i * 0.2
            replies.append({"seq": i + 1, "send_ts": send, "recv_ts": send + 0.04, "rtt_ms": 40.0 + (i % 3)})
        probe = SyntheticProbe(timeline, replies)
        built = {"interface": SyntheticCollector("interface", iface, "interface" not in unhealthy),
                 "queue": SyntheticCollector("queue", queue, "queue" not in unhealthy),
                 "connections": SyntheticCollector("connections", conn, "connections" not in unhealthy),
                 "resources": SyntheticCollector("resources", res, "resources" not in unhealthy),
                 "probe": probe}
        if drop_collector:
            built[drop_collector]._rows = []
        return built

    return build


def make_orch(tmp_path: Path, builder=None, *, cfg=None, labeling=None, executor_factory=None,
              lifecycle_factory=None) -> ExperimentOrchestrator:
    cfg = cfg or make_cfg(tmp_path)
    env = load_environment(DEFAULT_CONFIG_DIR)
    rt = _Runtime(
        lifecycle=lifecycle_factory or FakeLifecycle, executor=executor_factory or FakeExecutor,
        clock=lambda: 1_700_000_000.0, sleep=lambda s: None, skip_preflight=True,
        build_collectors=builder or synthetic_builder(),
    )
    return ExperimentOrchestrator(cfg, env, labeling or LabelingConfig(queue_delay_threshold_ms=5.0),
                                  DEFAULT_CONFIG_DIR, rt)


# ------------------------------------------------------------------- config parsing
def test_experiment_yaml_is_parsed_and_validated(tmp_path):
    cfg = make_cfg(tmp_path)
    assert cfg.scenarios == ("normal",) and cfg.seeds == (42, 43) and cfg.timeline.pre_roll_s == 5
    assert cfg.capture.snaplen == 96 and cfg.probe.interval_s == 0.2
    assert cfg.output.data_dir == tmp_path / "data"


@pytest.mark.parametrize("patch,message", [
    ({"seeds": [1]}, "expected 2 seeds"),
    ({"seeds": [1, 1]}, "distinct"),
    ({"scenarios": None}, "scenarios"),
    ({"bogus": 1}, "unknown key"),
    ({"capture": {"enabled": False, "nfstream": True}}, "nfstream needs capture.enabled"),
    ({"capture": {"snaplen": 10}}, "snaplen"),
    ({"probe": {"interval_s": 0.01}}, "interval_s"),
    ({"repetitions": 0, "seeds": []}, "repetitions"),
    ({"validation": {"cpu_saturation_pct": 150}}, "cpu_saturation_pct"),
])
def test_invalid_experiment_yaml_is_rejected(tmp_path, patch, message):
    data = {"name": "t", "scenarios": ["normal"], "repetitions": 2, "seeds": [42, 43]}
    data.update(patch)
    if data.get("scenarios", 1) is None:
        del data["scenarios"]
    with pytest.raises(ConfigError, match=message):
        ExperimentConfig.from_mapping(data)


def test_default_campaign_file_matches_the_specification():
    from netguard.experiments.config import PRIMARY_SCENARIOS, load_experiment_config

    cfg = load_experiment_config(DEFAULT_CONFIG_DIR / "experiments" / "phase2.yaml")
    assert set(cfg.scenarios) == set(PRIMARY_SCENARIOS) and cfg.repetitions == 3 and len(set(cfg.seeds)) == 3
    assert cfg.capture.snaplen == 96 and cfg.capture.enabled and cfg.probe.interval_s == 0.2
    assert cfg.validation.cpu_saturation_pct == 100.0
    runs = plan_runs(cfg)
    assert len(runs) == 15 and runs[0].scenario == "normal"  # NORMAL first: it calibrates X


def test_experiment_ids_are_unique_and_carry_the_scenario():
    ids = {ds.new_experiment_id("ramp", 1) for _ in range(200)}
    assert len(ids) == 200 and all("-ramp-r1-" in i for i in ids)


# ------------------------------------------------------------------ successful run
def test_successful_experiment_writes_parquet_metadata_and_labels(tmp_path):
    orch = make_orch(tmp_path)
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_SUCCESS, meta.errors
    data = orch.data_dir
    frame = ds.read_parquet(data / "processed" / f"{meta.experiment_id}.parquet")
    assert len(frame) == DURATION and frame["second"].tolist() == list(range(DURATION))
    assert (frame["experiment_id"] == meta.experiment_id).all() and (frame["seed"] == 42).all()
    assert (frame["scenario_label"] == "NORMAL").all() and (frame["experiment_status"] == "success").all()
    for column in ("current_congestion", "future_congestion", "time_to_congestion", "utilisation_pct",
                   "queue_delay_ms", "loss_pct", "cpu_percent", "rtt_mean_ms", "jitter_ms", "active_connections"):
        assert column in frame.columns
    assert frame["utilisation_pct"].round(3).eq(20.0).all()
    assert not frame["current_congestion"].any()
    assert ds.read_file_metadata(data / "processed" / f"{meta.experiment_id}.parquet")["scenario"] == "normal"
    # raw collector outputs
    raw = data / "raw" / meta.experiment_id
    assert {p.name for p in raw.glob("*.parquet")} >= {"interface.parquet", "queue.parquet", "resources.parquet"}
    # metadata json
    stored = json.loads(ds.metadata_path(data, meta.experiment_id).read_text())
    assert stored["status"] == "success" and stored["seed"] == 42 and stored["repetition"] == 1
    assert stored["config"]["links"]["bottleneck"]["bandwidth_mbps"] == 10.0
    assert stored["started_at"] and stored["ended_at"] and stored["anchor_wall_s"] == 1_700_000_005.0
    assert stored["checks"]["rows"] == DURATION and stored["checks"]["cleanup"]["clean"]


def test_timestamps_align_with_the_traffic_start(tmp_path):
    orch = make_orch(tmp_path)
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    frame = ds.read_parquet(orch.data_dir / "processed" / f"{meta.experiment_id}.parquet")
    assert frame["timestamp_s"].tolist() == [1_700_000_005.0 + k for k in range(DURATION)]
    assert meta.anchor_wall_s == 1_700_000_005.0
    # the executor was told to start scenario time 0 pre_roll seconds after the collectors' reference
    import time

    assert FakeExecutor.last.t0 is not None and FakeExecutor.last.t0 - time.monotonic() < 5.5
    assert FakeExecutor.last.stopped


def test_observations_without_calibration_have_pending_labels(tmp_path):
    orch = make_orch(tmp_path, labeling=LabelingConfig())
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    frame = ds.read_parquet(orch.data_dir / "processed" / f"{meta.experiment_id}.parquet")
    assert meta.status == "success" and (frame["labels_status"] == "pending_calibration").all()
    assert frame["current_congestion"].isna().all()


def test_congestion_in_the_collected_data_is_labelled(tmp_path):
    builder = synthetic_builder(util=lambda k: 100.0 if 10 <= k < 20 else 20.0,
                                queue_backlog=lambda k: 60_000 if 10 <= k < 20 else 0)
    orch = make_orch(tmp_path, builder)
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    frame = ds.read_parquet(orch.data_dir / "processed" / f"{meta.experiment_id}.parquet")
    assert frame["current_congestion"][10:20].all() and not frame["current_congestion"][:10].any()
    assert frame["queue_delay_ms"][12] == pytest.approx(48.0)
    assert bool(frame["future_congestion"][5]) and frame["time_to_congestion"][5] == 5
    assert frame["future_label_available"].sum() == DURATION - 30  # last 30 s unavailable
    assert len(frame) == DURATION  # but still stored


# ----------------------------------------------------------------- failure handling
def test_unhealthy_collector_marks_the_experiment_failed(tmp_path):
    orch = make_orch(tmp_path, synthetic_builder(unhealthy=("queue",)))
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_FAILED
    assert any("collector queue unhealthy" in e for e in meta.errors)


def test_missing_measurements_are_nan_not_filled_and_fail_the_run(tmp_path):
    orch = make_orch(tmp_path, synthetic_builder(drop_collector="resources"))
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    frame = ds.read_parquet(orch.data_dir / "processed" / f"{meta.experiment_id}.parquet")
    assert frame["cpu_percent"].isna().all()  # nothing fabricated
    assert meta.status == ds.STATUS_FAILED and any("missing" in e for e in meta.errors)
    assert (frame["experiment_status"] == "failed").all()


def test_failed_traffic_events_fail_the_run(tmp_path):
    orch = make_orch(tmp_path, executor_factory=lambda net, plan, env: FakeExecutor(net, plan, env, fail_events=1))
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_FAILED and meta.event_results["failed"] == 1
    assert any("traffic event" in e for e in meta.errors)


def test_exception_during_the_run_is_recorded_and_cleanup_still_runs(tmp_path):
    FakeLifecycle.instances.clear()
    orch = make_orch(tmp_path, lifecycle_factory=lambda env: FakeLifecycle(env, fail_start=True))
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_FAILED and any("mininet exploded" in e for e in meta.errors)
    assert FakeLifecycle.instances[-1].cleanup_calls >= 2  # before and after the failed start
    stored = json.loads(ds.metadata_path(orch.data_dir, meta.experiment_id).read_text())
    assert stored["status"] == "failed" and stored["ended_at"]
    assert not (orch.data_dir / "processed" / f"{meta.experiment_id}.parquet").exists()  # no data, no table


def test_executor_crash_fails_the_run_and_stops_traffic(tmp_path):
    class Crashing(FakeExecutor):
        def run(self, t0_monotonic=None):
            raise RuntimeError("iperf3 vanished")

    orch = make_orch(tmp_path, executor_factory=Crashing)
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_FAILED and FakeExecutor.last.stopped


def test_leftover_mininet_state_after_the_run_fails_the_experiment(tmp_path):
    orch = make_orch(tmp_path, lifecycle_factory=lambda env: FakeLifecycle(env, leftovers=["interface s1-eth1"]))
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_FAILED
    assert meta.checks["cleanup"]["clean"] is False


def test_saturated_cpu_invalidates_the_experiment(tmp_path):
    orch = make_orch(tmp_path, synthetic_builder(cpu=lambda k: 100.0 if k == 12 else 30.0))
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_INVALID and not meta.errors
    assert meta.checks["cpu_saturated_seconds"] >= 1 and "CPU" in meta.invalid_reasons[0]
    frame = ds.read_parquet(orch.data_dir / "processed" / f"{meta.experiment_id}.parquet")
    assert (frame["experiment_status"] == "invalid").all()  # kept, but clearly marked


def test_cpu_saturation_outside_the_measurement_window_is_ignored(tmp_path):
    # tick -5 (pre-roll) is not part of the measurement window
    builder = synthetic_builder(cpu=lambda k: 30.0)
    base = builder

    def with_preroll_spike(timeline, net, env, devices):
        built = base(timeline, net, env, devices)
        for row in built["resources"]._rows:
            if row["tick"] < 0:
                row["cpu_percent"] = 100.0
        return built

    meta = make_orch(tmp_path, with_preroll_spike).run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_SUCCESS


def test_failed_run_does_not_enter_the_consolidated_dataset(tmp_path):
    good = make_orch(tmp_path).run_experiment(RunSpec("normal", 1, 42))
    bad = make_orch(tmp_path, synthetic_builder(unhealthy=("queue",))).run_experiment(RunSpec("normal", 2, 43))
    invalid = make_orch(tmp_path, synthetic_builder(cpu=lambda k: 100.0)).run_experiment(RunSpec("normal", 1, 42))
    assert (good.status, bad.status, invalid.status) == ("success", "failed", "invalid")
    path = ds.build_dataset(tmp_path / "data")
    dataset = ds.read_parquet(path)
    assert set(dataset["experiment_id"]) == {good.experiment_id}


# ------------------------------------------------------------------------ resume
def test_resume_skips_successful_runs_and_retries_failed_ones(tmp_path):
    orch = make_orch(tmp_path)
    runs = plan_runs(orch.cfg)
    first = orch.run_campaign(runs[:1])
    assert [m.status for m in first] == ["success"]
    # r2 fails the first time
    failing = make_orch(tmp_path, synthetic_builder(unhealthy=("queue",)))
    assert [m.status for m in failing.run_campaign(runs)] == ["failed"]  # r1 skipped, r2 failed
    again = make_orch(tmp_path).run_campaign(runs)
    assert len(again) == 1 and again[0].repetition == 2 and again[0].status == "success"
    assert again[0].attempt == 2  # the failed attempt stays on record
    all_meta = ds.list_metadata(tmp_path / "data")
    assert sorted(m["status"] for m in all_meta) == ["failed", "success", "success"]
    assert make_orch(tmp_path).run_campaign(runs) == []  # nothing left to do


def test_seeds_are_recorded_per_repetition(tmp_path):
    runs = plan_runs(make_cfg(tmp_path))
    assert [(r.repetition, r.seed) for r in runs] == [(1, 42), (2, 43)]
    orch = make_orch(tmp_path)
    metas = orch.run_campaign(runs)
    assert [(m.repetition, m.seed) for m in metas] == [(1, 42), (2, 43)]


def test_campaign_calibrates_x_after_the_normal_runs_and_relabels_them(tmp_path, monkeypatch):
    # enough NORMAL data for the 100-sample minimum: 2 runs x 40 s is not enough, so use a 60 s duration
    cfg = make_cfg(tmp_path, duration_s=60)
    orch = make_orch(tmp_path, cfg=cfg, labeling=LabelingConfig())
    metas = orch.run_campaign(plan_runs(cfg))
    assert [m.status for m in metas] == ["success", "success"]
    calibration = json.loads((tmp_path / "data" / "metadata" / "calibration.json").read_text())
    assert calibration["queue_delay_threshold_ms"] == 1.0 and len(calibration["experiment_ids"]) == 2
    for m in metas:
        frame = ds.read_parquet(tmp_path / "data" / "processed" / f"{m.experiment_id}.parquet")
        assert (frame["labels_status"] == "labelled").all()  # NORMAL runs were relabelled with the frozen X


def test_a_failed_automatic_calibration_does_not_abort_the_campaign(tmp_path):
    # 2 x 40 s = 80 usable seconds < the 100 s minimum: calibration is refused, the runs are still recorded
    orch = make_orch(tmp_path, labeling=LabelingConfig())
    metas = orch.run_campaign(plan_runs(orch.cfg))
    assert [m.status for m in metas] == ["success", "success"]
    assert not (tmp_path / "data" / "metadata" / "calibration.json").exists()
    frame = ds.read_parquet(tmp_path / "data" / "processed" / f"{metas[0].experiment_id}.parquet")
    assert (frame["labels_status"] == "pending_calibration").all()  # honestly pending, not guessed
