"""Typed, validated models for the topology, link and traffic-tool YAML files."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional, Tuple

from netguard.exceptions import ConfigError
from netguard.utils.validation import (
    REQUIRED,
    get_bool,
    get_float,
    get_int,
    get_str,
    reject_unknown,
    require_mapping,
)

logger = logging.getLogger(__name__)

#: Only these private blocks are accepted for the emulated network.
_RFC1918 = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))

MIN_HOSTS_PER_SIDE = 3
MAX_HOSTS_PER_SIDE = 4


# --------------------------------------------------------------------------- links
@dataclass(frozen=True)
class LinkParams:
    """Shaping parameters for one link direction (applied with tc netem + tbf)."""

    bandwidth_mbps: float
    delay_ms: float = 0.0
    loss_pct: float = 0.0
    jitter_ms: float = 0.0
    queue_packets: int = 50
    mtu: int = 1500

    _KEYS = ("bandwidth_mbps", "delay_ms", "loss_pct", "jitter_ms", "queue_packets", "mtu")

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any], ctx: str, base: Optional["LinkParams"] = None
    ) -> "LinkParams":
        """Build from a mapping; missing keys fall back to ``base`` if given."""
        data = require_mapping(data, ctx)
        reject_unknown(data, cls._KEYS, ctx)

        def dflt(name: str, fallback: Any) -> Any:
            return getattr(base, name) if base is not None else fallback

        return cls(
            bandwidth_mbps=get_float(
                data, "bandwidth_mbps", ctx, dflt("bandwidth_mbps", REQUIRED),
                minimum=0.0, maximum=10_000.0, exclusive_min=True,
            ),
            delay_ms=get_float(
                data, "delay_ms", ctx, dflt("delay_ms", 0.0), minimum=0.0, maximum=60_000.0
            ),
            loss_pct=get_float(
                data, "loss_pct", ctx, dflt("loss_pct", 0.0), minimum=0.0, maximum=100.0
            ),
            jitter_ms=get_float(
                data, "jitter_ms", ctx, dflt("jitter_ms", 0.0), minimum=0.0, maximum=60_000.0
            ),
            queue_packets=get_int(
                data, "queue_packets", ctx, dflt("queue_packets", 50), minimum=1, maximum=1_000_000
            ),
            mtu=get_int(data, "mtu", ctx, dflt("mtu", 1500), minimum=576, maximum=9000),
        )

    @property
    def rate_kbit(self) -> int:
        """Rate as an integer number of kbit/s (the precision tc is given)."""
        return max(1, int(round(self.bandwidth_mbps * 1000)))

    @property
    def burst_bytes(self) -> int:
        """tbf burst: ~10 ms worth of data, never below two MTUs."""
        return max(2 * self.mtu, int(self.rate_kbit * 1000 / 8 * 0.01))

    @property
    def limit_bytes(self) -> int:
        """tbf queue size in bytes, derived from ``queue_packets``; at least one burst."""
        return max(self.queue_packets * self.mtu, self.burst_bytes)


@dataclass(frozen=True)
class LinksConfig:
    """Parameters of the bottleneck (s1<->s2) link and of the host access links."""

    bottleneck: LinkParams
    access: LinkParams
    disable_offload: bool = True

    def __post_init__(self) -> None:
        if self.access.bandwidth_mbps <= self.bottleneck.bandwidth_mbps:
            raise ConfigError(
                "links: access bandwidth "
                f"({self.access.bandwidth_mbps} Mbps) must be greater than the bottleneck "
                f"bandwidth ({self.bottleneck.bandwidth_mbps} Mbps) so that the inter-switch "
                "link remains the bottleneck"
            )
        if self.access.bandwidth_mbps < 2 * self.bottleneck.bandwidth_mbps:
            logger.warning(
                "Access bandwidth (%.1f Mbps) is less than 2x the bottleneck (%.1f Mbps); "
                "access links may start to influence measurements.",
                self.access.bandwidth_mbps,
                self.bottleneck.bandwidth_mbps,
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LinksConfig":
        data = require_mapping(data, "links")
        reject_unknown(data, ("bottleneck", "access", "disable_offload"), "links")
        if "bottleneck" not in data or "access" not in data:
            raise ConfigError("links: both 'bottleneck' and 'access' sections are required")
        return cls(
            bottleneck=LinkParams.from_mapping(data["bottleneck"], "links.bottleneck"),
            access=LinkParams.from_mapping(data["access"], "links.access"),
            disable_offload=get_bool(data, "disable_offload", "links", True),
        )

    def with_bottleneck_overrides(self, overrides: Mapping[str, Any]) -> "LinksConfig":
        """Return a copy with selected bottleneck fields replaced (re-validated)."""
        if not overrides:
            return self
        new = LinkParams.from_mapping(overrides, "link_overrides", base=self.bottleneck)
        return replace(self, bottleneck=new)


# ----------------------------------------------------------------------- topology
@dataclass(frozen=True)
class TopologyConfig:
    """Structure of the dumbbell: switch names, host counts and addressing."""

    hosts_per_side: int = 4
    subnet: str = "10.0.0.0/24"
    left_switch: str = "s1"
    right_switch: str = "s2"
    fail_mode: str = "standalone"

    def __post_init__(self) -> None:
        if not MIN_HOSTS_PER_SIDE <= self.hosts_per_side <= MAX_HOSTS_PER_SIDE:
            raise ConfigError(
                f"topology.hosts_per_side: must be between {MIN_HOSTS_PER_SIDE} and "
                f"{MAX_HOSTS_PER_SIDE}, got {self.hosts_per_side}"
            )
        try:
            network = ipaddress.ip_network(self.subnet, strict=True)
        except ValueError as exc:
            raise ConfigError(f"topology.subnet: invalid network {self.subnet!r}: {exc}") from exc
        if network.version != 4:
            raise ConfigError("topology.subnet: only IPv4 is supported")
        if not any(network.subnet_of(block) for block in _RFC1918):
            raise ConfigError(
                f"topology.subnet: {self.subnet} is not a private (RFC 1918) range; "
                "the simulation must stay isolated"
            )
        if network.num_addresses - 2 < 2 * self.hosts_per_side:
            raise ConfigError(f"topology.subnet: {self.subnet} is too small for the hosts")
        if self.left_switch == self.right_switch:
            raise ConfigError("topology: left_switch and right_switch must differ")
        if self.fail_mode not in ("standalone", "secure"):
            raise ConfigError("topology.fail_mode: must be 'standalone' or 'secure'")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TopologyConfig":
        data = require_mapping(data, "topology")
        reject_unknown(
            data, ("hosts_per_side", "subnet", "left_switch", "right_switch", "fail_mode"), "topology"
        )
        return cls(
            hosts_per_side=get_int(data, "hosts_per_side", "topology", 4),
            subnet=get_str(data, "subnet", "topology", "10.0.0.0/24"),
            left_switch=get_str(data, "left_switch", "topology", "s1"),
            right_switch=get_str(data, "right_switch", "topology", "s2"),
            fail_mode=get_str(data, "fail_mode", "topology", "standalone"),
        )

    @property
    def network(self) -> "ipaddress.IPv4Network":
        return ipaddress.ip_network(self.subnet)

    @property
    def left_hosts(self) -> Tuple[str, ...]:
        return tuple(f"h{i}" for i in range(1, self.hosts_per_side + 1))

    @property
    def right_hosts(self) -> Tuple[str, ...]:
        n = self.hosts_per_side
        return tuple(f"h{i}" for i in range(n + 1, 2 * n + 1))

    @property
    def all_hosts(self) -> Tuple[str, ...]:
        return self.left_hosts + self.right_hosts

    def host_ip(self, host: str) -> str:
        """IPv4 address (without prefix) of ``host``: ``h<i>`` -> ``<network>.<i>``."""
        if host not in self.all_hosts:
            raise ConfigError(f"unknown host {host!r}; topology hosts are {list(self.all_hosts)}")
        index = int(host[1:])
        return str(self.network.network_address + index)

    def host_cidr(self, host: str) -> str:
        return f"{self.host_ip(host)}/{self.network.prefixlen}"


# ---------------------------------------------------------------- traffic defaults
@dataclass(frozen=True)
class Iperf3Settings:
    base_port: int = 5201
    connect_timeout_ms: int = 5000


@dataclass(frozen=True)
class HttpSettings:
    port: int = 8000
    payload_kb: int = 64
    timeout_s: float = 5.0


@dataclass(frozen=True)
class DnsSettings:
    port: int = 53
    domains: Tuple[str, ...] = ("example.test", "mail.example.test", "www.example.test")
    timeout_s: float = 1.0


@dataclass(frozen=True)
class PingSettings:
    timeout_s: int = 1


@dataclass(frozen=True)
class Hping3Settings:
    dst_port: int = 5001


@dataclass(frozen=True)
class ExecutorSettings:
    server_start_lead_s: float = 0.5
    grace_s: float = 10.0


@dataclass(frozen=True)
class TrafficLimits:
    """Safety caps that protect the VM CPU and keep experiments sane."""

    max_rate_mbps: float = 100.0
    max_request_rate: float = 500.0
    max_clients: int = 64
    max_connections: int = 32
    max_events: int = 2000
    max_packet_size: int = 1472
    max_hping3_pps: int = 20000


@dataclass(frozen=True)
class TrafficDefaults:
    """Tool-level settings from ``traffic.yaml`` (not scenario-specific)."""

    iperf3: Iperf3Settings = field(default_factory=Iperf3Settings)
    http: HttpSettings = field(default_factory=HttpSettings)
    dns: DnsSettings = field(default_factory=DnsSettings)
    ping: PingSettings = field(default_factory=PingSettings)
    hping3: Hping3Settings = field(default_factory=Hping3Settings)
    executor: ExecutorSettings = field(default_factory=ExecutorSettings)
    limits: TrafficLimits = field(default_factory=TrafficLimits)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TrafficDefaults":
        data = require_mapping(data, "traffic")
        reject_unknown(
            data, ("iperf3", "http", "dns", "ping", "hping3", "executor", "limits"), "traffic"
        )

        def section(name: str) -> Mapping[str, Any]:
            return require_mapping(data.get(name) or {}, f"traffic.{name}")

        s = section("iperf3")
        reject_unknown(s, ("base_port", "connect_timeout_ms"), "traffic.iperf3")
        iperf3 = Iperf3Settings(
            base_port=get_int(s, "base_port", "traffic.iperf3", 5201, minimum=1024, maximum=60000),
            connect_timeout_ms=get_int(
                s, "connect_timeout_ms", "traffic.iperf3", 5000, minimum=100, maximum=60000
            ),
        )

        s = section("http")
        reject_unknown(s, ("port", "payload_kb", "timeout_s"), "traffic.http")
        http = HttpSettings(
            port=get_int(s, "port", "traffic.http", 8000, minimum=1, maximum=65535),
            payload_kb=get_int(s, "payload_kb", "traffic.http", 64, minimum=1, maximum=10240),
            timeout_s=get_float(
                s, "timeout_s", "traffic.http", 5.0, minimum=0.0, maximum=300.0, exclusive_min=True
            ),
        )

        s = section("dns")
        reject_unknown(s, ("port", "domains", "timeout_s"), "traffic.dns")
        domains = s.get("domains", DnsSettings().domains)
        if (
            not isinstance(domains, (list, tuple))
            or not domains
            or not all(isinstance(x, str) and x.strip() for x in domains)
        ):
            raise ConfigError("traffic.dns.domains: expected a non-empty list of domain names")
        dns = DnsSettings(
            port=get_int(s, "port", "traffic.dns", 53, minimum=1, maximum=65535),
            domains=tuple(x.strip() for x in domains),
            timeout_s=get_float(
                s, "timeout_s", "traffic.dns", 1.0, minimum=0.0, maximum=60.0, exclusive_min=True
            ),
        )

        s = section("ping")
        reject_unknown(s, ("timeout_s",), "traffic.ping")
        ping = PingSettings(timeout_s=get_int(s, "timeout_s", "traffic.ping", 1, minimum=1, maximum=60))

        s = section("hping3")
        reject_unknown(s, ("dst_port",), "traffic.hping3")
        hping3 = Hping3Settings(
            dst_port=get_int(s, "dst_port", "traffic.hping3", 5001, minimum=1, maximum=65535)
        )

        s = section("executor")
        reject_unknown(s, ("server_start_lead_s", "grace_s"), "traffic.executor")
        executor = ExecutorSettings(
            server_start_lead_s=get_float(
                s, "server_start_lead_s", "traffic.executor", 0.5, minimum=0.0, maximum=30.0
            ),
            grace_s=get_float(s, "grace_s", "traffic.executor", 10.0, minimum=0.0, maximum=600.0),
        )

        s = section("limits")
        reject_unknown(
            s,
            ("max_rate_mbps", "max_request_rate", "max_clients", "max_connections",
             "max_events", "max_packet_size", "max_hping3_pps"),
            "traffic.limits",
        )
        d = TrafficLimits()
        limits = TrafficLimits(
            max_rate_mbps=get_float(
                s, "max_rate_mbps", "traffic.limits", d.max_rate_mbps, minimum=0.0, exclusive_min=True
            ),
            max_request_rate=get_float(
                s, "max_request_rate", "traffic.limits", d.max_request_rate,
                minimum=0.0, exclusive_min=True,
            ),
            max_clients=get_int(s, "max_clients", "traffic.limits", d.max_clients, minimum=1),
            max_connections=get_int(
                s, "max_connections", "traffic.limits", d.max_connections, minimum=1
            ),
            max_events=get_int(s, "max_events", "traffic.limits", d.max_events, minimum=1),
            max_packet_size=get_int(
                s, "max_packet_size", "traffic.limits", d.max_packet_size, minimum=16, maximum=65507
            ),
            max_hping3_pps=get_int(
                s, "max_hping3_pps", "traffic.limits", d.max_hping3_pps, minimum=1
            ),
        )
        return cls(iperf3, http, dns, ping, hping3, executor, limits)


@dataclass(frozen=True)
class Environment:
    """Everything the simulation needs apart from the scenario itself."""

    topology: TopologyConfig
    links: LinksConfig
    traffic: TrafficDefaults

    def with_link_overrides(self, overrides: Mapping[str, Any]) -> "Environment":
        return replace(self, links=self.links.with_bottleneck_overrides(overrides))

    def describe(self) -> Dict[str, Any]:
        """Plain-dict summary for logging."""
        return {
            "hosts_per_side": self.topology.hosts_per_side,
            "subnet": self.topology.subnet,
            "bottleneck": self.links.bottleneck.__dict__,
            "access": self.links.access.__dict__,
        }
