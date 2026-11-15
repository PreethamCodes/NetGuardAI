"""Enumerations shared by the scenario definitions and the traffic generators."""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class Tool(str, Enum):
    """Traffic generation tool used to realise an event."""

    IPERF3 = "iperf3"
    HPING3 = "hping3"
    PING = "ping"
    HTTP = "http"  # Python HTTP client/server workers
    DNS = "dns"  # Scapy-crafted DNS queries against a Python responder


class Protocol(str, Enum):
    """Traffic protocol label."""

    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    HTTP = "http"
    DNS = "dns"


class Pattern(str, Enum):
    """Traffic pattern (temporal shape of the offered load)."""

    NORMAL = "NORMAL"
    BURST = "BURST"
    PERIODIC = "PERIODIC"
    RANDOM = "RANDOM"
    RAMP = "RAMP"
    FLASH_CROWD = "FLASH_CROWD"
    UDP_FLOOD = "UDP_FLOOD"


#: Protocols each tool can generate.
TOOL_PROTOCOLS: Dict[Tool, FrozenSet[Protocol]] = {
    Tool.IPERF3: frozenset({Protocol.TCP, Protocol.UDP}),
    Tool.HPING3: frozenset({Protocol.UDP}),
    Tool.PING: frozenset({Protocol.ICMP}),
    Tool.HTTP: frozenset({Protocol.HTTP, Protocol.TCP}),
    Tool.DNS: frozenset({Protocol.DNS, Protocol.UDP}),
}

#: Protocol assumed when a scenario does not state one.
DEFAULT_PROTOCOL: Dict[Tool, Protocol] = {
    Tool.IPERF3: Protocol.TCP,
    Tool.HPING3: Protocol.UDP,
    Tool.PING: Protocol.ICMP,
    Tool.HTTP: Protocol.HTTP,
    Tool.DNS: Protocol.DNS,
}

#: Tools whose rate is expressed in Mbit/s of application payload.
BANDWIDTH_TOOLS: FrozenSet[Tool] = frozenset({Tool.IPERF3, Tool.HPING3})

#: Tools whose rate is expressed in requests (or packets, or queries) per second.
REQUEST_TOOLS: FrozenSet[Tool] = frozenset({Tool.HTTP, Tool.DNS, Tool.PING})

#: Tools that may be used by each pattern.
PATTERN_TOOLS: Dict[Pattern, FrozenSet[Tool]] = {
    Pattern.NORMAL: frozenset(Tool),
    Pattern.BURST: frozenset({Tool.IPERF3, Tool.HPING3}),
    Pattern.PERIODIC: frozenset({Tool.IPERF3, Tool.HPING3}),
    Pattern.RANDOM: frozenset({Tool.IPERF3}),
    Pattern.RAMP: frozenset({Tool.IPERF3}),
    Pattern.FLASH_CROWD: frozenset({Tool.IPERF3, Tool.HTTP}),
    Pattern.UDP_FLOOD: frozenset({Tool.IPERF3, Tool.HPING3}),
}
