"""Active TCP connections and retransmissions from ``ss -ti`` inside every host namespace.

* ``active_connections`` = number of distinct ESTABLISHED TCP connections in the
  topology (both endpoints of a connection live in the topology, so they are
  de-duplicated by their endpoint pair).
* ``tcp_retransmissions`` = new retransmitted segments since the previous tick, summed
  over every socket (``retrans:<current>/<total>`` in ``ss -ti``). Sockets that open and
  close between two ticks are only seen if they exist at a tick, so their retransmissions
  can be missed: the metric slightly under-counts short-lived connections.

``ss`` runs in each host's network namespace through ``nsenter -t <pid> -n`` from the
root namespace (thread-safe, unlike ``Node.cmd`` which shares one shell per node).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from netguard.experiments.collectors.base import CommandRunner, PeriodicCollector, run_command
from netguard.experiments.timeline import Timeline

_RETRANS = re.compile(r"\bretrans:(\d+)/(\d+)")
_ESTAB = "ESTAB"

Endpoint = Tuple[str, int]


def _split_endpoint(text: str) -> Endpoint:
    host, _, port = text.rpartition(":")
    return host, int(port) if port.isdigit() else 0


def parse_ss(text: str) -> List[Dict[str, Any]]:
    """Parse ``ss -tinH`` output into one dict per socket."""
    sockets: List[Dict[str, Any]] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        parts = lines[i].split()
        if len(parts) >= 5 and not lines[i][0].isspace():
            state, local, peer = parts[0], _split_endpoint(parts[3]), _split_endpoint(parts[4])
            info = lines[i + 1] if i + 1 < len(lines) and lines[i + 1][0].isspace() else ""
            match = _RETRANS.search(info)
            sockets.append({
                "state": state, "local": local, "peer": peer,
                "retrans_total": int(match.group(2)) if match else 0,
            })
            i += 2 if info else 1
        else:
            i += 1
    return sockets


class ConnectionCollector(PeriodicCollector):
    """``hosts`` maps a host name to the pid of its Mininet shell (for ``nsenter``)."""

    name = "connections"

    def __init__(self, timeline: Timeline, host_pids: Mapping[str, int],
                 runner: Callable[[Sequence[str]], Tuple[int, str]] = run_command, **kw: Any):
        super().__init__(timeline, **kw)
        self.host_pids = dict(host_pids)
        self._run = runner
        self._last_total: Dict[Tuple[str, Endpoint, Endpoint], int] = {}
        self._seen_first_tick = False

    def sample(self, tick: int, ts: float) -> List[Dict[str, Any]]:
        connections: set = set()
        n_sockets = 0
        total_now: Dict[Tuple[str, Endpoint, Endpoint], int] = {}
        for host, pid in self.host_pids.items():
            rc, out = self._run(["nsenter", "-t", str(pid), "-n", "ss", "-tinH"])
            if rc != 0:
                raise RuntimeError(f"ss in {host} failed (rc={rc}): {out.strip()[:200]}")
            for sock in parse_ss(out):
                n_sockets += 1
                if sock["state"] == _ESTAB:
                    connections.add(frozenset((sock["local"], sock["peer"])))
                total_now[(host, sock["local"], sock["peer"])] = sock["retrans_total"]
        if self._seen_first_tick:
            delta = sum(max(0, total - self._last_total.get(key, 0)) for key, total in total_now.items())
        else:
            delta = 0  # nothing before the first tick: the baseline, not a measurement
        self._seen_first_tick = True
        self._last_total = total_now
        return [{"active_connections": len(connections), "tcp_sockets": n_sockets, "retrans_new": delta}]


def derive_connection_metrics(raw: Sequence[Mapping[str, Any]], n_seconds: int) -> pd.DataFrame:
    """Second k = the state at tick k+1 (retransmissions: those seen since tick k)."""
    frame = pd.DataFrame(list(raw))
    conns = [float("nan")] * n_seconds
    retrans = [float("nan")] * n_seconds
    if not frame.empty:
        by_tick = frame.drop_duplicates("tick", keep="last").set_index("tick")
        for k in range(n_seconds):
            if k in by_tick.index and (k + 1) in by_tick.index:  # need both ticks: delta is measured between them
                conns[k] = float(by_tick.loc[k + 1, "active_connections"])
                retrans[k] = float(by_tick.loc[k + 1, "retrans_new"])
    return pd.DataFrame({"active_connections": conns, "tcp_retransmissions": retrans})
