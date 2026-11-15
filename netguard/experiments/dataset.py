"""Per-second observation table, experiment metadata and Parquet storage.

Layout (all under ``output.data_dir``)::

    raw/<experiment_id>/<collector>.parquet   unmodified collector samples (+ flows.parquet, protocol_hierarchy.txt)
    processed/<experiment_id>.parquet         one row per scenario second, features + labels
    processed/dataset.parquet                 successful experiments only (``dataset build``)
    metadata/<experiment_id>.json             configuration, seeds, timings, status, errors
    metadata/calibration.json                 the fixed queue-delay threshold X
    captures/<experiment_id>.pcap             only when capture.keep_pcap is true
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from netguard.exceptions import NetGuardError

logger = logging.getLogger(__name__)

STATUS_SUCCESS, STATUS_FAILED, STATUS_INVALID, STATUS_RUNNING = "success", "failed", "invalid", "running"
ID_COLUMNS = ("experiment_id", "scenario", "repetition", "seed", "second", "timestamp_s")

#: Metrics that must be present (see ``validation.max_missing_fraction``) for a run to count as successful.
REQUIRED_METRICS = (
    "fwd_throughput_mbps", "rev_throughput_mbps", "fwd_utilisation_pct", "fwd_pps",
    "fwd_queue_delay_ms", "rev_queue_delay_ms", "fwd_tc_loss_pct", "rev_tc_loss_pct",
    "active_connections", "tcp_retransmissions", "cpu_percent", "ram_percent", "probe_sent",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_experiment_id(scenario: str, repetition: int, now: Optional[datetime] = None) -> str:
    """Unique, sortable ID: ``<UTC time>-<scenario>-r<rep>-<6 hex>``."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{scenario}-r{repetition}-{uuid.uuid4().hex[:6]}"


@dataclass
class ExperimentMetadata:
    experiment_id: str
    campaign: str
    scenario: str
    scenario_label: str
    repetition: int
    seed: int
    duration_s: int
    attempt: int = 1
    status: str = STATUS_RUNNING
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    anchor_wall_s: Optional[float] = None  # wall-clock instant of scenario second 0
    config: Dict[str, Any] = field(default_factory=dict)  # topology, effective links, experiment/labelling settings
    plan: Dict[str, Any] = field(default_factory=dict)  # event counts per tool, offered load summary
    event_results: Dict[str, int] = field(default_factory=dict)
    collectors: Dict[str, Any] = field(default_factory=dict)  # health per collector
    capture: Dict[str, Any] = field(default_factory=dict)
    checks: Dict[str, Any] = field(default_factory=dict)  # completeness, CPU saturation, row count, cleanup
    labels: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    invalid_reasons: List[str] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def mark(self, status: str) -> None:
        self.status = status


def metadata_path(data_dir: Path, experiment_id: str) -> Path:
    return data_dir / "metadata" / f"{experiment_id}.json"


def write_metadata(data_dir: Path, meta: ExperimentMetadata) -> Path:
    path = metadata_path(data_dir, meta.experiment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta.to_dict(), indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic: a crash never leaves half a metadata file
    return path


def load_metadata(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_metadata(data_dir: Path) -> List[Dict[str, Any]]:
    folder = data_dir / "metadata"
    if not folder.is_dir():
        return []
    items = []
    for path in sorted(folder.glob("*.json")):
        if path.name == "calibration.json":
            continue
        items.append(load_metadata(path))
    return items


# ------------------------------------------------------------------ observation table
def assemble_observations(
    meta: ExperimentMetadata, frames: Sequence[pd.DataFrame], n_seconds: int,
) -> pd.DataFrame:
    """Join the per-collector frames (all ``n_seconds`` long, indexed by second) with the ID columns."""
    base = pd.DataFrame({
        "experiment_id": meta.experiment_id, "scenario": meta.scenario, "repetition": meta.repetition,
        "seed": meta.seed, "second": range(n_seconds),
    })
    base["timestamp_s"] = (meta.anchor_wall_s or math.nan) + base["second"]
    parts = [base]
    for frame in frames:
        if len(frame) != n_seconds:
            raise NetGuardError(f"collector frame has {len(frame)} rows, expected {n_seconds}")
        parts.append(frame.reset_index(drop=True))
    obs = pd.concat(parts, axis=1)
    obs["experiment_status"] = meta.status
    return add_summary_columns(obs)


def add_summary_columns(obs: pd.DataFrame) -> pd.DataFrame:
    """Label inputs derived from both directions of the bottleneck (the worse direction counts)."""
    out = obs.copy()
    out["throughput_mbps"] = out[["fwd_throughput_mbps", "rev_throughput_mbps"]].max(axis=1, skipna=False)
    out["utilisation_pct"] = out[["fwd_utilisation_pct", "rev_utilisation_pct"]].max(axis=1, skipna=False)
    out["queue_delay_ms"] = out[["fwd_queue_delay_ms", "rev_queue_delay_ms"]].max(axis=1, skipna=False)
    out["loss_pct"] = out[["fwd_tc_loss_pct", "rev_tc_loss_pct"]].max(axis=1, skipna=False)
    return out


def missing_fraction(obs: pd.DataFrame, columns: Sequence[str]) -> Dict[str, float]:
    return {c: float(obs[c].isna().mean()) if c in obs.columns else 1.0 for c in columns}


# ----------------------------------------------------------------------- parquet I/O
def write_parquet(frame: pd.DataFrame, path: Path, metadata: Optional[Mapping[str, Any]] = None) -> Path:
    """Write ``frame`` to Parquet (zstd), with file-level key/value metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    if metadata:
        merged = dict(table.schema.metadata or {})
        merged[b"netguard"] = json.dumps(metadata, default=str).encode("utf-8")
        table = table.replace_schema_metadata(merged)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return path


def read_parquet(path: Path) -> pd.DataFrame:
    return pq.read_table(path).to_pandas()


def read_file_metadata(path: Path) -> Dict[str, Any]:
    meta = pq.read_schema(path).metadata or {}
    return json.loads(meta[b"netguard"].decode("utf-8")) if b"netguard" in meta else {}


def write_raw(raw_dir: Path, name: str, rows: Sequence[Mapping[str, Any]]) -> Optional[Path]:
    """Unmodified collector samples; skipped (and reported by the caller) when there are none."""
    if not rows:
        return None
    return write_parquet(pd.DataFrame(list(rows)), raw_dir / f"{name}.parquet")


EXPECTED_DTYPES = {
    "experiment_id": "string", "scenario": "string", "repetition": "int", "seed": "int", "second": "int",
    "timestamp_s": "float",
}


def validate_observation_schema(obs: pd.DataFrame, n_seconds: Optional[int] = None) -> List[str]:
    """Structural problems of an observation table (empty list = OK)."""
    problems: List[str] = []
    for column in ID_COLUMNS:
        if column not in obs.columns:
            problems.append(f"missing column {column}")
    if problems:
        return problems
    if obs["experiment_id"].nunique() != 1:
        problems.append("more than one experiment_id in one table")
    seconds = obs["second"].tolist()
    if seconds != list(range(len(seconds))):
        problems.append("'second' is not a gap-free 0..n-1 sequence")
    if n_seconds is not None and len(obs) != n_seconds:
        problems.append(f"expected {n_seconds} rows, got {len(obs)}")
    if obs["timestamp_s"].isna().any():
        problems.append("timestamp_s contains nulls")
    elif not (obs["timestamp_s"].diff().dropna().round(6) == 1.0).all():
        problems.append("timestamp_s is not strictly 1 s apart")
    return problems


def build_dataset(data_dir: Path, scenarios: Optional[Sequence[str]] = None) -> Path:
    """Concatenate the processed tables of all *successful* experiments into ``processed/dataset.parquet``."""
    frames = []
    for meta in list_metadata(data_dir):
        if meta["status"] != STATUS_SUCCESS:
            continue
        if scenarios and meta["scenario"] not in scenarios:
            continue
        path = data_dir / "processed" / f"{meta['experiment_id']}.parquet"
        if not path.is_file():
            raise NetGuardError(f"experiment {meta['experiment_id']} is 'success' but {path} is missing")
        frames.append(read_parquet(path))
    if not frames:
        raise NetGuardError("no successful experiments found to build a dataset from")
    dataset = pd.concat(frames, ignore_index=True)
    return write_parquet(dataset, data_dir / "processed" / "dataset.parquet",
                         {"experiments": dataset["experiment_id"].nunique(), "rows": len(dataset),
                          "built_at": utc_now()})
