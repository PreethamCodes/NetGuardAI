"""Typed, validated models for the Phase 2 experiment and labelling YAML files."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple, Union

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_yaml
from netguard.exceptions import ConfigError
from netguard.utils.validation import (
    as_list,
    get_bool,
    get_float,
    get_int,
    get_str,
    reject_unknown,
    require_mapping,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = DEFAULT_CONFIG_DIR.parent
PathLike = Union[str, Path]

#: Scenario files that make up the primary dataset; their upper-cased name is ``scenario_label``.
PRIMARY_SCENARIOS = ("normal", "ramp", "flash_crowd", "udp_flood", "degraded")


# ---------------------------------------------------------------------------- labelling
@dataclass(frozen=True)
class CalibrationSettings:
    """How the queue-delay threshold X is derived from NORMAL experiments."""

    source_scenario: str = "normal"
    percentile: float = 99.9
    max_utilisation_pct: float = 90.0  # only seconds below this utilisation are used
    min_threshold_ms: float = 1.0  # floor, so that X = 0 (an always-true condition) cannot occur


@dataclass(frozen=True)
class LabelingConfig:
    """Parameters of the ground-truth congestion definition (``configs/labeling.yaml``)."""

    horizon_s: int = 30
    utilisation_pct: float = 90.0
    loss_pct: float = 1.0
    min_consecutive_s: int = 3
    queue_delay_threshold_ms: Optional[float] = None  # X; None until calibrated
    calibration: CalibrationSettings = field(default_factory=CalibrationSettings)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LabelingConfig":
        data = require_mapping(data, "labeling")
        reject_unknown(
            data,
            ("horizon_s", "utilisation_pct", "loss_pct", "min_consecutive_s",
             "queue_delay_threshold_ms", "calibration"),
            "labeling",
        )
        cal = require_mapping(data.get("calibration") or {}, "labeling.calibration")
        reject_unknown(
            cal, ("source_scenario", "percentile", "max_utilisation_pct", "min_threshold_ms"),
            "labeling.calibration",
        )
        d = CalibrationSettings()
        calibration = CalibrationSettings(
            source_scenario=get_str(cal, "source_scenario", "labeling.calibration", d.source_scenario),
            percentile=get_float(cal, "percentile", "labeling.calibration", d.percentile,
                                 minimum=50.0, maximum=100.0),
            max_utilisation_pct=get_float(cal, "max_utilisation_pct", "labeling.calibration",
                                          d.max_utilisation_pct, minimum=0.0, maximum=100.0),
            min_threshold_ms=get_float(cal, "min_threshold_ms", "labeling.calibration",
                                       d.min_threshold_ms, minimum=0.0),
        )
        return cls(
            horizon_s=get_int(data, "horizon_s", "labeling", 30, minimum=1, maximum=3600),
            utilisation_pct=get_float(data, "utilisation_pct", "labeling", 90.0, minimum=0.0, maximum=200.0),
            loss_pct=get_float(data, "loss_pct", "labeling", 1.0, minimum=0.0, maximum=100.0),
            min_consecutive_s=get_int(data, "min_consecutive_s", "labeling", 3, minimum=1, maximum=3600),
            queue_delay_threshold_ms=get_float(
                data, "queue_delay_threshold_ms", "labeling", None, minimum=0.0),
            calibration=calibration,
        )

    def with_threshold(self, threshold_ms: float) -> "LabelingConfig":
        from dataclasses import replace

        return replace(self, queue_delay_threshold_ms=float(threshold_ms))

    def to_dict(self) -> dict:
        return {
            "horizon_s": self.horizon_s,
            "utilisation_pct": self.utilisation_pct,
            "loss_pct": self.loss_pct,
            "min_consecutive_s": self.min_consecutive_s,
            "queue_delay_threshold_ms": self.queue_delay_threshold_ms,
        }


def load_labeling(config_dir: Optional[PathLike] = None) -> LabelingConfig:
    base = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    try:
        return LabelingConfig.from_mapping(load_yaml(base / "labeling.yaml"))
    except ConfigError as exc:
        raise ConfigError(f"[{base / 'labeling.yaml'}] {exc}") from exc


# ------------------------------------------------------------------------- experiments
@dataclass(frozen=True)
class TimelineSettings:
    pre_roll_s: int = 5  # collectors run this long before scenario time 0 (raw data only)
    tail_s: int = 3  # ... and this long after the scenario end (lets late probe replies arrive)


@dataclass(frozen=True)
class ProbeSettings:
    src: str = "h4"
    dst: str = "h8"
    interval_s: float = 0.2
    payload_bytes: int = 56


@dataclass(frozen=True)
class CaptureSettings:
    enabled: bool = True
    snaplen: int = 96
    interface: str = "left"  # which end of the bottleneck link to capture on: left (s1) or right (s2)
    keep_pcap: bool = False
    nfstream: bool = True  # extract flows from the capture (needs enabled: true)


@dataclass(frozen=True)
class ValidationSettings:
    cpu_saturation_pct: float = 100.0  # a CPU sample at/above this value invalidates the run
    max_missing_fraction: float = 0.05  # required per-second metrics may miss at most this share


@dataclass(frozen=True)
class OutputSettings:
    data_dir: Path = PROJECT_ROOT / "data"
    results_dir: Path = PROJECT_ROOT / "results"


@dataclass(frozen=True)
class ExperimentConfig:
    """One campaign: which scenarios, how often, with which seeds and collectors."""

    name: str
    scenarios: Tuple[str, ...]
    repetitions: int
    seeds: Tuple[int, ...]
    duration_s: Optional[float] = None  # None: use each scenario's duration_s
    timeline: TimelineSettings = field(default_factory=TimelineSettings)
    probe: ProbeSettings = field(default_factory=ProbeSettings)
    capture: CaptureSettings = field(default_factory=CaptureSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    check_ping: bool = True

    def __post_init__(self) -> None:
        if len(self.seeds) != self.repetitions:
            raise ConfigError(
                f"experiment.seeds: expected {self.repetitions} seeds (one per repetition), got {len(self.seeds)}"
            )
        if len(set(self.seeds)) != len(self.seeds):
            raise ConfigError("experiment.seeds: seeds must be distinct")
        if self.capture.nfstream and not self.capture.enabled:
            raise ConfigError("experiment.capture: nfstream needs capture.enabled: true (flows come from the capture)")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], base_dir: Path = PROJECT_ROOT) -> "ExperimentConfig":
        data = require_mapping(data, "experiment")
        reject_unknown(
            data,
            ("name", "scenarios", "repetitions", "seeds", "base_seed", "duration_s", "timeline", "probe",
             "capture", "validation", "output", "check_ping"),
            "experiment",
        )

        def section(name: str, keys: Tuple[str, ...]) -> Mapping[str, Any]:
            sec = require_mapping(data.get(name) or {}, f"experiment.{name}")
            reject_unknown(sec, keys, f"experiment.{name}")
            return sec

        name = get_str(data, "name", "experiment")
        if "scenarios" not in data:
            raise ConfigError("experiment.scenarios: required value is missing")
        scenarios = tuple(str(x).strip() for x in as_list(data["scenarios"], "experiment.scenarios"))
        repetitions = get_int(data, "repetitions", "experiment", 3, minimum=1, maximum=100)
        if "seeds" in data and data["seeds"] is not None:
            raw = as_list(data["seeds"], "experiment.seeds")
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in raw):
                raise ConfigError("experiment.seeds: expected a list of integers")
            seeds = tuple(raw)
        else:
            base_seed = get_int(data, "base_seed", "experiment", 42, minimum=0)
            seeds = tuple(base_seed + i for i in range(repetitions))

        s = section("timeline", ("pre_roll_s", "tail_s"))
        timeline = TimelineSettings(
            pre_roll_s=get_int(s, "pre_roll_s", "experiment.timeline", 5, minimum=2, maximum=120),
            tail_s=get_int(s, "tail_s", "experiment.timeline", 3, minimum=2, maximum=120),
        )
        s = section("probe", ("src", "dst", "interval_s", "payload_bytes"))
        probe = ProbeSettings(
            src=get_str(s, "src", "experiment.probe", "h4"),
            dst=get_str(s, "dst", "experiment.probe", "h8"),
            interval_s=get_float(s, "interval_s", "experiment.probe", 0.2, minimum=0.2, maximum=1.0),
            payload_bytes=get_int(s, "payload_bytes", "experiment.probe", 56, minimum=0, maximum=1400),
        )
        s = section("capture", ("enabled", "snaplen", "interface", "keep_pcap", "nfstream"))
        capture = CaptureSettings(
            enabled=get_bool(s, "enabled", "experiment.capture", True),
            snaplen=get_int(s, "snaplen", "experiment.capture", 96, minimum=64, maximum=65535),
            interface=get_str(s, "interface", "experiment.capture", "left", choices=("left", "right")),
            keep_pcap=get_bool(s, "keep_pcap", "experiment.capture", False),
            nfstream=get_bool(s, "nfstream", "experiment.capture", True),
        )
        s = section("validation", ("cpu_saturation_pct", "max_missing_fraction"))
        validation = ValidationSettings(
            cpu_saturation_pct=get_float(s, "cpu_saturation_pct", "experiment.validation", 100.0,
                                         minimum=50.0, maximum=100.0),
            max_missing_fraction=get_float(s, "max_missing_fraction", "experiment.validation", 0.05,
                                           minimum=0.0, maximum=1.0),
        )
        s = section("output", ("data_dir", "results_dir"))
        output = OutputSettings(
            data_dir=_resolve(base_dir, get_str(s, "data_dir", "experiment.output", "data")),
            results_dir=_resolve(base_dir, get_str(s, "results_dir", "experiment.output", "results")),
        )
        return cls(
            name=name,
            scenarios=scenarios,
            repetitions=repetitions,
            seeds=seeds,
            duration_s=get_float(data, "duration_s", "experiment", None, minimum=1.0, maximum=7200.0),
            timeline=timeline,
            probe=probe,
            capture=capture,
            validation=validation,
            output=output,
            check_ping=get_bool(data, "check_ping", "experiment", True),
        )


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def load_experiment_config(path: PathLike, base_dir: Path = PROJECT_ROOT) -> ExperimentConfig:
    """Load and validate a campaign file such as ``configs/experiments/phase2.yaml``."""
    path = Path(path)
    try:
        return ExperimentConfig.from_mapping(load_yaml(path), base_dir=base_dir)
    except ConfigError as exc:
        raise ConfigError(f"[{path}] {exc}") from exc
