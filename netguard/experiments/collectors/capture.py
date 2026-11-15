"""Packet capture and analysis.

Responsibilities are split so that nothing is done twice:

* **TShark** captures the packets (header-only, ``snaplen`` 96 B) on the bottleneck
  port and does the *packet / protocol level* analysis: per-second packet and byte
  counts per protocol (TCP, UDP, ICMP, DNS, HTTP) and the protocol hierarchy.
* **NFStream** (:mod:`netguard.experiments.collectors.flows`) reads the same pcap and
  does the *flow level* analysis. It never re-implements protocol counting.

The capture runs in the root namespace on ``s1-eth5`` (or ``s2-eth5``); that port
sees every packet crossing the bottleneck in both directions exactly once. HTTP is
recognised by the configured server port (the 96-byte snap length does not hold
the request line), DNS by UDP port 53.
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from netguard.exceptions import NetGuardError
from netguard.experiments.collectors.base import Collector, Health

logger = logging.getLogger(__name__)

_FIELDS = ("frame.time_epoch", "frame.len", "ip.proto", "tcp.srcport", "tcp.dstport",
           "udp.srcport", "udp.dstport", "dns.flags.response", "dns.flags.rcode")


def tshark_capture_command(interface: str, pcap: Path, snaplen: int, duration_s: Optional[float] = None) -> List[str]:
    argv = ["tshark", "-i", interface, "-s", str(snaplen), "-f", "ip", "-F", "pcap", "-w", str(pcap), "-q", "-n"]
    if duration_s is not None:
        argv += ["-a", f"duration:{max(1, int(math.ceil(duration_s)))}"]
    return argv


class TsharkCollector(Collector):
    """Runs ``tshark -w`` for the whole experiment.

    Ubuntu's AppArmor profile for tshark does not accept signals from other processes
    (``kill`` fails with EACCES), so the capture is bounded with ``-a duration:N`` and
    ends - and flushes the pcap - by itself. :meth:`stop` signals it when allowed
    (faster) and otherwise waits for that deadline.
    """

    name = "tshark"

    def __init__(self, interface: str, pcap: Path, snaplen: int = 96, ready_timeout_s: float = 10.0,
                 settle_s: float = 0.7, duration_s: Optional[float] = None):
        self.interface, self.pcap, self.snaplen = interface, pcap, snaplen
        self.settle_s = settle_s
        self.duration_s = duration_s
        self._started_mono = 0.0
        self.ready_timeout_s = ready_timeout_s
        self._proc: Optional[subprocess.Popen] = None
        self._stderr: List[str] = []
        self._ready = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self.packets_captured: Optional[int] = None
        self.packets_dropped: Optional[int] = None
        self._started = False

    def start(self) -> None:
        self.pcap.parent.mkdir(parents=True, exist_ok=True)
        self._started_mono = time.monotonic()
        self._proc = subprocess.Popen(tshark_capture_command(self.interface, self.pcap, self.snaplen, self.duration_s),
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      text=True)
        self._reader = threading.Thread(target=self._read_stderr, name="collector-tshark", daemon=True)
        self._reader.start()
        deadline = time.monotonic() + self.ready_timeout_s
        while time.monotonic() < deadline:
            if self._ready.wait(0.1):
                # "Capturing on" is printed before dumpcap opens the output file: make sure it survived that
                time.sleep(self.settle_s)
                if self._proc.poll() is None:
                    self._started = True
                    return
                break
            if self._proc.poll() is not None:
                break
        self.stop()
        raise NetGuardError(f"tshark did not start capturing on {self.interface}: {self.stderr_text()[-300:]}")

    def stderr_text(self) -> str:
        return "".join(self._stderr)

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
                proc.send_signal(signal.SIGINT)  # dumpcap flushes the file and prints its counters
            except PermissionError:
                logger.debug("tshark does not accept signals (AppArmor): waiting for its -a duration limit")
            remaining = (self.duration_s - (time.monotonic() - self._started_mono)) if self.duration_s else 0.0
            try:
                proc.wait(timeout=max(10.0, remaining + 10.0))
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except PermissionError:
                    logger.error("tshark did not stop and cannot be killed (AppArmor)")
                proc.wait(timeout=10)
        if self._reader is not None:
            self._reader.join(timeout=5)
        text = "".join(self._stderr)
        m = re.search(r"(\d+) packets? captured", text)
        self.packets_captured = int(m.group(1)) if m else None
        m = re.search(r"(\d+) packets? dropped", text)
        self.packets_dropped = int(m.group(1)) if m else 0 if self.packets_captured is not None else None

    def raw_samples(self) -> List[Dict[str, Any]]:
        return []  # the pcap itself is the raw output (see ProtocolAnalysis)

    def health(self) -> Health:
        errors = []
        if not self.pcap.exists() or self.pcap.stat().st_size <= 24:
            errors.append("capture file is empty")
        # kernel drops are reported in the experiment metadata (capture.packets_dropped_by_kernel)
        return Health(self.name, self._started, attempted=1, failed=1 if errors else 0, errors=tuple(errors))


# ------------------------------------------------------------------------ analysis
def read_packets(pcap: Path) -> pd.DataFrame:
    """One row per captured packet with the fields needed for protocol accounting."""
    argv = ["tshark", "-r", str(pcap), "-n", "-T", "fields", "-E", "separator=,", "-E", "occurrence=f"]
    for f in _FIELDS:
        argv += ["-e", f]
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        raise NetGuardError(f"tshark -r failed (rc={done.returncode}): {done.stderr.strip()[:300]}")
    frame = pd.read_csv(io.StringIO(done.stdout), header=None, names=list(_FIELDS), dtype=str,
                        keep_default_na=False)
    for col in ("frame.time_epoch", "frame.len"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def protocol_hierarchy(pcap: Path) -> str:
    """Text of ``tshark -z io,phs`` (protocol hierarchy statistics) for the capture."""
    done = subprocess.run(["tshark", "-r", str(pcap), "-n", "-q", "-z", "io,phs"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else f"unavailable: {done.stderr.strip()[:200]}"


def classify_packets(packets: pd.DataFrame, http_port: int) -> pd.DataFrame:
    """Add boolean protocol columns (a DNS or HTTP packet is also counted as UDP / TCP)."""
    proto = pd.to_numeric(packets["ip.proto"], errors="coerce")
    tcp_ports = pd.concat([pd.to_numeric(packets["tcp.srcport"], errors="coerce"),
                           pd.to_numeric(packets["tcp.dstport"], errors="coerce")], axis=1)
    udp_ports = pd.concat([pd.to_numeric(packets["udp.srcport"], errors="coerce"),
                           pd.to_numeric(packets["udp.dstport"], errors="coerce")], axis=1)
    out = pd.DataFrame({"ts": packets["frame.time_epoch"], "bytes": packets["frame.len"]})
    out["tcp"] = proto == 6
    out["udp"] = proto == 17
    out["icmp"] = proto == 1
    out["http"] = out["tcp"] & (tcp_ports == http_port).any(axis=1)
    out["dns"] = out["udp"] & (udp_ports == 53).any(axis=1)
    resp = packets["dns.flags.response"].str.strip()
    out["dns_response"] = out["dns"] & resp.isin(["1", "True", "true"])
    out["dns_query"] = out["dns"] & ~out["dns_response"]
    out["dns_error"] = out["dns_response"] & ~packets["dns.flags.rcode"].str.strip().isin(["0", ""])
    return out


def derive_packet_metrics(classified: pd.DataFrame, n_seconds: int, anchor_wall: float) -> pd.DataFrame:
    """Per-second packet / byte counts per protocol from the classified capture."""
    names = ("pkts", "bytes", "tcp_pkts", "udp_pkts", "icmp_pkts", "dns_pkts", "http_pkts",
             "dns_queries", "dns_responses", "dns_errors")
    out = {f"tshark_{n}": [0.0] * n_seconds for n in names}
    if classified.empty:
        return pd.DataFrame(out)
    k = ((classified["ts"] - anchor_wall).apply(math.floor)).astype("Int64")
    valid = k.between(0, n_seconds - 1).fillna(False)
    sel, kk = classified[valid], k[valid].astype(int)
    grouped = {
        "pkts": sel.groupby(kk).size(),
        "bytes": sel.groupby(kk)["bytes"].sum(),
        "tcp_pkts": sel.groupby(kk)["tcp"].sum(), "udp_pkts": sel.groupby(kk)["udp"].sum(),
        "icmp_pkts": sel.groupby(kk)["icmp"].sum(), "dns_pkts": sel.groupby(kk)["dns"].sum(),
        "http_pkts": sel.groupby(kk)["http"].sum(), "dns_queries": sel.groupby(kk)["dns_query"].sum(),
        "dns_responses": sel.groupby(kk)["dns_response"].sum(), "dns_errors": sel.groupby(kk)["dns_error"].sum(),
    }
    for name, series in grouped.items():
        for second, value in series.items():
            out[f"tshark_{name}"][int(second)] = float(value)
    return pd.DataFrame(out)
