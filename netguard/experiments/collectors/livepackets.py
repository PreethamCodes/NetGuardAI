"""Second-by-second packet/flow statistics from a *live* TShark stream.

The offline pipeline (TShark pcap + NFStream) only finishes after an experiment. A live demo needs the same
signals while the traffic is running, so this collector reads ``tshark -l -T fields`` and keeps, per second of the
experiment timeline:

* packet counts per protocol (TCP, UDP, ICMP, DNS = UDP port 53, HTTP = the HTTP server's TCP port),
* ``nf_new_flows``  - bidirectional flows (5-tuple) whose first packet is in that second (same as the
  offline NFStream ``first_seen`` definition),
* ``nf_active_flows`` - flows whose first packet is at or before that second and whose last packet *seen so far* is
  at or after it. This is the offline NFStream definition (a flow is active for every second between its first and
  last packet). Because "last packet" needs a look-ahead, the live feeder reports a second only a few seconds after
  it ended (``LiveFeeder.delay_s``); a flow silent for longer than that delay is then counted as ended too early.
  ``netguard live`` compares live and offline values after the run and stores the difference in the metadata.
* ``syn_packets`` - TCP SYN / SYN-ACK packets seen in the second. Offline the SYNs of a flow are attributed to the
  second the flow started; the two differ only when a handshake straddles a second boundary.

Like :class:`TsharkCollector` it is bounded with ``-a duration:N`` because Ubuntu's AppArmor profile does not let
other processes signal tshark.
"""

from __future__ import annotations

import logging
import math
import signal
import subprocess
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from netguard.exceptions import NetGuardError
from netguard.experiments.collectors.base import Collector, Health

logger = logging.getLogger(__name__)

FIELDS = ("frame.time_epoch", "ip.src", "ip.dst", "ip.proto", "tcp.srcport", "tcp.dstport", "udp.srcport",
          "udp.dstport", "tcp.flags.syn")


def live_command(interface: str, snaplen: int, duration_s: float) -> List[str]:
    argv = ["tshark", "-i", interface, "-s", str(snaplen), "-f", "ip", "-n", "-l", "-T", "fields",
            "-E", "separator=,", "-E", "occurrence=f", "-a", f"duration:{max(1, int(math.ceil(duration_s)))}"]
    for f in FIELDS:
        argv += ["-e", f]
    return argv


FlowKey = Tuple[str, int, str, int, int]


class PacketAggregator:
    """Pure aggregation of parsed packets into per-second statistics (unit-tested without TShark)."""

    def __init__(self, anchor_wall: float, http_port: int) -> None:
        self.anchor = anchor_wall
        self.http_port = http_port
        self._lock = threading.Lock()
        self._counts: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._flows_first: Dict[FlowKey, int] = {}
        self._flows_last: Dict[FlowKey, int] = {}
        self.packets = 0

    @staticmethod
    def _port(value: str) -> int:
        return int(value) if value.strip().isdigit() else 0

    def add_line(self, line: str) -> bool:
        parts = line.rstrip("\n").split(",")
        if len(parts) < len(FIELDS):
            return False
        try:
            ts = float(parts[0])
        except ValueError:
            return False
        src, dst = parts[1], parts[2]
        proto = int(parts[3]) if parts[3].strip().isdigit() else 0
        tcp_s, tcp_d, udp_s, udp_d = (self._port(p) for p in parts[4:8])
        syn = parts[8].strip().lower() in ("1", "true")
        sport, dport = (tcp_s, tcp_d) if proto == 6 else (udp_s, udp_d) if proto == 17 else (0, 0)
        second = int(math.floor(ts - self.anchor))
        key: FlowKey = (src, sport, dst, dport, proto) if (src, sport) <= (dst, dport) else (dst, dport, src, sport, proto)
        with self._lock:
            self.packets += 1
            c = self._counts[second]
            c["pkts"] += 1
            if proto == 6:
                c["tcp"] += 1
                if http_hit(self.http_port, tcp_s, tcp_d):
                    c["http"] += 1
                if syn:
                    c["syn"] += 1
            elif proto == 17:
                c["udp"] += 1
                if udp_s == 53 or udp_d == 53:
                    c["dns"] += 1
            elif proto == 1:
                c["icmp"] += 1
            if key not in self._flows_first:
                self._flows_first[key] = second
                c["new_flows"] += 1
            if second > self._flows_last.get(key, second - 1):
                self._flows_last[key] = second
        return True

    def stats(self, second: int) -> Dict[str, float]:
        """Statistics of a (finished) second, named like the Phase 2 dataset columns."""
        with self._lock:
            c = self._counts.get(second, {})
            return {
                "tshark_pkts": float(c.get("pkts", 0)), "tshark_tcp_pkts": float(c.get("tcp", 0)),
                "tshark_udp_pkts": float(c.get("udp", 0)), "tshark_icmp_pkts": float(c.get("icmp", 0)),
                "tshark_dns_pkts": float(c.get("dns", 0)), "tshark_http_pkts": float(c.get("http", 0)),
                "nf_new_flows": float(c.get("new_flows", 0)),
                "nf_active_flows": float(sum(1 for k, first in self._flows_first.items()
                                             if first <= second <= self._flows_last.get(k, first))),
                "syn_packets": float(c.get("syn", 0)),
            }


def http_hit(http_port: int, sport: int, dport: int) -> bool:
    return sport == http_port or dport == http_port


class LivePacketCollector(Collector):
    name = "live_packets"

    def __init__(self, interface: str, anchor_wall: float, http_port: int, duration_s: float, snaplen: int = 96,
                 ready_timeout_s: float = 10.0) -> None:
        self.interface, self.snaplen, self.duration_s = interface, snaplen, duration_s
        self.aggregator = PacketAggregator(anchor_wall, http_port)
        self.ready_timeout_s = ready_timeout_s
        self._proc: Optional[subprocess.Popen] = None
        self._threads: List[threading.Thread] = []
        self._ready = threading.Event()
        self._stderr: List[str] = []
        self._started = False
        self._started_mono = 0.0

    def start(self) -> None:
        self._started_mono = time.monotonic()
        self._proc = subprocess.Popen(live_command(self.interface, self.snaplen, self.duration_s), stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        for target in (self._read_stdout, self._read_stderr):
            t = threading.Thread(target=target, name=f"collector-{self.name}", daemon=True)
            t.start()
            self._threads.append(t)
        deadline = time.monotonic() + self.ready_timeout_s
        while time.monotonic() < deadline:
            if self._ready.wait(0.1):
                time.sleep(0.5)
                if self._proc.poll() is None:
                    self._started = True
                    return
                break
            if self._proc.poll() is not None:
                break
        self.stop()
        raise NetGuardError(f"live tshark did not start on {self.interface}: {''.join(self._stderr)[-300:]}")

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self.aggregator.add_line(line)

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr.append(line)
            if "Capturing on" in line:
                self._ready.set()

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except PermissionError:
                logger.debug("tshark does not accept signals (AppArmor): waiting for its -a duration limit")
            remaining = self.duration_s - (time.monotonic() - self._started_mono)
            try:
                proc.wait(timeout=max(10.0, remaining + 10.0))
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except PermissionError:
                    logger.error("live tshark did not stop and cannot be killed (AppArmor)")
                proc.wait(timeout=10)
        for t in self._threads:
            t.join(timeout=5)

    def raw_samples(self) -> List[Dict[str, Any]]:
        return []

    def health(self) -> Health:
        errors = () if self.aggregator.packets > 0 else ("no packets were read from the live capture",)
        return Health(self.name, self._started, attempted=1, failed=1 if errors else 0, errors=errors)
