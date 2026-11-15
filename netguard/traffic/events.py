"""Concrete, timed traffic events and the plan that holds them."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from netguard.config.environment import TopologyConfig, TrafficLimits
from netguard.exceptions import TrafficError
from netguard.traffic.types import BANDWIDTH_TOOLS, REQUEST_TOOLS, TOOL_PROTOCOLS, Protocol, Tool

#: Events shorter than this are dropped by the planner.
MIN_EVENT_S = 1.0

#: UDP payload size assumed for hping3 when a scenario does not give one.
DEFAULT_HPING3_PAYLOAD = 1000


@dataclass(frozen=True)
class TrafficEvent:
    """One traffic generator invocation: who talks to whom, when, how much."""

    event_id: str
    component: str
    pattern: str
    tool: Tool
    protocol: Protocol
    src: str
    dst: str
    start_s: float  # offset from scenario start
    duration_s: float
    rate_mbps: Optional[float] = None  # total for this event (all parallel connections)
    request_rate: Optional[float] = None  # requests / queries / packets per second
    packet_size: Optional[int] = None  # payload bytes
    parallel: int = 1  # parallel TCP connections / worker threads
    port: Optional[int] = None  # iperf3 server port (assigned by the planner)
    seed: int = 0  # for the HTTP / DNS workers

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["tool"] = self.tool.value
        data["protocol"] = self.protocol.value
        return data


def validate_event(event: TrafficEvent, limits: TrafficLimits, topology: TopologyConfig) -> None:
    """Check a single event against tool rules, safety limits and the topology.

    Raises :class:`TrafficError` describing the first violation found.
    """
    eid = event.event_id or "<event>"

    def fail(msg: str) -> None:
        raise TrafficError(f"{eid}: {msg}")

    if event.src not in topology.all_hosts:
        fail(f"unknown source host {event.src!r}")
    if event.dst not in topology.all_hosts:
        fail(f"unknown destination host {event.dst!r}")
    if event.src == event.dst:
        fail("source and destination must differ")
    if event.protocol not in TOOL_PROTOCOLS[event.tool]:
        fail(f"tool {event.tool.value} cannot generate protocol {event.protocol.value}")
    if event.start_s < 0:
        fail(f"start_s must be >= 0, got {event.start_s}")
    if event.duration_s <= 0:
        fail(f"duration_s must be > 0, got {event.duration_s}")
    if event.parallel < 1 or event.parallel > limits.max_connections:
        fail(f"parallel connections must be in 1..{limits.max_connections}, got {event.parallel}")

    if event.tool in BANDWIDTH_TOOLS:
        if event.request_rate is not None:
            fail(f"tool {event.tool.value} is configured with rate_mbps, not request_rate")
        if event.rate_mbps is None:
            if event.tool is Tool.HPING3 or event.protocol is Protocol.UDP:
                fail("a target rate_mbps is required for UDP traffic (no unbounded UDP floods)")
        elif not 0 < event.rate_mbps <= limits.max_rate_mbps:
            fail(f"rate_mbps must be in (0, {limits.max_rate_mbps}], got {event.rate_mbps}")
    else:
        assert event.tool in REQUEST_TOOLS
        if event.rate_mbps is not None:
            fail(f"tool {event.tool.value} is configured with request_rate, not rate_mbps")
        if event.request_rate is None or not 0 < event.request_rate <= limits.max_request_rate:
            fail(f"request_rate must be in (0, {limits.max_request_rate}], got {event.request_rate}")

    if event.packet_size is not None:
        if not 16 <= event.packet_size <= limits.max_packet_size:
            fail(f"packet_size must be in 16..{limits.max_packet_size}, got {event.packet_size}")
    if event.tool is Tool.HPING3 and event.rate_mbps is not None:
        pps = event.rate_mbps * 1e6 / 8 / (event.packet_size or DEFAULT_HPING3_PAYLOAD)
        if pps > limits.max_hping3_pps:
            fail(f"hping3 would need {pps:.0f} pps, above the limit of {limits.max_hping3_pps}")


@dataclass(frozen=True)
class TrafficPlan:
    """The full, deterministic schedule of a scenario."""

    scenario: str
    seed: int
    duration_s: float
    events: Tuple[TrafficEvent, ...]

    def hosts_used(self) -> List[str]:
        return sorted({e.src for e in self.events} | {e.dst for e in self.events})

    def tools_used(self) -> List[Tool]:
        return sorted({e.tool for e in self.events}, key=lambda t: t.value)

    def offered_load_mbps(self, t: float) -> float:
        """Sum of ``rate_mbps`` of all bandwidth-tool events active at time ``t``.

        Unpaced TCP events (no rate) are not counted; this is the *planned*
        offered load, not a measurement.
        """
        return sum(e.rate_mbps or 0.0 for e in self.events if e.start_s <= t < e.end_s)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario": self.scenario,
            "seed": self.seed,
            "duration_s": self.duration_s,
            "events": [e.to_dict() for e in self.events],
        }
