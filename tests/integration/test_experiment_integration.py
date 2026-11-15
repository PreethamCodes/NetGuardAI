"""Phase 2 integration test: real Mininet experiments with all collectors. Run INSIDE the Ubuntu VM, as root:

    sudo mn -c
    sudo .venv/bin/python -m pytest -m integration tests/integration/test_experiment_integration.py -v -s

Each experiment is short (45 s), so labels are only sanity-checked; the full 5 minute
campaign is run with ``python -m netguard experiment run``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_environment
from netguard.experiments import dataset as ds
from netguard.experiments.config import ExperimentConfig, LabelingConfig
from netguard.experiments.lifecycle import find_leftovers
from netguard.experiments.orchestrator import ExperimentOrchestrator, RunSpec, _Runtime, extra_preflight
from netguard.topology.dumbbell import mininet_available
from netguard.traffic.executor import TrafficExecutor
from netguard.utils.system import is_linux, is_root, missing_binaries, module_available


def _skip_reason() -> Any:
    if not is_linux():
        return "needs Linux"
    if not is_root():
        return "needs root (run with sudo)"
    if not mininet_available():
        return "Mininet is not importable"
    missing = missing_binaries(["ovs-vsctl", "tc", "iperf3", "ping", "tshark", "ss", "nsenter"])
    if missing:
        return f"missing programs: {missing}"
    for module in ("psutil", "pandas", "pyarrow", "nfstream"):
        if not module_available(module):
            return f"missing python package {module}"
    probe = subprocess.run(["ovs-vsctl", "--timeout=5", "show"], capture_output=True, text=True)
    return None if probe.returncode == 0 else "Open vSwitch is not running"


_REASON = _skip_reason()
pytestmark = [pytest.mark.integration, pytest.mark.skipif(_REASON is not None, reason=_REASON or "")]

DURATION = 45


def make_orchestrator(tmp_path: Path, scenario: str = "normal", **runtime: Any) -> ExperimentOrchestrator:
    cfg = ExperimentConfig.from_mapping({
        "name": "it", "scenarios": [scenario], "repetitions": 1, "seeds": [42], "duration_s": DURATION,
        "output": {"data_dir": str(tmp_path / "data"), "results_dir": str(tmp_path / "results")},
    })
    env = load_environment(DEFAULT_CONFIG_DIR)
    return ExperimentOrchestrator(cfg, env, LabelingConfig(queue_delay_threshold_ms=5.0), DEFAULT_CONFIG_DIR,
                                  _Runtime(**runtime) if runtime else None)


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exp")
    orch = make_orchestrator(tmp)
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    return orch, meta


def test_extra_preflight_is_clean():
    cfg = ExperimentConfig.from_mapping({"name": "x", "scenarios": ["normal"], "repetitions": 1, "seeds": [1]})
    assert extra_preflight(cfg) == []


def test_experiment_succeeds_and_runs_for_its_duration(completed):
    orch, meta = completed
    assert meta.status == ds.STATUS_SUCCESS, (meta.errors, meta.invalid_reasons, meta.checks)
    assert meta.checks["rows"] == DURATION
    started = pd.Timestamp(meta.started_at)
    ended = pd.Timestamp(meta.ended_at)
    assert DURATION <= (ended - started).total_seconds() < DURATION + 60


def test_every_required_measurement_has_real_values(completed):
    orch, meta = completed
    frame = ds.read_parquet(orch.data_dir / "processed" / f"{meta.experiment_id}.parquet")
    assert len(frame) == DURATION
    for column in ds.REQUIRED_METRICS + ("rtt_mean_ms", "jitter_ms", "probe_loss_pct", "tshark_pkts", "nf_active_flows"):
        assert frame[column].notna().mean() >= 0.9, column
    # a NORMAL scenario carries traffic on the bottleneck, and the probe sees the 40 ms path
    assert frame["fwd_throughput_mbps"].max() > 0.3
    assert frame["fwd_pps"].max() > 20
    assert frame["rtt_mean_ms"].median() == pytest.approx(40.0, abs=8.0)
    assert frame["active_connections"].max() >= 1
    assert frame["tshark_pkts"].sum() > 100 and frame["tshark_tcp_pkts"].sum() > 0 and frame["tshark_dns_pkts"].sum() > 0
    assert frame["cpu_percent"].between(0, 100).all() and frame["ram_percent"].between(0, 100).all()
    assert frame["queue_delay_ms"].between(0, 100).all()
    assert frame["utilisation_pct"].max() < 60  # normal load


def test_timestamps_are_one_second_apart_and_experiment_metadata_is_complete(completed):
    orch, meta = completed
    frame = ds.read_parquet(orch.data_dir / "processed" / f"{meta.experiment_id}.parquet")
    assert ds.validate_observation_schema(frame, DURATION) == []
    stored = ds.load_metadata(ds.metadata_path(orch.data_dir, meta.experiment_id))
    for key in ("experiment_id", "scenario", "seed", "config", "started_at", "ended_at", "status", "checks"):
        assert stored[key]
    assert stored["collectors"].keys() >= {"interface", "queue", "connections", "resources", "probe", "tshark"}
    assert all(v["ok"] for v in stored["collectors"].values())


def test_flows_were_extracted_and_the_capture_is_header_only(completed):
    orch, meta = completed
    flows = ds.read_parquet(orch.data_dir / "raw" / meta.experiment_id / "flows.parquet")
    assert len(flows) > 5 and {"src_ip", "dst_ip", "bidirectional_packets"} <= set(flows.columns)
    assert flows["src_ip"].str.startswith("10.0.0.").all()  # nothing leaves the emulated network
    assert meta.capture["snaplen"] == 96 and meta.capture["packets_in_pcap"] > 100
    assert not (orch.data_dir / "raw" / meta.experiment_id / "capture.pcap").exists()  # not kept by default
    assert (orch.data_dir / "raw" / meta.experiment_id / "protocol_hierarchy.txt").read_text().startswith("====")


def test_mininet_is_clean_after_a_successful_run(completed):
    _, meta = completed
    assert meta.checks["cleanup"]["clean"] is True
    assert find_leftovers() == []


def test_failed_experiment_is_marked_failed_and_mininet_is_cleaned(tmp_path):
    class Boom(TrafficExecutor):
        def run(self, t0_monotonic=None):
            raise RuntimeError("injected traffic failure")

    orch = make_orchestrator(tmp_path, executor=Boom)
    meta = orch.run_experiment(RunSpec("normal", 1, 42))
    assert meta.status == ds.STATUS_FAILED and any("injected traffic failure" in e for e in meta.errors)
    assert find_leftovers() == []  # the network was torn down although the run failed
    assert not (orch.data_dir / "raw" / meta.experiment_id / "capture.pcap").exists()
    assert meta.checks["cleanup"]["clean"] is True
