"""RTT, jitter and probe loss from ``ping`` probes every 0.2 s (inside Mininet).

``ping -D -O`` prints ``[epoch] ... icmp_seq=N ... time=X ms`` for every reply.
The *send* time of a probe is ``epoch - rtt``; probes that never got a reply are
placed on the schedule of their neighbours (``send(seq) = send(prev) +
(seq - prev) * interval``). A second's loss is therefore only defined where the
probe schedule is known, and the tail the process ran after the experiment makes
sure late replies are not mistaken for losses.

Jitter of a second = mean |RTT_i - RTT_{i-1}| over consecutive *received* probes
whose send time falls into that second (RFC 3550 style, but unsmoothed).
"""

from __future__ import annotations

import logging
import math
import re
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd

from netguard.exceptions import NetGuardError
from netguard.experiments.collectors.base import Collector, Health
from netguard.experiments.timeline import Timeline

logger = logging.getLogger(__name__)

_REPLY = re.compile(r"^\[(?P<ts>\d+(?:\.\d+)?)\].*?icmp_seq=(?P<seq>\d+).*?time=(?P<rtt>[\d.]+)\s*ms")
_SUMMARY = re.compile(r"(?P<sent>\d+) packets transmitted, (?P<recv>\d+) (?:packets )?received")


def ping_command(dst_ip: str, interval_s: float, payload_bytes: int) -> List[str]:
    return ["ping", "-n", "-D", "-O", "-i", f"{interval_s:g}", "-s", str(payload_bytes), "-W", "1", dst_ip]


def parse_ping_line(line: str) -> Optional[Dict[str, float]]:
    match = _REPLY.match(line.strip())
    if not match:
        return None
    rtt = float(match.group("rtt"))
    ts = float(match.group("ts"))
    return {"seq": int(match.group("seq")), "recv_ts": ts, "rtt_ms": rtt, "send_ts": ts - rtt / 1000.0}


class ProbeCollector(Collector):
    """Runs ping in the source host's namespace for the whole timeline."""

    name = "probe"

    def __init__(self, timeline: Timeline, popen: Callable[[Sequence[str]], subprocess.Popen],
                 argv: Sequence[str], interval_s: float = 0.2):
        self.timeline = timeline
        self._popen = popen
        self._argv = list(argv)
        self.interval_s = interval_s
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._replies: List[Dict[str, float]] = []
        self._summary: Dict[str, int] = {}
        self._lines = 0
        self._stderr_tail = ""
        self._started = False

    def start(self) -> None:
        self._proc = self._popen(self._argv)
        self._started = True
        self._reader = threading.Thread(target=self._read, name="collector-probe", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines += 1
            reply = parse_ping_line(line)
            if reply:
                self._replies.append(reply)
                continue
            summary = _SUMMARY.search(line)
            if summary:
                self._summary = {"sent": int(summary.group("sent")), "received": int(summary.group("recv"))}

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)  # makes ping print its statistics
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._reader is not None:
            self._reader.join(timeout=5)
        if proc.stderr is not None:
            try:
                self._stderr_tail = proc.stderr.read()[-300:]
            except (OSError, ValueError):
                pass

    def raw_samples(self) -> List[Dict[str, Any]]:
        return [{"tick": self.timeline.second_of(r["send_ts"]), "ts": r["recv_ts"], **r} for r in self._replies]

    @property
    def replies(self) -> List[Dict[str, float]]:
        return list(self._replies)

    @property
    def summary(self) -> Dict[str, int]:
        return dict(self._summary)

    def health(self) -> Health:
        errors = ()
        if not self._replies:
            errors = (f"no ping reply received (stderr: {self._stderr_tail.strip() or 'empty'})",)
        return Health(self.name, self._started, attempted=max(1, self._summary.get("sent", len(self._replies))),
                      failed=0 if self._replies else 1, errors=errors)


def derive_probe_metrics(
    replies: Sequence[Dict[str, float]], n_seconds: int, anchor_wall: float, interval_s: float = 0.2,
    total_sent: Optional[int] = None,
) -> pd.DataFrame:
    """Per-second probe counts, loss %, RTT statistics and jitter (NaN where nothing is known)."""
    cols = {k: [math.nan] * n_seconds for k in (
        "probe_sent", "probe_received", "probe_loss_pct", "rtt_mean_ms", "rtt_min_ms", "rtt_max_ms", "jitter_ms")}
    ordered = sorted({r["seq"]: r for r in replies}.values(), key=lambda r: r["seq"])
    if not ordered:
        return pd.DataFrame(cols)

    # send time of every sequence number from the first to the last probe sent
    last_seq = max(ordered[-1]["seq"], (total_sent or 0))
    send_ts: Dict[int, float] = {}
    got = {r["seq"]: r for r in ordered}
    prev = None
    for seq in range(ordered[0]["seq"], last_seq + 1):
        if seq in got:
            send_ts[seq] = got[seq]["send_ts"]
            prev = (seq, got[seq]["send_ts"])
        elif prev is not None:
            send_ts[seq] = prev[1] + (seq - prev[0]) * interval_s
    sent = [0] * n_seconds
    recv = [0] * n_seconds
    rtts: List[List[float]] = [[] for _ in range(n_seconds)]
    jit: List[List[float]] = [[] for _ in range(n_seconds)]
    last_rtt: Optional[float] = None
    for seq in sorted(send_ts):
        k = int(math.floor(send_ts[seq] - anchor_wall))
        if not 0 <= k < n_seconds:
            if seq in got:
                last_rtt = got[seq]["rtt_ms"]
            continue
        sent[k] += 1
        if seq in got:
            recv[k] += 1
            rtt = got[seq]["rtt_ms"]
            rtts[k].append(rtt)
            if last_rtt is not None:
                jit[k].append(abs(rtt - last_rtt))
            last_rtt = rtt
    for k in range(n_seconds):
        if sent[k] == 0:
            continue  # no knowledge of the probe schedule in this second
        cols["probe_sent"][k] = float(sent[k])
        cols["probe_received"][k] = float(recv[k])
        cols["probe_loss_pct"][k] = (sent[k] - recv[k]) / sent[k] * 100.0
        if rtts[k]:
            cols["rtt_mean_ms"][k] = sum(rtts[k]) / len(rtts[k])
            cols["rtt_min_ms"][k] = min(rtts[k])
            cols["rtt_max_ms"][k] = max(rtts[k])
        if jit[k]:
            cols["jitter_ms"][k] = sum(jit[k]) / len(jit[k])
    return pd.DataFrame(cols)
