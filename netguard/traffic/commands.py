"""Pure functions that turn a :class:`TrafficEvent` into a command line (argv).

Nothing in this module executes anything, so every builder is unit-testable
without Mininet. Rates are *application payload* rates: for UDP tools the
packets-per-second figure is ``rate / (8 * payload)``.

Safety: the builders never emit ``--flood``, ``--rand-source``, ``--spoof``
or raw destination addresses chosen by the caller; the destination is always
the IP of a Mininet host resolved from the topology configuration.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from netguard.config.environment import TrafficDefaults
from netguard.exceptions import TrafficError
from netguard.traffic.events import DEFAULT_HPING3_PAYLOAD, TrafficEvent, TrafficPlan
from netguard.traffic.types import Protocol, Tool

#: Repository root, exported to worker processes as PYTHONPATH so that
#: ``python -m netguard.traffic.workers.*`` resolves inside Mininet hosts.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

HTTP_PAYLOAD_PATH = "/payload"


def _seconds(duration_s: float) -> int:
    """Whole seconds for tools that only accept integers (iperf3 -t)."""
    return max(1, int(round(duration_s)))


def _num(value: float) -> str:
    """Compact decimal formatting for argv values."""
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _worker(python: str, module: str, root: Path) -> List[str]:
    return ["env", f"PYTHONPATH={root}", python, "-m", f"netguard.traffic.workers.{module}"]


# ----------------------------------------------------------------------- iperf3
def iperf3_server_command(port: int) -> List[str]:
    """One-shot iperf3 server (exits after serving a single test)."""
    return ["iperf3", "-s", "-p", str(port), "-1"]


def iperf3_client_command(event: TrafficEvent, dst_ip: str, defaults: TrafficDefaults) -> List[str]:
    """iperf3 client for a TCP or UDP event. ``-b`` is per stream."""
    if event.port is None:
        raise TrafficError(f"{event.event_id}: iperf3 events need a server port")
    cmd = [
        "iperf3", "-c", dst_ip, "-p", str(event.port), "-t", str(_seconds(event.duration_s)),
        "-J", "--connect-timeout", str(defaults.iperf3.connect_timeout_ms),
    ]
    if event.protocol is Protocol.UDP:
        cmd.append("-u")
    if event.rate_mbps is not None:
        per_stream_kbit = max(1, int(round(event.rate_mbps * 1000 / event.parallel)))
        cmd += ["-b", f"{per_stream_kbit}K"]
    if event.packet_size is not None:
        cmd += ["-l", str(event.packet_size)]
    if event.parallel > 1:
        cmd += ["-P", str(event.parallel)]
    return cmd


# ------------------------------------------------------------------------- ping
def ping_command(event: TrafficEvent, dst_ip: str, defaults: TrafficDefaults) -> List[str]:
    """ICMP echo probes at ``request_rate`` packets per second."""
    assert event.request_rate is not None
    interval = 1.0 / event.request_rate
    count = max(1, int(event.duration_s * event.request_rate))
    cmd = ["ping", "-n", "-q", "-c", str(count), "-i", _num(interval), "-W", str(defaults.ping.timeout_s)]
    if event.packet_size is not None:
        cmd += ["-s", str(event.packet_size)]
    cmd.append(dst_ip)
    return cmd


# ----------------------------------------------------------------------- hping3
def hping3_command(event: TrafficEvent, dst_ip: str, defaults: TrafficDefaults) -> List[str]:
    """Rate-controlled UDP stream. Always bounded by ``-c`` and ``-i``."""
    assert event.rate_mbps is not None
    payload = event.packet_size or DEFAULT_HPING3_PAYLOAD
    pps = event.rate_mbps * 1e6 / 8 / payload
    interval_us = max(1, int(round(1e6 / pps)))
    count = max(1, int(math.ceil(pps * event.duration_s)))
    return [
        "hping3", "--udp", "-p", str(defaults.hping3.dst_port), "-d", str(payload),
        "-i", f"u{interval_us}", "-c", str(count), "-q", dst_ip,
    ]


# ------------------------------------------------------------------ python workers
def http_server_command(bind_ip: str, defaults: TrafficDefaults, python: str = "python3", root: Path = PROJECT_ROOT) -> List[str]:
    return _worker(python, "http_server", root) + [
        "--bind", bind_ip, "--port", str(defaults.http.port), "--payload-kb", str(defaults.http.payload_kb),
    ]


def http_client_command(event: TrafficEvent, dst_ip: str, defaults: TrafficDefaults, python: str = "python3", root: Path = PROJECT_ROOT) -> List[str]:
    assert event.request_rate is not None
    url = f"http://{dst_ip}:{defaults.http.port}{HTTP_PAYLOAD_PATH}"
    return _worker(python, "http_client", root) + [
        "--url", url, "--rate", _num(event.request_rate), "--duration", _num(event.duration_s),
        "--clients", str(event.parallel), "--seed", str(event.seed),
        "--timeout", _num(defaults.http.timeout_s),
    ]


def dns_server_command(bind_ip: str, defaults: TrafficDefaults, python: str = "python3", root: Path = PROJECT_ROOT) -> List[str]:
    return _worker(python, "dns_server", root) + ["--bind", bind_ip, "--port", str(defaults.dns.port)]


def dns_client_command(event: TrafficEvent, dst_ip: str, defaults: TrafficDefaults, python: str = "python3", root: Path = PROJECT_ROOT) -> List[str]:
    assert event.request_rate is not None
    return _worker(python, "dns_client", root) + [
        "--server", dst_ip, "--port", str(defaults.dns.port),
        "--domains", ",".join(defaults.dns.domains),
        "--rate", _num(event.request_rate), "--duration", _num(event.duration_s),
        "--clients", str(event.parallel), "--seed", str(event.seed),
        "--timeout", _num(defaults.dns.timeout_s),
    ]


# ---------------------------------------------------------------------- dispatch
def build_client_command(
    event: TrafficEvent, dst_ip: str, defaults: TrafficDefaults,
    python: str = "python3", root: Path = PROJECT_ROOT,
) -> List[str]:
    """Return the argv that realises ``event`` on its source host."""
    if event.tool is Tool.IPERF3:
        return iperf3_client_command(event, dst_ip, defaults)
    if event.tool is Tool.PING:
        return ping_command(event, dst_ip, defaults)
    if event.tool is Tool.HPING3:
        return hping3_command(event, dst_ip, defaults)
    if event.tool is Tool.HTTP:
        return http_client_command(event, dst_ip, defaults, python, root)
    if event.tool is Tool.DNS:
        return dns_client_command(event, dst_ip, defaults, python, root)
    raise TrafficError(f"{event.event_id}: unsupported tool {event.tool}")  # pragma: no cover


@dataclass(frozen=True)
class ServerSpec:
    """A long-running server that must exist on ``host`` before clients start."""

    kind: str  # "http" | "dns"
    host: str


def required_servers(plan: TrafficPlan) -> List[ServerSpec]:
    """HTTP/DNS servers needed by ``plan`` (iperf3 servers are per event)."""
    seen: Set[Tuple[str, str]] = set()
    specs: List[ServerSpec] = []
    for e in plan.events:
        if e.tool in (Tool.HTTP, Tool.DNS) and (e.tool.value, e.dst) not in seen:
            seen.add((e.tool.value, e.dst))
            specs.append(ServerSpec(e.tool.value, e.dst))
    return specs


def build_server_command(
    spec: ServerSpec, bind_ip: str, defaults: TrafficDefaults,
    python: str = "python3", root: Path = PROJECT_ROOT,
) -> List[str]:
    if spec.kind == "http":
        return http_server_command(bind_ip, defaults, python, root)
    if spec.kind == "dns":
        return dns_server_command(bind_ip, defaults, python, root)
    raise TrafficError(f"unknown server kind {spec.kind!r}")


# ------------------------------------------------------------------ iperf3 output
@dataclass(frozen=True)
class Iperf3Result:
    """Subset of the iperf3 ``-J`` report that Phase 1 verifies."""

    protocol: str
    bits_per_second: float
    lost_percent: Optional[float] = None  # UDP
    jitter_ms: Optional[float] = None  # UDP
    retransmits: Optional[int] = None  # TCP

    @property
    def mbps(self) -> float:
        return self.bits_per_second / 1e6


def parse_iperf3_json(text: str) -> Iperf3Result:
    """Parse the client-side JSON report of ``iperf3 -J``.

    Raises :class:`TrafficError` if the report is an error or incomplete.
    """
    try:
        report: Dict[str, Any] = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise TrafficError(f"iperf3 output is not valid JSON: {exc}") from exc
    if "error" in report:
        raise TrafficError(f"iperf3 reported an error: {report['error']}")
    end = report.get("end")
    if not isinstance(end, dict):
        raise TrafficError("iperf3 JSON has no 'end' section")
    protocol = str(report.get("start", {}).get("test_start", {}).get("protocol", "")).lower()
    is_udp = protocol == "udp" or (protocol == "" and "sum" in end and "sum_received" not in end)
    if is_udp and "sum" in end:
        s = end["sum"]
        return Iperf3Result(
            protocol="udp",
            bits_per_second=float(s["bits_per_second"]),
            lost_percent=s.get("lost_percent"),
            jitter_ms=s.get("jitter_ms"),
        )
    if "sum_received" in end:  # TCP: the receiver-side figure is the delivered rate
        return Iperf3Result(
            protocol="tcp",
            bits_per_second=float(end["sum_received"]["bits_per_second"]),
            retransmits=end.get("sum_sent", {}).get("retransmits"),
        )
    raise TrafficError("iperf3 JSON has neither a UDP 'sum' nor a TCP 'sum_received' section")
