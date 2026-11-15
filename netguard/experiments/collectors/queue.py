"""Queue backlog and drops from ``tc -s -j qdisc`` on the bottleneck ports.

Each shaped port has ``netem`` (root: delay + random loss) with a ``tbf`` child
(rate limit + the drop-tail queue, see :mod:`netguard.topology.tc`):

* **queue backlog** = the ``tbf`` backlog (bytes / packets). The netem backlog is
  packets "on the wire" in the delay line and is *not* queueing delay.
* **queue delay** (ms) = tbf backlog bytes * 8 / link rate.
* **drops**: ``tbf`` drops are queue-overflow drops. The ``netem`` drop counter
  contains random loss and (because netem enqueues into tbf) the overflow drops too,
  so total drops = max(netem drops, tbf drops) and random loss = total - overflow.
* **tc loss %** = total drops / (packets delivered + total drops) in that second.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd

from netguard.experiments.collectors.base import CommandRunner, PeriodicCollector, run_command
from netguard.experiments.collectors.interface import MAX_DT_S, MIN_DT_S
from netguard.experiments.timeline import Timeline


def parse_tc_qdisc_json(text: str) -> Dict[str, Optional[Dict[str, Any]]]:
    """Pick the netem and tbf entries out of ``tc -s -j qdisc show dev X``."""
    entries = json.loads(text)
    result: Dict[str, Optional[Dict[str, Any]]] = {"netem": None, "tbf": None}
    for entry in entries:
        kind = entry.get("kind")
        if kind in result:
            result[kind] = entry
    if result["tbf"] is None:
        raise ValueError("no tbf qdisc reported (is the bottleneck shaped?)")
    return result


def qdisc_row(direction: str, dev: str, parsed: Mapping[str, Optional[Mapping[str, Any]]]) -> Dict[str, Any]:
    tbf, netem = parsed["tbf"], parsed["netem"]
    assert tbf is not None
    return {
        "direction": direction, "dev": dev,
        "tbf_backlog_bytes": int(tbf["backlog"]), "tbf_qlen": int(tbf["qlen"]),
        "tbf_drops": int(tbf["drops"]), "tbf_overlimits": int(tbf["overlimits"]),
        "tbf_packets": int(tbf["packets"]), "tbf_bytes": int(tbf["bytes"]),
        "tbf_rate_bytes_per_s": float(tbf["options"]["rate"]),
        "netem_drops": int(netem["drops"]) if netem else 0,
        "netem_packets": int(netem["packets"]) if netem else 0,
        "netem_backlog_bytes": int(netem["backlog"]) if netem else 0,
    }


class QueueCollector(PeriodicCollector):
    name = "queue"

    def __init__(self, timeline: Timeline, devices: Mapping[str, str], runner: CommandRunner = run_command, **kw: Any):
        super().__init__(timeline, **kw)
        self.devices = dict(devices)
        self._run = runner

    def sample(self, tick: int, ts: float) -> List[Dict[str, Any]]:
        rows = []
        for direction, dev in self.devices.items():
            rc, out = self._run(["tc", "-s", "-j", "qdisc", "show", "dev", dev])
            if rc != 0:
                raise RuntimeError(f"tc qdisc show dev {dev} failed (rc={rc}): {out.strip()[:200]}")
            rows.append(qdisc_row(direction, dev, parse_tc_qdisc_json(out)))
        return rows


def derive_queue_metrics(
    raw: Sequence[Mapping[str, Any]], n_seconds: int, capacity_mbps: float
) -> pd.DataFrame:
    """Per-second queue backlog / delay / drops / loss for both directions (NaN where unmeasured)."""
    frame = pd.DataFrame(list(raw))
    nan = [math.nan] * n_seconds
    out: Dict[str, List[float]] = {}
    for direction in ("fwd", "rev"):
        cols = {k: list(nan) for k in (
            "queue_backlog_bytes", "queue_backlog_packets", "queue_delay_ms", "queue_drops_per_s",
            "random_drops_per_s", "tc_drops_per_s", "tc_loss_pct")}
        if not frame.empty and direction in set(frame["direction"]):
            sub = frame[frame["direction"] == direction].drop_duplicates("tick", keep="last").set_index("tick")
            for k in range(n_seconds):
                if k not in sub.index or (k + 1) not in sub.index:
                    continue
                a, b = sub.loc[k], sub.loc[k + 1]
                dt = float(b["ts"] - a["ts"])
                if not MIN_DT_S <= dt <= MAX_DT_S:
                    continue
                d_tbf = b["tbf_drops"] - a["tbf_drops"]
                d_netem = b["netem_drops"] - a["netem_drops"]
                delivered = b["tbf_packets"] - a["tbf_packets"]
                if min(d_tbf, d_netem, delivered) < 0:
                    continue  # counters reset: leave the second unmeasured
                total = max(d_netem, d_tbf)
                rate = float(b["tbf_rate_bytes_per_s"]) or capacity_mbps * 1e6 / 8
                backlog = float(b["tbf_backlog_bytes"])
                cols["queue_backlog_bytes"][k] = backlog
                cols["queue_backlog_packets"][k] = float(b["tbf_qlen"])
                cols["queue_delay_ms"][k] = backlog / rate * 1000.0
                cols["queue_drops_per_s"][k] = d_tbf / dt
                cols["random_drops_per_s"][k] = max(0, total - d_tbf) / dt
                cols["tc_drops_per_s"][k] = total / dt
                denom = delivered + total
                cols["tc_loss_pct"][k] = total / denom * 100.0 if denom > 0 else 0.0  # no packets: nothing lost
        for name, values in cols.items():
            out[f"{direction}_{name}"] = values
    return pd.DataFrame(out, dtype="float64")
