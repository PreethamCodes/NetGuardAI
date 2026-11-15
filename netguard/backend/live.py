"""Live ingestion: per-second monitoring samples -> features -> predictions.

A monitor (for example the Phase 2 collectors) posts one sample per second. The backend keeps the
last ``BUFFER_S`` seconds of each stream, builds the same features as in training from that history,
and predicts only when the feature vector is complete (after about 30 s). Samples are never
interpolated; a stream with a gap starts over.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from netguard.backend.inference import Predictor, _clean
from netguard.backend.replay import alerts_for
from netguard.experiments.config import LabelingConfig
from netguard.prep.features import build_features

logger = logging.getLogger(__name__)

BUFFER_S = 45  # >= longest lag (30 s) + window


class IngestSample(BaseModel):
    """One second of measurements, named like the Phase 2 dataset columns.

    A metric that could not be measured is sent as ``null`` (never 0): the backend then withholds the predictions
    that depend on it instead of guessing.
    """

    stream_id: str = Field("live-1", max_length=64)
    timestamp_s: float
    throughput_mbps: Optional[float] = None
    fwd_pps: Optional[float] = None
    utilisation_pct: Optional[float] = None
    rtt_mean_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    probe_loss_pct: Optional[float] = None
    loss_pct: Optional[float] = None
    fwd_queue_backlog_bytes: Optional[float] = None
    rev_queue_backlog_bytes: Optional[float] = 0.0
    queue_delay_ms: Optional[float] = None
    fwd_queue_drops_per_s: Optional[float] = 0.0
    rev_queue_drops_per_s: Optional[float] = 0.0
    active_connections: Optional[float] = None
    tcp_retransmissions: Optional[float] = None
    nf_active_flows: Optional[float] = None
    nf_new_flows: Optional[float] = None
    tshark_pkts: Optional[float] = None
    tshark_tcp_pkts: Optional[float] = None
    tshark_udp_pkts: Optional[float] = None
    tshark_icmp_pkts: Optional[float] = None
    tshark_dns_pkts: Optional[float] = None
    tshark_http_pkts: Optional[float] = None
    syn_packets: Optional[float] = Field(None, description="SYN packets seen in this second")
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None


class LiveStreams:
    def __init__(self, predictor: Predictor, labeling: LabelingConfig) -> None:
        self.predictor = predictor
        self.labeling = labeling
        self._rows: Dict[str, Deque[Dict[str, Any]]] = {}
        self._syn: Dict[str, Deque[Optional[float]]] = {}
        self._counter: Dict[str, int] = {}
        self._last_ts: Dict[str, float] = {}

    def ingest(self, sample: IngestSample) -> Dict[str, Any]:
        sid = sample.stream_id
        last = self._last_ts.get(sid)
        if last is not None and sample.timestamp_s <= last:
            raise ValueError(f"timestamp {sample.timestamp_s} is not after the previous sample ({last})")
        if last is not None and sample.timestamp_s - last > 1.5:  # a gap: history is no longer contiguous
            self.reset(sid)
        self._last_ts[sid] = sample.timestamp_s
        second = self._counter.get(sid, 0)
        self._counter[sid] = second + 1
        row = {k: (np.nan if v is None else v) for k, v in sample.model_dump(exclude={"syn_packets", "stream_id"}).items()}
        row.update({"experiment_id": sid, "second": second})
        self._rows.setdefault(sid, deque(maxlen=BUFFER_S)).append(row)
        self._syn.setdefault(sid, deque(maxlen=BUFFER_S)).append(sample.syn_packets)

        frame = pd.DataFrame(list(self._rows[sid]))
        # a second without a SYN count stays NaN (never 0): it only blocks the 5 s window features that use it
        syn = pd.Series([np.nan if v is None else v for v in self._syn[sid]], dtype="float64")
        feats = build_features(
            frame, flows=None, anchor_wall=0.0, util_pct=self.labeling.utilisation_pct,
            queue_ms=float(self.labeling.queue_delay_threshold_ms or 0.0), loss_pct=self.labeling.loss_pct,
            min_consecutive=self.labeling.min_consecutive_s, syn=syn,
        )
        pred = self.predictor.predict(feats.tail(1))[0]
        metrics = {k: _clean(v) for k, v in sample.model_dump(exclude={"stream_id"}).items()}
        record = {"source": "live", "historical": False, "stream_id": sid, "second": second,
                  "timestamp_s": sample.timestamp_s, "metrics": metrics, "predictions": pred}
        record["alerts"] = alerts_for(pred, second, sample.timestamp_s)
        return record

    def reset(self, stream_id: str) -> None:
        for d in (self._rows, self._syn, self._counter, self._last_ts):
            d.pop(stream_id, None)
