"""Flow extraction with NFStream from the header-only capture.

NFStream reads the pcap offline (after the experiment, outside the measurement
window) and produces one record per bidirectional flow. Per-second aggregates
(``nf_active_flows``, ``nf_new_flows``) are derived from the flow start/end times;
the full flow table is stored in ``data/raw/<id>/flows.parquet``.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, List

import pandas as pd

from netguard.exceptions import NetGuardError

logger = logging.getLogger(__name__)

#: Columns kept from NFStream's output (statistical_analysis=True adds many more; they are kept as-is).
CORE_COLUMNS = (
    "id", "bidirectional_first_seen_ms", "bidirectional_last_seen_ms", "bidirectional_duration_ms",
    "bidirectional_packets", "bidirectional_bytes", "src_ip", "src_port", "dst_ip", "dst_port", "protocol",
    "src2dst_packets", "src2dst_bytes", "dst2src_packets", "dst2src_bytes",
)


def extract_flows(pcap: Path, *, idle_timeout_s: int = 120, active_timeout_s: int = 1800) -> pd.DataFrame:
    """Run NFStream on ``pcap`` and return the flow table (empty frame if the capture has no flows)."""
    try:
        from nfstream import NFStreamer
    except ImportError as exc:
        raise NetGuardError("NFStream is not installed (pip install -r requirements.txt)") from exc
    if not pcap.is_file():
        raise NetGuardError(f"capture file not found: {pcap}")
    streamer = NFStreamer(
        source=str(pcap), statistical_analysis=True, n_dissections=0, n_meters=1,
        idle_timeout=idle_timeout_s, active_timeout=active_timeout_s, decode_tunnels=False,
    )
    flows = streamer.to_pandas()
    if flows is None:
        raise NetGuardError("NFStream returned no result")
    logger.info("NFStream: %d flows from %s", len(flows), pcap.name)
    return flows


def derive_flow_metrics(flows: pd.DataFrame, n_seconds: int, anchor_wall: float) -> pd.DataFrame:
    """Per-second number of flows alive (``nf_active_flows``) and flows that started (``nf_new_flows``)."""
    active = [0.0] * n_seconds
    new = [0.0] * n_seconds
    if not flows.empty:
        start = (flows["bidirectional_first_seen_ms"].astype(float) / 1000.0 - anchor_wall)
        end = (flows["bidirectional_last_seen_ms"].astype(float) / 1000.0 - anchor_wall)
        for s, e in zip(start, end):
            first, last = int(math.floor(s)), int(math.floor(e))
            if 0 <= first < n_seconds:
                new[first] += 1.0
            for k in range(max(first, 0), min(last, n_seconds - 1) + 1):
                active[k] += 1.0
    return pd.DataFrame({"nf_active_flows": active, "nf_new_flows": new})
