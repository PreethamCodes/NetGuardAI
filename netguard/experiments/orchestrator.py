"""Experiment orchestrator: one YAML campaign -> experiments -> Parquet datasets.

One experiment =

    clean Mininet -> build + shape dumbbell -> (pingAll) -> start TShark capture
    -> start collectors on the shared timeline -> run the Phase 1 traffic plan
    -> stop collectors -> tear the network down and verify the cleanup
    -> derive per-second metrics, NFStream flows, labels -> write Parquet + metadata

The orchestrator never repairs a bad run: missing collector output, failed traffic
events or an empty capture mark the experiment ``failed``; a saturated VM CPU marks it
``invalid``. Only ``success`` runs enter the consolidated dataset.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd

from netguard.config.environment import Environment
from netguard.config.loader import DEFAULT_CONFIG_DIR, load_environment
from netguard.exceptions import NetGuardError, TopologyError
from netguard.experiments import dataset as ds
from netguard.experiments.calibration import effective_labeling, load_calibration
from netguard.experiments.collectors.base import Collector, Health
from netguard.experiments.collectors.capture import (
    TsharkCollector, classify_packets, derive_packet_metrics, protocol_hierarchy, read_packets,
)
from netguard.experiments.collectors.connections import ConnectionCollector, derive_connection_metrics
from netguard.experiments.collectors.flows import derive_flow_metrics, extract_flows
from netguard.experiments.collectors.interface import InterfaceCollector, derive_interface_metrics
from netguard.experiments.collectors.probe import ProbeCollector, derive_probe_metrics, ping_command
from netguard.experiments.collectors.queue import QueueCollector, derive_queue_metrics
from netguard.experiments.collectors.resources import (
    ResourceCollector, derive_resource_metrics, saturated_seconds,
)
from netguard.experiments.config import ExperimentConfig, LabelingConfig
from netguard.experiments.labels import label_observations, unlabelled
from netguard.experiments.lifecycle import MininetLifecycle
from netguard.experiments.timeline import Timeline
from netguard.scenarios.loader import load_scenario
from netguard.topology.dumbbell import bottleneck_interfaces
from netguard.traffic.executor import TrafficExecutor
from netguard.traffic.planner import build_plan, effective_environment
from netguard.utils.system import is_linux, is_root, missing_binaries, module_available, preflight

logger = logging.getLogger(__name__)

STATUS_FAILED = ds.STATUS_FAILED


@dataclass(frozen=True)
class RunSpec:
    scenario: str
    repetition: int  # 1-based
    seed: int


def plan_runs(cfg: ExperimentConfig, scenarios: Optional[Sequence[str]] = None,
              repetitions: Optional[Sequence[int]] = None) -> List[RunSpec]:
    """Scenario-major order (so NORMAL, needed for calibration, comes first when listed first)."""
    runs = []
    for scenario in cfg.scenarios:
        if scenarios and scenario not in scenarios:
            continue
        for rep in range(1, cfg.repetitions + 1):
            if repetitions and rep not in repetitions:
                continue
            runs.append(RunSpec(scenario, rep, cfg.seeds[rep - 1]))
    return runs


def extra_preflight(cfg: ExperimentConfig) -> List[str]:
    """Phase 2 requirements on top of the Phase 1 pre-flight."""
    problems = [f"required program not found: {b}"
                for b in missing_binaries(["ip", "tc", "ss", "nsenter", "ping"] + (["tshark"] if cfg.capture.enabled else []))]
    for module in ("psutil", "pandas", "pyarrow") + (("nfstream",) if cfg.capture.nfstream else ()):
        if not module_available(module):
            problems.append(f"Python package {module!r} is missing (pip install -r requirements.txt)")
    return problems


@dataclass
class _Runtime:
    """Factories that tests replace with fakes."""

    lifecycle: Callable[[Environment], MininetLifecycle] = MininetLifecycle
    executor: Callable[..., TrafficExecutor] = TrafficExecutor
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    skip_preflight: bool = False
    #: ``(timeline, net, env, devices) -> {name: collector}``; tests inject synthetic collectors
    build_collectors: Optional[Callable[..., Dict[str, Collector]]] = None
    #: ``(timeline, collectors, net, env, capture_dev) -> feeder``; starts a live feed next to the collectors
    live_factory: Optional[Callable[..., Any]] = None


class ExperimentOrchestrator:
    def __init__(self, cfg: ExperimentConfig, env: Environment, labeling: LabelingConfig,
                 config_dir: Path = DEFAULT_CONFIG_DIR, runtime: Optional[_Runtime] = None) -> None:
        self.cfg = cfg
        self.env = env
        self.labeling = labeling
        self.config_dir = Path(config_dir)
        self.rt = runtime or _Runtime()
        self.data_dir = cfg.output.data_dir

    # ----------------------------------------------------------------- campaign level
    def completed(self) -> set:
        return {(m["scenario"], m["repetition"]) for m in ds.list_metadata(self.data_dir)
                if m["campaign"] == self.cfg.name and m["status"] == ds.STATUS_SUCCESS}

    def next_attempt(self, spec: RunSpec) -> int:
        return 1 + sum(1 for m in ds.list_metadata(self.data_dir)
                       if m["campaign"] == self.cfg.name and m["scenario"] == spec.scenario
                       and m["repetition"] == spec.repetition)

    def run_campaign(self, runs: Sequence[RunSpec], *, resume: bool = True,
                     auto_calibrate: bool = True) -> List[ds.ExperimentMetadata]:
        done = self.completed() if resume else set()
        results: List[ds.ExperimentMetadata] = []
        for i, spec in enumerate(runs, 1):
            if (spec.scenario, spec.repetition) in done:
                logger.info("[%d/%d] %s r%d already successful: skipped (resume)", i, len(runs),
                            spec.scenario, spec.repetition)
                continue
            logger.info("[%d/%d] running %s repetition %d (seed %d)", i, len(runs), spec.scenario,
                        spec.repetition, spec.seed)
            results.append(self.run_experiment(spec))
            if auto_calibrate:
                self._maybe_calibrate()
        return results

    def _maybe_calibrate(self) -> None:
        from netguard.experiments.postprocess import calibrate_and_label

        if load_calibration(self.data_dir) is not None or self.labeling.queue_delay_threshold_ms is not None:
            return
        source = self.labeling.calibration.source_scenario
        needed = [r for r in range(1, self.cfg.repetitions + 1)]
        ok = {m["repetition"] for m in ds.list_metadata(self.data_dir)
              if m["campaign"] == self.cfg.name and m["scenario"] == source and m["status"] == ds.STATUS_SUCCESS}
        if source in self.cfg.scenarios and ok.issuperset(needed):
            logger.info("all %s experiments are done: calibrating the queue-delay threshold X", source)
            try:
                calibrate_and_label(self.data_dir, self.labeling, campaign=self.cfg.name)
            except NetGuardError as exc:  # e.g. too little NORMAL data: the campaign must go on, labels stay pending
                logger.warning("automatic calibration of X not possible: %s", exc)
                return
            self.labeling = effective_labeling(self.labeling, self.data_dir)

    # ------------------------------------------------------------------ one experiment
    def run_experiment(self, spec: RunSpec) -> ds.ExperimentMetadata:
        scenario = (load_scenario(spec.scenario, self.config_dir).with_seed(spec.seed)
                    .with_duration(self.cfg.duration_s))
        plan = build_plan(scenario, self.env)
        eff = effective_environment(scenario, self.env)
        n_seconds = int(round(plan.duration_s))
        exp_id = ds.new_experiment_id(spec.scenario, spec.repetition)
        meta = ds.ExperimentMetadata(
            experiment_id=exp_id, campaign=self.cfg.name, scenario=spec.scenario,
            scenario_label=spec.scenario.upper(), repetition=spec.repetition, seed=spec.seed,
            duration_s=n_seconds, attempt=self.next_attempt(spec), started_at=ds.utc_now(),
            config={
                "topology": eff.topology.__dict__, "links": {"bottleneck": eff.links.bottleneck.__dict__,
                                                             "access": eff.links.access.__dict__},
                "link_overrides": dict(scenario.link_overrides),
                "experiment": {"timeline": self.cfg.timeline.__dict__, "probe": self.cfg.probe.__dict__,
                               "capture": self.cfg.capture.__dict__, "validation": self.cfg.validation.__dict__},
                "labeling": self.labeling.to_dict(),
            },
            plan={"events": len(plan.events), "tools": sorted(t.value for t in plan.tools_used()),
                  "scenario_description": scenario.description},
        )
        ds.write_metadata(self.data_dir, meta)
        raw_dir = self.data_dir / "raw" / exp_id
        state: Dict[str, Any] = {}
        try:
            self._execute(meta, scenario, plan, eff, n_seconds, raw_dir, state)
            self._analyse(meta, plan, eff, n_seconds, raw_dir, state)
        except KeyboardInterrupt:
            meta.errors.append("interrupted by the user")
            meta.mark(STATUS_FAILED)
            raise
        except Exception as exc:  # noqa: BLE001 - any failure is recorded, never swallowed silently
            logger.error("experiment %s failed: %s", exp_id, exc)
            logger.debug("%s", traceback.format_exc())
            meta.errors.append(f"{type(exc).__name__}: {exc}")
            meta.errors.append(traceback.format_exc(limit=8))
            meta.mark(STATUS_FAILED)
        finally:
            meta.ended_at = ds.utc_now()
            self._persist_partial(meta, eff, n_seconds, raw_dir, state)
            ds.write_metadata(self.data_dir, meta)
            logger.info("experiment %s finished with status %s", exp_id, meta.status)
        return meta

    # -- phase A: live run (network up) ----------------------------------------------
    def _execute(self, meta: ds.ExperimentMetadata, scenario: Any, plan: Any, eff: Environment, n_seconds: int,
                 raw_dir: Path, state: Dict[str, Any]) -> None:
        cfg, rt = self.cfg, self.rt
        if not rt.skip_preflight:
            problems = preflight(plan) + extra_preflight(cfg)
            if problems:
                raise NetGuardError("pre-flight checks failed:\n  - " + "\n  - ".join(problems))
        lifecycle = rt.lifecycle(eff)
        state["lifecycle"] = lifecycle
        collectors: Dict[str, Collector] = {}
        state["collectors"] = collectors
        try:
            with lifecycle.network() as net:
                if cfg.check_ping:
                    from netguard.scenarios.runner import check_connectivity

                    check_connectivity(net, tolerate_loss=eff.links.bottleneck.loss_pct > 0)
                fwd_dev, rev_dev = bottleneck_interfaces(eff.topology)
                devices = {"fwd": fwd_dev, "rev": rev_dev}
                capture_dev = fwd_dev if cfg.capture.interface == "left" else rev_dev
                # dumpcap is confined by Ubuntu's AppArmor profile and runs without CAP_DAC_OVERRIDE: it cannot
                # enter directories owned by another user (e.g. the project under /home/<user>), so the capture
                # goes to a private root-owned temp directory and is copied out afterwards if it is to be kept.
                capture_dir = Path(tempfile.mkdtemp(prefix="netguard-capture-"))
                state["capture_dir"] = capture_dir
                pcap = capture_dir / "capture.pcap"
                state["pcap"] = pcap

                tshark: Optional[TsharkCollector] = None
                if cfg.capture.enabled:  # slowest to start, so first
                    span = cfg.timeline.pre_roll_s + n_seconds + cfg.timeline.tail_s
                    tshark = TsharkCollector(capture_dev, pcap, cfg.capture.snaplen, duration_s=span + 5)
                    collectors["tshark"] = tshark
                    tshark.start()

                wall, mono = rt.clock(), time.monotonic()
                anchor = wall + cfg.timeline.pre_roll_s
                timeline = Timeline(anchor, n_seconds, cfg.timeline.pre_roll_s, cfg.timeline.tail_s)
                state["timeline"] = timeline
                meta.anchor_wall_s = anchor

                builder = rt.build_collectors or self._default_collectors
                collectors.update(builder(timeline, net, eff, devices))
                for name, collector in collectors.items():
                    if name != "tshark":
                        collector.start()

                if rt.live_factory is not None:
                    state["feeder"] = rt.live_factory(timeline, collectors, net, eff, capture_dev)

                executor = rt.executor(net, plan, eff)
                state["executor"] = executor
                t0_mono = mono + cfg.timeline.pre_roll_s
                try:
                    results = executor.run(t0_monotonic=t0_mono)
                finally:
                    executor.stop()
                state["event_results"] = results
                # keep sampling until the timeline (including the tail) is over
                rt.sleep(max(0.0, timeline.end_wall - rt.clock() + 0.5))
                if state.get("feeder") is not None:
                    state["feeder"].stop()
                for name in ("probe", "connections", "queue", "interface", "resources", "live_packets"):
                    if name in collectors:
                        collectors[name].stop()
                if tshark is not None:
                    tshark.stop()
        finally:
            if state.get("feeder") is not None:
                state["feeder"].stop()
            for collector in collectors.values():  # also on errors: never leave a process behind
                collector.stop()
            report = state["lifecycle"].last_report
            meta.checks["cleanup"] = report.to_dict() if report else {"ran": False}

    def _default_collectors(self, timeline: Timeline, net: Any, eff: Environment,
                            devices: Dict[str, str]) -> Dict[str, Collector]:
        cfg = self.cfg
        probe_node = net.get(cfg.probe.src)
        probe = ProbeCollector(
            timeline,
            lambda argv: probe_node.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, universal_newlines=True),
            ping_command(eff.topology.host_ip(cfg.probe.dst), cfg.probe.interval_s, cfg.probe.payload_bytes),
            cfg.probe.interval_s,
        )
        host_pids = {h: net.get(h).pid for h in eff.topology.all_hosts}
        built: List[Collector] = [
            InterfaceCollector(timeline, devices),
            QueueCollector(timeline, devices),
            ConnectionCollector(timeline, host_pids),
            ResourceCollector(timeline),
            probe,
        ]
        return {c.name: c for c in built}

    # -- phase B: analysis (network already gone) -----------------------------------------
    def _analyse(self, meta: ds.ExperimentMetadata, plan: Any, eff: Environment, n_seconds: int, raw_dir: Path,
                 state: Dict[str, Any]) -> None:
        cfg = self.cfg
        timeline: Timeline = state["timeline"]
        collectors: Dict[str, Collector] = state["collectors"]
        capacity = eff.links.bottleneck.bandwidth_mbps

        # traffic events
        results = state["event_results"]
        counts: Dict[str, int] = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        meta.event_results = counts
        bad = [f"{r.event_id}: {r.status} {r.error or ''}".strip() for r in results if r.status != "ok"]
        if bad:
            meta.errors.append(f"{len(bad)} traffic event(s) did not complete: {bad[:10]}")

        # collectors
        health: Dict[str, Health] = {n: c.health() for n, c in collectors.items()}
        meta.collectors = {n: h.to_dict() for n, h in health.items()}
        for n, h in health.items():
            if not h.ok:
                meta.errors.append(f"collector {n} unhealthy: {h.errors or 'no samples'}")

        interface_raw = collectors["interface"].raw_samples()
        queue_raw = collectors["queue"].raw_samples()
        conn_raw = collectors["connections"].raw_samples()
        res_raw = collectors["resources"].raw_samples()
        probe: ProbeCollector = collectors["probe"]  # type: ignore[assignment]
        probe_replies = probe.replies  # raw replies, kept in full

        frames = [
            derive_interface_metrics(interface_raw, n_seconds, capacity),
            derive_queue_metrics(queue_raw, n_seconds, capacity),
            derive_probe_metrics(probe_replies, n_seconds, timeline.anchor_wall, cfg.probe.interval_s,
                                 probe.summary.get("sent")),
            derive_connection_metrics(conn_raw, n_seconds),
            derive_resource_metrics(res_raw, n_seconds),
        ]

        pcap: Path = state["pcap"]
        if cfg.capture.enabled:
            packets = read_packets(pcap)
            classified = classify_packets(packets, eff.traffic.http.port)
            frames.append(derive_packet_metrics(classified, n_seconds, timeline.anchor_wall))
            tshark: TsharkCollector = collectors["tshark"]  # type: ignore[assignment]
            meta.capture = {
                "interface": tshark.interface, "snaplen": cfg.capture.snaplen,
                "packets_in_pcap": int(len(packets)), "packets_captured_reported": tshark.packets_captured,
                "packets_dropped_by_kernel": tshark.packets_dropped, "pcap_kept": cfg.capture.keep_pcap,
                "pcap_size_bytes": pcap.stat().st_size, "tshark_stderr": tshark.stderr_text()[-400:],
            }
            (raw_dir).mkdir(parents=True, exist_ok=True)
            (raw_dir / "protocol_hierarchy.txt").write_text(protocol_hierarchy(pcap) + "\n", encoding="utf-8")
            if cfg.capture.nfstream:
                flows = extract_flows(pcap)
                ds.write_parquet(flows, raw_dir / "flows.parquet")
                state["flows"] = flows
                meta.capture["flows"] = int(len(flows))
                frames.append(derive_flow_metrics(flows, n_seconds, timeline.anchor_wall))

        obs = ds.assemble_observations(meta, frames, n_seconds)
        state["observations"] = obs
        feeder = state.get("feeder")
        if feeder is not None:
            from netguard.experiments.livefeed import compare_live_with_offline

            meta.checks["live"] = feeder.summary()
            meta.checks["live_vs_offline"] = compare_live_with_offline(feeder.samples, obs)
            if feeder.posted == 0:
                meta.errors.append(f"live feed delivered no samples: {feeder.last_error or 'unknown reason'}")

        # --- checks -> status
        checks = meta.checks
        checks["rows"] = len(obs)
        checks["schema_problems"] = ds.validate_observation_schema(obs, n_seconds)
        missing = ds.missing_fraction(obs, ds.REQUIRED_METRICS)
        checks["missing_fraction"] = missing
        too_many = {c: f for c, f in missing.items() if f > cfg.validation.max_missing_fraction}
        if too_many:
            meta.errors.append(f"required metrics missing in too many seconds: {too_many}")
        if checks["schema_problems"]:
            meta.errors.append(f"schema problems: {checks['schema_problems']}")
        saturated = saturated_seconds(res_raw, cfg.validation.cpu_saturation_pct, 1, n_seconds)
        checks["cpu_saturated_seconds"] = saturated
        checks["cpu_max_percent"] = max((r["cpu_percent"] for r in res_raw), default=None)
        if saturated:
            meta.invalid_reasons.append(
                f"VM CPU reached {cfg.validation.cpu_saturation_pct:g}% in {saturated} second(s)")
        cleanup = checks.get("cleanup", {})
        if not cleanup.get("clean", False):
            meta.errors.append(f"Mininet cleanup was not clean: {cleanup}")

        if meta.errors:
            meta.mark(STATUS_FAILED)
        elif meta.invalid_reasons:
            meta.mark(ds.STATUS_INVALID)
        else:
            meta.mark(ds.STATUS_SUCCESS)

    # -- always: write whatever exists ---------------------------------------------------
    def _persist_partial(self, meta: ds.ExperimentMetadata, eff: Environment, n_seconds: int, raw_dir: Path,
                         state: Dict[str, Any]) -> None:
        collectors: Dict[str, Collector] = state.get("collectors", {})
        files: Dict[str, str] = {}
        try:
            for name, collector in collectors.items():
                path = ds.write_raw(raw_dir, name, collector.raw_samples())
                if path:
                    files[f"raw_{name}"] = str(path)
            flows_path = raw_dir / "flows.parquet"
            if flows_path.exists():
                files["raw_flows"] = str(flows_path)
            pcap: Optional[Path] = state.get("pcap")
            if pcap is not None and pcap.exists() and self.cfg.capture.keep_pcap:
                kept = self.data_dir / "captures" / f"{meta.experiment_id}.pcap"
                kept.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pcap, kept)
                files["pcap"] = str(kept)
            capture_dir: Optional[Path] = state.get("capture_dir")
            if capture_dir is not None:
                shutil.rmtree(capture_dir, ignore_errors=True)  # header-only captures are large: keep only on request
            obs: Optional[pd.DataFrame] = state.get("observations")
            if obs is not None:
                obs = obs.copy()
                obs["experiment_status"] = meta.status
                labeling = effective_labeling(self.labeling, self.data_dir)
                if labeling.queue_delay_threshold_ms is not None:
                    obs = label_observations(obs, labeling, meta.scenario_label)
                    meta.labels = {"status": "labelled", **labeling.to_dict()}
                else:
                    obs = unlabelled(obs, meta.scenario_label)
                    meta.labels = {"status": "pending_calibration"}
                path = ds.write_parquet(obs, self.data_dir / "processed" / f"{meta.experiment_id}.parquet",
                                        {"experiment": meta.experiment_id, "status": meta.status,
                                         "scenario": meta.scenario, "seed": meta.seed})
                files["processed"] = str(path)
        except Exception as exc:  # noqa: BLE001 - writing problems must still end in a recorded failure
            logger.error("could not persist outputs of %s: %s", meta.experiment_id, exc)
            meta.errors.append(f"persisting outputs failed: {type(exc).__name__}: {exc}")
            meta.mark(STATUS_FAILED)
        meta.files = files


def build_orchestrator(cfg: ExperimentConfig, config_dir: Path = DEFAULT_CONFIG_DIR) -> ExperimentOrchestrator:
    from netguard.experiments.config import load_labeling

    env = load_environment(config_dir)
    labeling = effective_labeling(load_labeling(config_dir), cfg.output.data_dir)
    return ExperimentOrchestrator(cfg, env, labeling, config_dir)
