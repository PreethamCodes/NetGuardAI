"""Linux traffic-control (tc) configuration for emulated links.

Each shaped interface gets::

    root   1:  netem   delay / jitter / loss      (propagation + random loss)
    child  10: tbf     rate / burst / queue limit (capacity + small drop-tail queue)

netem sits in front of the token-bucket filter, the layout recommended in the
netem documentation for combining delay with rate limiting. Packets dropped
because the tbf queue is full show up in ``tc -s qdisc`` for the tbf qdisc,
which later phases use as the bottleneck queue/drop counters.

The functions here do not touch the system: commands are produced as argv
lists and executed through a caller-supplied ``run`` callable, so they can be
unit-tested on any OS.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from netguard.config.environment import LinkParams
from netguard.exceptions import TopologyError

logger = logging.getLogger(__name__)

#: ``run(argv) -> (returncode, combined_output)``
Runner = Callable[[Sequence[str]], Tuple[int, str]]

NETEM_HANDLE = "1:"
TBF_PARENT = "1:1"
TBF_HANDLE = "10:"
MIN_NETEM_LIMIT = 1000


def netem_limit_packets(link: LinkParams) -> int:
    """netem queue (packets) large enough to hold everything "in flight" in the delay line."""
    bytes_per_s = link.bandwidth_mbps * 1e6 / 8
    in_flight = bytes_per_s * (link.delay_ms + link.jitter_ms) / 1000.0 / link.mtu
    return max(MIN_NETEM_LIMIT, int(in_flight * 2) + link.queue_packets)


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def build_qdisc_commands(dev: str, link: LinkParams) -> List[List[str]]:
    """argv lists that (re)create the netem+tbf hierarchy on ``dev``."""
    netem = ["tc", "qdisc", "add", "dev", dev, "root", "handle", NETEM_HANDLE, "netem",
             "limit", str(netem_limit_packets(link))]
    if link.delay_ms > 0 or link.jitter_ms > 0:
        netem += ["delay", f"{_fmt(link.delay_ms)}ms"]
        if link.jitter_ms > 0:
            netem.append(f"{_fmt(link.jitter_ms)}ms")
    if link.loss_pct > 0:
        netem += ["loss", f"{_fmt(link.loss_pct)}%"]
    tbf = ["tc", "qdisc", "add", "dev", dev, "parent", TBF_PARENT, "handle", TBF_HANDLE, "tbf",
           "rate", f"{link.rate_kbit}kbit", "burst", str(link.burst_bytes), "limit", str(link.limit_bytes)]
    return [netem, tbf]


def build_reset_command(dev: str) -> List[str]:
    return ["tc", "qdisc", "del", "dev", dev, "root"]


def build_offload_command(dev: str) -> List[str]:
    return ["ethtool", "-K", dev, "tso", "off", "gso", "off", "gro", "off"]


def apply_link(run: Runner, dev: str, link: LinkParams, *, disable_offload: bool = True) -> None:
    """Configure ``dev`` according to ``link``; raises :class:`TopologyError` on failure."""
    if disable_offload:
        rc, out = run(build_offload_command(dev))
        if rc != 0:  # not fatal: tbf segments GSO packets itself
            logger.warning("could not disable offloads on %s (rc=%d): %s", dev, rc, out.strip())
    run(build_reset_command(dev))  # fails harmlessly if there is no root qdisc yet
    for argv in build_qdisc_commands(dev, link):
        rc, out = run(argv)
        if rc != 0:
            raise TopologyError(f"command failed (rc={rc}): {' '.join(argv)}\n{out.strip()}")
    logger.debug("shaped %s: %s", dev, link)


# ------------------------------------------------------------------- inspection
_SIZE_UNITS = {"": 1.0, "K": 1024.0, "M": 1024.0 ** 2, "G": 1024.0 ** 3}
_RATE_UNITS = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}
_TIME_UNITS_MS = {"us": 0.001, "ms": 1.0, "s": 1000.0}


@dataclass(frozen=True)
class QdiscState:
    """What ``tc qdisc show`` reports for one interface."""

    has_netem: bool = False
    has_tbf: bool = False
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_pct: float = 0.0
    rate_mbps: Optional[float] = None
    burst_bytes: Optional[float] = None
    limit_bytes: Optional[float] = None


def parse_qdisc_show(text: str) -> QdiscState:
    """Parse the output of ``tc qdisc show dev <dev>``."""
    state = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("qdisc netem"):
            state["has_netem"] = True
            m = re.search(r"delay\s+([\d.]+)(us|ms|s)(?:\s+([\d.]+)(us|ms|s))?", line)
            if m:
                state["delay_ms"] = float(m.group(1)) * _TIME_UNITS_MS[m.group(2)]
                if m.group(3):
                    state["jitter_ms"] = float(m.group(3)) * _TIME_UNITS_MS[m.group(4)]
            m = re.search(r"loss\s+([\d.]+)%", line)
            if m:
                state["loss_pct"] = float(m.group(1))
        elif line.startswith("qdisc tbf"):
            state["has_tbf"] = True
            m = re.search(r"rate\s+([\d.]+)([KMG]?)bit", line)
            if m:
                state["rate_mbps"] = float(m.group(1)) * _RATE_UNITS[m.group(2)] / 1e6
            m = re.search(r"burst\s+([\d.]+)([KMG]?)b\b", line)
            if m:
                state["burst_bytes"] = float(m.group(1)) * _SIZE_UNITS[m.group(2)]
            m = re.search(r"limit\s+([\d.]+)([KMG]?)b\b", line)
            if m:
                state["limit_bytes"] = float(m.group(1)) * _SIZE_UNITS[m.group(2)]
    return QdiscState(**state)


def check_link_state(state: QdiscState, expected: LinkParams) -> List[str]:
    """Compare a parsed qdisc state with ``expected``; returns human-readable problems."""
    problems: List[str] = []
    if not state.has_netem:
        problems.append("no netem qdisc found")
    if not state.has_tbf:
        problems.append("no tbf qdisc found")
    if state.rate_mbps is None:
        problems.append("tbf rate not reported")
    elif abs(state.rate_mbps - expected.bandwidth_mbps) > 0.01 * expected.bandwidth_mbps:
        problems.append(f"tbf rate {state.rate_mbps:.3f} Mbps != expected {expected.bandwidth_mbps} Mbps")
    if abs(state.delay_ms - expected.delay_ms) > max(0.5, 0.02 * expected.delay_ms):
        problems.append(f"netem delay {state.delay_ms} ms != expected {expected.delay_ms} ms")
    if abs(state.loss_pct - expected.loss_pct) > 0.1:
        problems.append(f"netem loss {state.loss_pct}% != expected {expected.loss_pct}%")
    return problems


def read_qdisc(run: Runner, dev: str) -> QdiscState:
    rc, out = run(["tc", "qdisc", "show", "dev", dev])
    if rc != 0:
        raise TopologyError(f"tc qdisc show dev {dev} failed (rc={rc}): {out.strip()}")
    return parse_qdisc_show(out)
