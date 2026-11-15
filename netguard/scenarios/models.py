"""Scenario definition models: a scenario is a list of traffic components.

A *component* describes one family of flows (e.g. "four paced TCP bulk
transfers" or "a UDP flood between two hosts") and the *pattern* that decides
how those flows are laid out over time. Components are turned into concrete,
timed :class:`netguard.traffic.events.TrafficEvent` objects by
:mod:`netguard.traffic.planner`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from netguard.config.environment import LinkParams
from netguard.exceptions import ConfigError
from netguard.scenarios.options import resolve_options
from netguard.traffic.types import (
    BANDWIDTH_TOOLS,
    DEFAULT_PROTOCOL,
    PATTERN_TOOLS,
    REQUEST_TOOLS,
    TOOL_PROTOCOLS,
    Pattern,
    Protocol,
    Tool,
)
from netguard.utils.validation import (
    REQUIRED,
    as_list,
    get_float,
    get_int,
    get_str,
    join_ctx,
    parse_enum,
    reject_unknown,
    require_mapping,
)

#: Host selectors: a group name or an explicit Mininet host name. Raw IP
#: addresses are deliberately not accepted, so traffic can only ever target
#: hosts of the emulated topology.
_SELECTOR_RE = re.compile(r"^(left|right|all|h[0-9]+)$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: Patterns that derive their rate from options instead of ``rate_mbps``.
_RATE_FROM_OPTIONS = {Pattern.RAMP, Pattern.RANDOM}

#: Patterns for which the top-level ``duration_s`` is meaningful.
_USES_DURATION = {Pattern.NORMAL, Pattern.FLASH_CROWD, Pattern.UDP_FLOOD}

_COMPONENT_KEYS = (
    "name", "pattern", "tool", "protocol", "src", "dst", "rate_mbps", "request_rate",
    "packet_size", "duration_s", "start_s", "clients", "connections", "burst_interval_s",
    "seed", "options",
)
_SCENARIO_KEYS = ("name", "description", "duration_s", "seed", "link_overrides", "components")


def _parse_selectors(value: Any, name: str) -> Tuple[str, ...]:
    selectors: List[str] = []
    for item in as_list(value, name):
        if not isinstance(item, str) or not _SELECTOR_RE.match(item.strip().lower()):
            raise ConfigError(
                f"{name}: invalid host selector {item!r}; use 'left', 'right', 'all' or a "
                "Mininet host name such as 'h1' (IP addresses are not allowed)"
            )
        selectors.append(item.strip().lower())
    return tuple(selectors)


@dataclass(frozen=True)
class TrafficComponent:
    """One validated component of a scenario (see module docstring)."""

    name: str
    pattern: Pattern
    tool: Tool
    protocol: Protocol
    src: Tuple[str, ...]
    dst: Tuple[str, ...]
    rate_mbps: Optional[float] = None  # per client, application payload
    request_rate: Optional[float] = None  # per client, requests/queries/packets per second
    packet_size: Optional[int] = None  # payload bytes
    duration_s: Optional[float] = None  # None: until the scenario ends
    start_s: float = 0.0
    clients: int = 1
    connections: int = 1  # parallel connections per client
    burst_interval_s: Optional[float] = None
    seed: Optional[int] = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Any, ctx: str, scenario_duration_s: float) -> "TrafficComponent":
        data = require_mapping(data, ctx)
        reject_unknown(data, _COMPONENT_KEYS, ctx)

        name = get_str(data, "name", ctx)
        if not _NAME_RE.match(name):
            raise ConfigError(f"{join_ctx(ctx, 'name')}: use letters, digits, '_' or '-', got {name!r}")
        pattern = parse_enum(Pattern, get_str(data, "pattern", ctx), join_ctx(ctx, "pattern"))
        tool = parse_enum(Tool, get_str(data, "tool", ctx, "iperf3"), join_ctx(ctx, "tool"))
        if tool not in PATTERN_TOOLS[pattern]:
            allowed = sorted(t.value for t in PATTERN_TOOLS[pattern])
            raise ConfigError(f"{ctx}: pattern {pattern.value} supports tools {allowed}, not {tool.value}")

        raw_proto = data.get("protocol")
        protocol = (
            parse_enum(Protocol, raw_proto, join_ctx(ctx, "protocol"))
            if raw_proto is not None
            else DEFAULT_PROTOCOL[tool]
        )
        if protocol not in TOOL_PROTOCOLS[tool]:
            allowed = sorted(p.value for p in TOOL_PROTOCOLS[tool])
            raise ConfigError(f"{join_ctx(ctx, 'protocol')}: tool {tool.value} supports {allowed}, got {protocol.value}")
        if tool in (Tool.HTTP, Tool.DNS, Tool.PING):
            protocol = DEFAULT_PROTOCOL[tool]  # normalise tcp->http, udp->dns
        if pattern is Pattern.UDP_FLOOD and protocol is not Protocol.UDP:
            raise ConfigError(f"{join_ctx(ctx, 'protocol')}: UDP_FLOOD requires protocol udp")
        if pattern is Pattern.FLASH_CROWD and tool is Tool.IPERF3 and protocol is not Protocol.TCP:
            raise ConfigError(f"{join_ctx(ctx, 'protocol')}: FLASH_CROWD requires TCP clients")

        src = _parse_selectors(data.get("src", "left"), join_ctx(ctx, "src"))
        dst = _parse_selectors(data.get("dst", "right"), join_ctx(ctx, "dst"))

        rate_mbps = get_float(data, "rate_mbps", ctx, None, minimum=0.0, exclusive_min=True)
        request_rate = get_float(data, "request_rate", ctx, None, minimum=0.0, exclusive_min=True)
        if pattern in _RATE_FROM_OPTIONS:
            if rate_mbps is not None or request_rate is not None:
                raise ConfigError(f"{ctx}: pattern {pattern.value} takes its rates from 'options'; remove rate_mbps/request_rate")
        elif tool in BANDWIDTH_TOOLS:
            if request_rate is not None:
                raise ConfigError(f"{join_ctx(ctx, 'request_rate')}: not applicable to tool {tool.value}; use rate_mbps")
            if rate_mbps is None and not (tool is Tool.IPERF3 and protocol is Protocol.TCP):
                raise ConfigError(f"{join_ctx(ctx, 'rate_mbps')}: required for tool {tool.value} ({protocol.value})")
        else:
            assert tool in REQUEST_TOOLS
            if rate_mbps is not None:
                raise ConfigError(f"{join_ctx(ctx, 'rate_mbps')}: not applicable to tool {tool.value}; use request_rate")
            if request_rate is None:
                raise ConfigError(f"{join_ctx(ctx, 'request_rate')}: required for tool {tool.value}")

        packet_size = get_int(data, "packet_size", ctx, None, minimum=1, maximum=65507)
        if packet_size is not None and tool not in (Tool.IPERF3, Tool.HPING3, Tool.PING):
            raise ConfigError(f"{join_ctx(ctx, 'packet_size')}: not applicable to tool {tool.value}")
        if packet_size is not None and tool in (Tool.IPERF3, Tool.HPING3) and protocol is Protocol.UDP and packet_size < 16:
            raise ConfigError(f"{join_ctx(ctx, 'packet_size')}: UDP payload must be at least 16 bytes")

        duration_s = get_float(data, "duration_s", ctx, None, minimum=0.0, exclusive_min=True)
        if duration_s is not None and pattern not in _USES_DURATION:
            raise ConfigError(
                f"{join_ctx(ctx, 'duration_s')}: not used by pattern {pattern.value} "
                "(it has its own timing options)"
            )
        start_s =get_float(data, "start_s", ctx, 0.0, minimum=0.0)
        if start_s >= scenario_duration_s:
            raise ConfigError(f"{join_ctx(ctx, 'start_s')}: {start_s} is not before the scenario end ({scenario_duration_s})")
        clients = get_int(data, "clients", ctx, 1, minimum=1)
        connections = get_int(data, "connections", ctx, 1, minimum=1)
        if connections > 1 and tool not in (Tool.IPERF3, Tool.HTTP):
            raise ConfigError(f"{join_ctx(ctx, 'connections')}: only supported for iperf3 and http")

        options = resolve_options(pattern, data.get("options"), join_ctx(ctx, "options"))

        burst_interval_s = get_float(data, "burst_interval_s", ctx, None, minimum=0.0, exclusive_min=True)
        if pattern is Pattern.BURST:
            if options["burst_count"] > 1:
                if burst_interval_s is None:
                    raise ConfigError(f"{join_ctx(ctx, 'burst_interval_s')}: required when options.burst_count > 1")
                if burst_interval_s < options["burst_duration_s"]:
                    raise ConfigError(f"{join_ctx(ctx, 'burst_interval_s')}: must be >= options.burst_duration_s")
        elif burst_interval_s is not None:
            raise ConfigError(f"{join_ctx(ctx, 'burst_interval_s')}: only valid for pattern BURST (PERIODIC uses options.period_s)")
        if pattern is Pattern.PERIODIC and options["on_duration_s"] > options["period_s"]:
            raise ConfigError(f"{join_ctx(ctx, 'options.on_duration_s')}: must be <= options.period_s")
        if pattern is Pattern.RAMP and not options["start_load"] <= options["onset_load"] <= options["max_load"]:
            raise ConfigError(f"{join_ctx(ctx, 'options')}: require start_load <= onset_load <= max_load")

        seed = get_int(data, "seed", ctx, None, minimum=0)

        return cls(
            name=name, pattern=pattern, tool=tool, protocol=protocol, src=src, dst=dst,
            rate_mbps=rate_mbps, request_rate=request_rate, packet_size=packet_size,
            duration_s=duration_s, start_s=start_s, clients=clients, connections=connections,
            burst_interval_s=burst_interval_s, seed=seed, options=options,
        )


@dataclass(frozen=True)
class ScenarioConfig:
    """A complete, validated scenario."""

    name: str
    description: str
    duration_s: float
    seed: int
    components: Tuple[TrafficComponent, ...]
    link_overrides: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Any, source: str = "scenario") -> "ScenarioConfig":
        data = require_mapping(data, source)
        reject_unknown(data, _SCENARIO_KEYS, source)

        name = get_str(data, "name", source)
        if not _NAME_RE.match(name):
            raise ConfigError(f"{source}.name: use letters, digits, '_' or '-', got {name!r}")
        description = get_str(data, "description", source, "")
        duration_s = get_float(data, "duration_s", source, REQUIRED, minimum=0.0, maximum=86400.0, exclusive_min=True)
        seed = get_int(data, "seed", source, 0, minimum=0)

        overrides = require_mapping(data.get("link_overrides") or {}, f"{source}.link_overrides")
        if overrides:  # validate keys/ranges now; merged with real defaults later
            LinkParams.from_mapping(overrides, f"{source}.link_overrides", base=LinkParams(bandwidth_mbps=1.0))

        raw_components = data.get("components")
        if not isinstance(raw_components, list) or not raw_components:
            raise ConfigError(f"{source}.components: expected a non-empty list")
        components = tuple(
            TrafficComponent.from_mapping(item, f"{source}.components[{i}]", duration_s)
            for i, item in enumerate(raw_components)
        )
        names = [c.name for c in components]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ConfigError(f"{source}.components: duplicate component name(s) {duplicates}")
        return cls(name, description or "", duration_s, seed, components, dict(overrides))

    def with_seed(self, seed: Optional[int]) -> "ScenarioConfig":
        """Return a copy using ``seed`` (no-op for ``None``)."""
        if seed is None:
            return self
        return ScenarioConfig(self.name, self.description, self.duration_s, seed, self.components, self.link_overrides)

    def with_duration(self, duration_s: Optional[float]) -> "ScenarioConfig":
        """Return a copy with a different total duration (components re-validated)."""
        if duration_s is None:
            return self
        raw = scenario_to_mapping(self)
        raw["duration_s"] = duration_s
        return ScenarioConfig.from_mapping(raw, self.name)


def scenario_to_mapping(scenario: ScenarioConfig) -> Dict[str, Any]:
    """Serialise a scenario back to the YAML mapping form (used for re-validation)."""
    components = []
    for c in scenario.components:
        item: Dict[str, Any] = {
            "name": c.name, "pattern": c.pattern.value, "tool": c.tool.value,
            "protocol": c.protocol.value, "src": list(c.src), "dst": list(c.dst),
            "start_s": c.start_s, "clients": c.clients, "connections": c.connections,
            "options": {k: (list(v) if isinstance(v, tuple) else v) for k, v in c.options.items()},
        }
        for key in ("rate_mbps", "request_rate", "packet_size", "duration_s", "burst_interval_s", "seed"):
            value = getattr(c, key)
            if value is not None:
                item[key] = value
        components.append(item)
    mapping: Dict[str, Any] = {
        "name": scenario.name, "duration_s": scenario.duration_s, "seed": scenario.seed,
        "link_overrides": dict(scenario.link_overrides), "components": components,
    }
    if scenario.description:
        mapping["description"] = scenario.description
    return mapping
