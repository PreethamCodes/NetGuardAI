"""Interface counters (``ip -s -j link``): throughput, packets/s and utilisation.

The two bottleneck interfaces live in the root namespace (they are the switch
ports ``s1-eth5`` / ``s2-eth5``), so no namespace entry is needed. The ``tx``
counters of a switch port are what the port has put on the wire, i.e. after the
tbf rate limiter:

* ``fwd`` = egress of the left switch port  (traffic going left -> right)
* ``rev`` = egress of the right switch port (traffic going right -> left)

Counter bytes include the 14-byte Ethernet header, so a fully loaded 10 Mbit/s
link reads about 10.09 Mbit/s (utilisation up to ~100.9 %); values are not clipped.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from netguard.experiments.collectors.base import CommandRunner, PeriodicCollector, run_command
from netguard.experiments.timeline import Timeline

#: A second whose two bracketing samples are further apart / closer than this is not trusted.
MIN_DT_S, MAX_DT_S = 0.5, 1.5


def parse_ip_link_json(text: str, devices: Sequence[str]) -> Dict[str, Dict[str, int]]:
    """Counters of ``devices`` from ``ip -s -j link show`` output."""
    wanted = set(devices)
    found: Dict[str, Dict[str, int]] = {}
    for link in json.loads(text):
        name = link.get("ifname")
        if name not in wanted:
            continue
        stats = link["stats64"]
        found[name] = {
            "rx_bytes": int(stats["rx"]["bytes"]), "rx_packets": int(stats["rx"]["packets"]),
            "rx_dropped": int(stats["rx"]["dropped"]),
            "tx_bytes": int(stats["tx"]["bytes"]), "tx_packets": int(stats["tx"]["packets"]),
            "tx_dropped": int(stats["tx"]["dropped"]),
        }
    missing = wanted - set(found)
    if missing:
        raise ValueError(f"interface(s) not found in ip output: {sorted(missing)}")
    return found


class InterfaceCollector(PeriodicCollector):
    """Reads the byte/packet counters of the bottleneck ports once per tick."""

    name = "interface"

    def __init__(self, timeline: Timeline, devices: Mapping[str, str], runner: CommandRunner = run_command, **kw: Any):
        super().__init__(timeline, **kw)
        self.devices = dict(devices)  # {"fwd": "s1-eth5", "rev": "s2-eth5"}
        self._run = runner

    def sample(self, tick: int, ts: float) -> List[Dict[str, Any]]:
        rc, out = self._run(["ip", "-s", "-j", "link", "show"])
        if rc != 0:
            raise RuntimeError(f"ip link failed (rc={rc}): {out.strip()[:200]}")
        counters = parse_ip_link_json(out, list(self.devices.values()))
        return [{"direction": d, "dev": dev, **counters[dev]} for d, dev in self.devices.items()]


def _delta_rate(samples: pd.DataFrame, column: str, n_seconds: int) -> "pd.Series":
    """Per-second rate of a cumulative counter: second k = (tick k+1 - tick k) / dt."""
    by_tick = samples.set_index("tick")
    values = by_tick[column]
    stamps = by_tick["ts"]
    out = []
    for k in range(n_seconds):
        if k in values.index and (k + 1) in values.index:
            dt = float(stamps[k + 1] - stamps[k])
            diff = float(values[k + 1] - values[k])
            out.append(diff / dt if MIN_DT_S <= dt <= MAX_DT_S and diff >= 0 else math.nan)
        else:
            out.append(math.nan)
    return pd.Series(out, dtype="float64")


def derive_interface_metrics(
    raw: Sequence[Mapping[str, Any]], n_seconds: int, capacity_mbps: float
) -> pd.DataFrame:
    """Per-second ``fwd_*`` / ``rev_*`` throughput, pps and utilisation (NaN where unmeasured)."""
    frame = pd.DataFrame(list(raw))
    columns: Dict[str, "pd.Series"] = {}
    for direction in ("fwd", "rev"):
        if frame.empty or direction not in set(frame["direction"]):
            sub = pd.DataFrame({"tick": [], "ts": [], "tx_bytes": [], "tx_packets": [], "tx_dropped": []})
        else:
            sub = frame[frame["direction"] == direction].drop_duplicates("tick", keep="last")
        bytes_per_s = _delta_rate(sub, "tx_bytes", n_seconds) if len(sub) else pd.Series([math.nan] * n_seconds)
        pps = _delta_rate(sub, "tx_packets", n_seconds) if len(sub) else pd.Series([math.nan] * n_seconds)
        dropped = _delta_rate(sub, "tx_dropped", n_seconds) if len(sub) else pd.Series([math.nan] * n_seconds)
        columns[f"{direction}_tx_bytes_per_s"] = bytes_per_s
        columns[f"{direction}_throughput_mbps"] = bytes_per_s * 8 / 1e6
        columns[f"{direction}_pps"] = pps
        columns[f"{direction}_utilisation_pct"] = bytes_per_s * 8 / 1e6 / capacity_mbps * 100.0
        columns[f"{direction}_dev_tx_dropped_per_s"] = dropped
    return pd.DataFrame(columns)
