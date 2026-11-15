"""Validation report: what was actually collected, from the stored outputs only."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from netguard.experiments import dataset as ds
from netguard.experiments.calibration import effective_labeling, load_calibration
from netguard.experiments.config import ExperimentConfig, LabelingConfig

#: Metric groups reported per experiment, with the column that proves the group delivered data.
METRIC_GROUPS = {
    "throughput (ip -s link)": "fwd_throughput_mbps",
    "packets per second": "fwd_pps",
    "bandwidth utilisation": "fwd_utilisation_pct",
    "RTT (ping probe)": "rtt_mean_ms",
    "jitter (ping probe)": "jitter_ms",
    "probe loss": "probe_loss_pct",
    "tc loss / drops": "fwd_tc_loss_pct",
    "queue backlog (tc -s qdisc)": "fwd_queue_backlog_bytes",
    "queue delay": "fwd_queue_delay_ms",
    "active connections (ss)": "active_connections",
    "TCP retransmissions (ss)": "tcp_retransmissions",
    "VM CPU (psutil)": "cpu_percent",
    "VM RAM (psutil)": "ram_percent",
    "TShark packets": "tshark_pkts",
    "NFStream flows": "nf_active_flows",
}


def _fmt(x: Any, digits: int = 2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def write_report(cfg: ExperimentConfig, labeling: LabelingConfig) -> Path:
    data_dir = cfg.output.data_dir
    labeling = effective_labeling(labeling, data_dir)
    metas = [m for m in ds.list_metadata(data_dir) if m["campaign"] == cfg.name]
    counts = Counter(m["status"] for m in metas)
    lines: List[str] = [f"# Phase 2 validation report - campaign `{cfg.name}`", "",
                        f"Generated from `{data_dir}` (stored outputs only).", "",
                        "## Run status", "",
                        f"- experiments recorded: {len(metas)}",
                        f"- success: {counts[ds.STATUS_SUCCESS]}, failed: {counts[ds.STATUS_FAILED]}, "
                        f"invalid: {counts[ds.STATUS_INVALID]}, still marked running: {counts[ds.STATUS_RUNNING]}", ""]
    calibration = load_calibration(data_dir)
    lines += ["## Queue-delay threshold X", ""]
    if calibration:
        lines += [f"- X = **{calibration['queue_delay_threshold_ms']:g} ms** "
                  f"(p{calibration['percentile']:g} = {calibration['raw_percentile_ms']:.3f} ms over "
                  f"{calibration['samples_used']} NORMAL seconds with utilisation < "
                  f"{calibration['max_utilisation_pct']:g}%; floor applied: {calibration['floor_applied']})",
                  f"- calibrated from: {', '.join(calibration['experiment_ids'])}", ""]
    else:
        lines += ["- not calibrated yet", ""]

    lines += ["## Experiments", "",
              "| experiment | scenario | rep | seed | status | rows | cpu max % | congested s | onsets | labelled | notes |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    verified: Dict[str, Dict[str, float]] = {}
    for m in sorted(metas, key=lambda x: (x["scenario"], x["repetition"], x["attempt"])):
        path = data_dir / "processed" / f"{m['experiment_id']}.parquet"
        rows = congested = onsets = "n/a"
        labelled = m.get("labels", {}).get("status", "n/a")
        if path.is_file():
            frame = ds.read_parquet(path)
            rows = len(frame)
            if "current_congestion" in frame.columns and frame["current_congestion"].notna().any():
                cur = frame["current_congestion"].fillna(False).astype(bool)
                congested = int(cur.sum())
                onsets = int((cur & ~cur.shift(1, fill_value=False)).sum())
            for label, column in METRIC_GROUPS.items():
                if column in frame.columns:
                    verified.setdefault(m["scenario"], {}).setdefault(label, 0.0)
                    if m["status"] == ds.STATUS_SUCCESS:
                        verified[m["scenario"]][label] = max(
                            verified[m["scenario"]][label], float(frame[column].notna().mean()))
        notes = "; ".join((m.get("errors") or [])[:1] + (m.get("invalid_reasons") or []))[:120].replace("|", "/")
        lines.append(f"| {m['experiment_id']} | {m['scenario']} | {m['repetition']} | {m['seed']} | {m['status']} | "
                     f"{rows} | {_fmt(m.get('checks', {}).get('cpu_max_percent'), 1)} | {congested} | {onsets} | "
                     f"{labelled} | {notes} |")

    lines += ["", "## Metrics with real data (best fraction of non-null seconds, successful runs)", ""]
    if verified:
        header = sorted({k for v in verified.values() for k in v})
        lines += ["| metric | " + " | ".join(sorted(verified)) + " |", "|---|" + "---|" * len(verified)]
        for metric in header:
            lines.append(f"| {metric} | " + " | ".join(
                f"{verified[s].get(metric, 0.0) * 100:.0f}%" if metric in verified[s] else "-" for s in sorted(verified))
                + " |")
    else:
        lines.append("no successful experiments yet")

    lines += ["", "## Labelling definition", "",
              f"- congested second: utilisation >= {labeling.utilisation_pct:g}% and (queue delay >= X or loss > "
              f"{labeling.loss_pct:g}%) for >= {labeling.min_consecutive_s} consecutive seconds",
              f"- future_congestion: a new onset within the next {labeling.horizon_s} s; the last "
              f"{labeling.horizon_s} s of every run have null future labels and `future_label_available = False`", ""]
    out = cfg.output.results_dir / "validation_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = cfg.output.results_dir / "experiments_summary.json"
    summary.write_text(json.dumps(
        [{k: m[k] for k in ("experiment_id", "scenario", "repetition", "seed", "status", "started_at", "ended_at",
                            "errors", "invalid_reasons")} for m in metas], indent=2, default=str) + "\n",
        encoding="utf-8")
    return out
