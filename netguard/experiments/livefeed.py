"""Live feeder: turns the running collectors into one backend sample per second.

Second ``k`` is finished at ``anchor + k + 1``; the feeder waits ``delay_s`` more (so the closing counter sample, the
last ping replies and the last packets have arrived, and the flows alive in second ``k`` can be told apart from flows
that already ended: the offline flow definition needs each flow's last packet), derives the metrics of second ``k`` with the *same* functions the
offline dataset uses, and POSTs them to the backend's ``/api/ingest``. A metric that cannot be measured is sent as
``null`` (never 0 or a guess); the backend then withholds the predictions that need it.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional

import pandas as pd

from netguard.experiments.collectors.base import Collector
from netguard.experiments.collectors.connections import derive_connection_metrics
from netguard.experiments.collectors.interface import derive_interface_metrics
from netguard.experiments.collectors.livepackets import LivePacketCollector
from netguard.experiments.collectors.probe import ProbeCollector, derive_probe_metrics
from netguard.experiments.collectors.queue import derive_queue_metrics
from netguard.experiments.collectors.resources import derive_resource_metrics
from netguard.experiments.dataset import add_summary_columns
from netguard.experiments.timeline import Timeline

logger = logging.getLogger(__name__)

#: IngestSample field -> column of the per-second table
FIELD_MAP = {
    "throughput_mbps": "throughput_mbps", "fwd_pps": "fwd_pps", "utilisation_pct": "utilisation_pct",
    "rtt_mean_ms": "rtt_mean_ms", "jitter_ms": "jitter_ms", "probe_loss_pct": "probe_loss_pct", "loss_pct": "loss_pct",
    "fwd_queue_backlog_bytes": "fwd_queue_backlog_bytes", "rev_queue_backlog_bytes": "rev_queue_backlog_bytes",
    "queue_delay_ms": "queue_delay_ms", "fwd_queue_drops_per_s": "fwd_queue_drops_per_s",
    "rev_queue_drops_per_s": "rev_queue_drops_per_s", "active_connections": "active_connections",
    "tcp_retransmissions": "tcp_retransmissions", "cpu_percent": "cpu_percent", "ram_percent": "ram_percent",
}


def _json_number(v: Any) -> Optional[float]:
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def http_post(url: str, payload: Dict[str, Any], timeout: float = 3.0) -> int:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local backend URL given by the user
        return resp.status


def build_sample(second: int, timeline: Timeline, collectors: Mapping[str, Collector], capacity_mbps: float,
                 probe_interval_s: float) -> Dict[str, Any]:
    """Metrics of dataset second ``second`` from what the collectors have recorded so far."""
    n = second + 1
    probe: ProbeCollector = collectors["probe"]  # type: ignore[assignment]
    packets: LivePacketCollector = collectors["live_packets"]  # type: ignore[assignment]
    frames = [
        derive_interface_metrics(collectors["interface"].raw_samples(), n, capacity_mbps),
        derive_queue_metrics(collectors["queue"].raw_samples(), n, capacity_mbps),
        derive_probe_metrics(probe.replies, n, timeline.anchor_wall, probe_interval_s, None),
        derive_connection_metrics(collectors["connections"].raw_samples(), n),
        derive_resource_metrics(collectors["resources"].raw_samples(), n),
    ]
    row = add_summary_columns(pd.concat([f.reset_index(drop=True) for f in frames], axis=1)).iloc[second]
    sample: Dict[str, Any] = {out: _json_number(row[col]) for out, col in FIELD_MAP.items()}
    sample.update({k: _json_number(v) for k, v in packets.aggregator.stats(second).items()})
    sample["timestamp_s"] = timeline.anchor_wall + second
    return sample


class LiveFeeder:
    def __init__(self, timeline: Timeline, collectors: Mapping[str, Collector], *, backend_url: str, stream_id: str,
                 capacity_mbps: float, probe_interval_s: float = 0.2, delay_s: float = 4.0,
                 poster: Callable[[str, Dict[str, Any]], int] = http_post,
                 clock: Callable[[], float] = time.time) -> None:
        self.timeline, self.collectors = timeline, collectors
        self.url = backend_url.rstrip("/") + "/api/ingest"
        self.stream_id = stream_id
        self.capacity, self.interval, self.delay = capacity_mbps, probe_interval_s, delay_s
        self._poster, self._clock = poster, clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.samples: List[Dict[str, Any]] = []  # what was sent, for the live-vs-offline comparison
        self.posted = 0
        self.failed = 0
        self.last_error = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="live-feeder", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        for k in range(self.timeline.duration_s):
            due = self.timeline.tick_time(k + 1) + self.delay
            if self._stop.wait(max(0.0, due - self._clock())):
                return
            try:
                sample = build_sample(k, self.timeline, self.collectors, self.capacity, self.interval)
            except Exception as exc:  # noqa: BLE001 - a bad second must not stop the feed
                self.failed += 1
                self.last_error = f"second {k}: {type(exc).__name__}: {exc}"
                logger.warning("live feeder: %s", self.last_error)
                continue
            self.samples.append({"second": k, **sample})
            try:
                self._poster(self.url, {**sample, "stream_id": self.stream_id})
                self.posted += 1
            except (urllib.error.URLError, OSError, ValueError) as exc:
                self.failed += 1
                self.last_error = f"second {k}: could not post to {self.url}: {exc}"
                if self.failed <= 3 or self.failed % 20 == 0:
                    logger.warning("live feeder: %s", self.last_error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def summary(self) -> Dict[str, Any]:
        return {"stream_id": self.stream_id, "backend": self.url, "posted": self.posted, "failed": self.failed,
                "last_error": self.last_error}


def compare_live_with_offline(samples: List[Dict[str, Any]], observations: pd.DataFrame) -> Dict[str, Any]:
    """Per column: how far the live-computed values are from the offline dataset values of the same run."""
    live = pd.DataFrame(samples).set_index("second")
    cols = [c for c in ("throughput_mbps", "utilisation_pct", "queue_delay_ms", "loss_pct", "rtt_mean_ms", "jitter_ms",
                        "active_connections", "tcp_retransmissions", "tshark_pkts", "tshark_tcp_pkts", "tshark_udp_pkts",
                        "tshark_icmp_pkts", "tshark_dns_pkts", "tshark_http_pkts", "nf_new_flows", "nf_active_flows")
            if c in live.columns and c in observations.columns]
    off = observations.set_index("second")
    out: Dict[str, Any] = {"seconds_compared": int(len(live.index.intersection(off.index))), "columns": {}}
    for c in cols:
        a, b = live[c].astype("float64"), off[c].astype("float64")
        both = pd.concat([a, b], axis=1, keys=["live", "offline"]).dropna()
        if both.empty:
            out["columns"][c] = {"n": 0}
            continue
        diff = (both["live"] - both["offline"]).abs()
        out["columns"][c] = {
            "n": int(len(both)), "mean_abs_diff": float(diff.mean()), "max_abs_diff": float(diff.max()),
            "offline_mean": float(both["offline"].mean()),
            "relative_mean_diff": float(diff.mean() / both["offline"].abs().mean()) if both["offline"].abs().mean() else None,
            "correlation": float(both["live"].corr(both["offline"])) if len(both) > 2 and both["live"].std() > 0
            and both["offline"].std() > 0 else None,
        }
    return out
