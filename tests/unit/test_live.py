"""Live-demo code tests: packet aggregation, the per-second sample builder, the feeder and the live-vs-offline check.
Inputs are synthetic and only check the logic; they are never presented as experimental data."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from netguard.experiments.collectors.base import Collector
from netguard.experiments.collectors.livepackets import LivePacketCollector, PacketAggregator, live_command
from netguard.experiments.livefeed import LiveFeeder, build_sample, compare_live_with_offline
from netguard.experiments.timeline import Timeline

ANCHOR = 1000.0


def line(ts, src, dst, proto, sport="", dport="", syn="0", tcp=True):
    tcp_s, tcp_d = (sport, dport) if proto == 6 else ("", "")
    udp_s, udp_d = (sport, dport) if proto == 17 else ("", "")
    return f"{ts},{src},{dst},{proto},{tcp_s},{tcp_d},{udp_s},{udp_d},{syn}\n"


# ---------------------------------------------------------------------------- aggregation
def test_packets_are_counted_per_second_and_protocol():
    a = PacketAggregator(ANCHOR, http_port=8000)
    lines = [
        line(1000.1, "10.0.0.1", "10.0.0.5", 6, "41000", "5201"),
        line(1000.2, "10.0.0.2", "10.0.0.6", 6, "41002", "8000"),  # HTTP
        line(1000.3, "10.0.0.3", "10.0.0.7", 17, "40000", "53"),  # DNS
        line(1000.4, "10.0.0.4", "10.0.0.8", 1),  # ICMP
        line(1001.5, "10.0.0.1", "10.0.0.5", 6, "41000", "5201"),
        line(990.0, "10.0.0.9", "10.0.0.5", 6, "1", "2"),  # before the anchor: second -10
    ]
    assert all(a.add_line(x) for x in lines)
    s0, s1 = a.stats(0), a.stats(1)
    assert (s0["tshark_pkts"], s0["tshark_tcp_pkts"], s0["tshark_udp_pkts"], s0["tshark_icmp_pkts"]) == (4, 2, 1, 1)
    assert s0["tshark_http_pkts"] == 1 and s0["tshark_dns_pkts"] == 1
    assert s1["tshark_pkts"] == 1 and a.stats(5)["tshark_pkts"] == 0 and a.packets == 6


def test_flows_are_bidirectional_new_once_and_active_when_they_send():
    a = PacketAggregator(ANCHOR, 8000)
    for ts, s, d, sp, dp in [(1000.1, "10.0.0.1", "10.0.0.5", "41000", "5201"), (1000.2, "10.0.0.5", "10.0.0.1", "5201", "41000"),
                             (1001.1, "10.0.0.1", "10.0.0.5", "41000", "5201"), (1001.2, "10.0.0.2", "10.0.0.6", "50000", "8000")]:
        a.add_line(line(ts, s, d, 6, sp, dp))
    s0, s1, s2 = a.stats(0), a.stats(1), a.stats(2)
    assert s0["nf_new_flows"] == 1 and s0["nf_active_flows"] == 1  # request and reply are one flow
    assert s1["nf_new_flows"] == 1 and s1["nf_active_flows"] == 2  # a second flow starts, the first continues
    assert s2["nf_active_flows"] == 0


def test_a_flow_is_active_between_its_first_and_last_packet_like_the_offline_definition():
    a = PacketAggregator(ANCHOR, 8000)
    a.add_line(line(1000.1, "10.0.0.1", "10.0.0.5", 6, "41000", "5201"))
    a.add_line(line(1003.1, "10.0.0.1", "10.0.0.5", 6, "41000", "5201"))  # silent in seconds 1 and 2
    assert [a.stats(k)["nf_active_flows"] for k in range(5)] == [1, 1, 1, 1, 0]
    assert [a.stats(k)["nf_new_flows"] for k in range(5)] == [1, 0, 0, 0, 0]


def test_syn_packets_count_syn_and_synack_only_for_tcp():
    a = PacketAggregator(ANCHOR, 8000)
    a.add_line(line(1000.1, "10.0.0.1", "10.0.0.5", 6, "41000", "5201", syn="1"))
    a.add_line(line(1000.2, "10.0.0.5", "10.0.0.1", 6, "5201", "41000", syn="True"))
    a.add_line(line(1000.3, "10.0.0.1", "10.0.0.5", 6, "41000", "5201", syn="0"))
    a.add_line(line(1000.4, "10.0.0.1", "10.0.0.5", 17, "1", "2", syn="1"))
    assert a.stats(0)["syn_packets"] == 2


def test_malformed_lines_are_ignored_and_the_command_is_bounded():
    a = PacketAggregator(ANCHOR, 8000)
    assert not a.add_line("garbage\n") and not a.add_line("notanumber,a,b,6,,,,,\n")
    assert a.packets == 0
    argv = live_command("s1-eth5", 96, 52.2)
    assert argv[:3] == ["tshark", "-i", "s1-eth5"] and "-l" in argv and argv[argv.index("-a") + 1] == "duration:53"
    assert "tcp.flags.syn" in argv


# ---------------------------------------------------------------------------- the sample
class Fake(Collector):
    def __init__(self, name, rows=None):
        self.name, self._rows = name, rows or []

    def raw_samples(self):
        return self._rows


class FakeProbe(Fake):
    replies = [{"seq": i + 1, "send_ts": ANCHOR + 0.2 * i, "recv_ts": ANCHOR + 0.2 * i + 0.04, "rtt_ms": 40.0}
               for i in range(40)]


class FakePackets(Fake):
    def __init__(self):
        super().__init__("live_packets")
        a = PacketAggregator(ANCHOR, 8000)
        for i in range(10):
            a.add_line(line(ANCHOR + 0.5 + i * 0.01, "10.0.0.1", "10.0.0.5", 6, "41000", "5201", syn="1" if i == 0 else "0"))
        self.aggregator = a


def synthetic_collectors(n_seconds=3):
    iface, queue, conn, res = [], [], [], []
    for tick in range(-2, n_seconds + 3):
        ts = ANCHOR + tick
        for d in ("fwd", "rev"):
            iface.append({"direction": d, "dev": d, "tick": tick, "ts": ts, "tx_bytes": int(tick * 1_250_000 * (1 if d == "fwd" else 0.5)),
                          "tx_packets": tick * 1000, "tx_dropped": 0})
            queue.append({"direction": d, "dev": d, "tick": tick, "ts": ts, "tbf_backlog_bytes": 62_500 if d == "fwd" else 0,
                          "tbf_qlen": 41, "tbf_drops": 0, "tbf_overlimits": 0, "tbf_packets": tick * 1000, "tbf_bytes": 0,
                          "tbf_rate_bytes_per_s": 1_250_000.0, "netem_drops": 0, "netem_packets": tick * 1000, "netem_backlog_bytes": 0})
        conn.append({"tick": tick, "ts": ts, "active_connections": 4, "tcp_sockets": 8, "retrans_new": 1})
        res.append({"tick": tick, "ts": ts, "cpu_percent": 12.0, "ram_percent": 40.0, "ram_used_mb": 1.0})
    probe = FakeProbe("probe")
    probe.interval_s = 0.2
    probe.replies = FakeProbe.replies
    return {"interface": Fake("interface", iface), "queue": Fake("queue", queue), "connections": Fake("connections", conn),
            "resources": Fake("resources", res), "probe": probe, "live_packets": FakePackets()}


def test_sample_of_a_second_uses_the_same_derivations_as_the_dataset():
    tl = Timeline(ANCHOR, 3, pre_roll_s=2, tail_s=2)
    s = build_sample(1, tl, synthetic_collectors(), capacity_mbps=10.0, probe_interval_s=0.2)
    assert s["throughput_mbps"] == pytest.approx(10.0)  # the busier direction: 1.25 MB/s
    assert s["utilisation_pct"] == pytest.approx(100.0) and s["queue_delay_ms"] == pytest.approx(50.0)
    assert s["rtt_mean_ms"] == pytest.approx(40.0) and s["probe_loss_pct"] == 0.0
    assert s["active_connections"] == 4 and s["tcp_retransmissions"] == 1 and s["cpu_percent"] == 12.0
    assert s["tshark_pkts"] == 0.0 and s["syn_packets"] == 0.0  # no packets in second 1
    assert build_sample(0, tl, synthetic_collectors(), 10.0, 0.2)["syn_packets"] == 1.0
    assert s["timestamp_s"] == ANCHOR + 1


def test_unmeasured_metrics_are_sent_as_null_not_zero():
    tl = Timeline(ANCHOR, 3, pre_roll_s=2, tail_s=2)
    c = synthetic_collectors()
    c["resources"]._rows = []  # no CPU samples at all
    s = build_sample(1, tl, c, 10.0, 0.2)
    assert s["cpu_percent"] is None and s["ram_percent"] is None
    assert all(v is None or math.isfinite(v) for v in s.values())  # JSON-safe: no NaN


# ------------------------------------------------------------------------------ the feeder
def test_feeder_posts_one_sample_per_second_and_keeps_going_when_the_backend_is_down():
    tl = Timeline(ANCHOR, 3, pre_roll_s=2, tail_s=2)
    sent = []
    now = [ANCHOR + 100]  # everything is already due

    def poster(url, payload):
        sent.append((url, payload))
        if len(sent) == 2:
            raise OSError("connection refused")
        return 200

    feeder = LiveFeeder(tl, synthetic_collectors(), backend_url="http://x:8000/", stream_id="s1", capacity_mbps=10.0,
                        poster=poster, clock=lambda: now[0], delay_s=0.0)
    assert LiveFeeder(tl, {}, backend_url="x", stream_id="y", capacity_mbps=1.0).delay >= 3.0  # flow look-ahead
    feeder.start()
    feeder._thread.join(timeout=5)
    assert [p["timestamp_s"] for _, p in sent] == [ANCHOR, ANCHOR + 1, ANCHOR + 2]
    assert all(u == "http://x:8000/api/ingest" and p["stream_id"] == "s1" for u, p in sent)
    assert feeder.posted == 2 and feeder.failed == 1 and "refused" in feeder.last_error
    assert len(feeder.samples) == 3  # kept for the comparison even if a post failed


def test_live_vs_offline_comparison_reports_differences_honestly():
    live = [{"second": k, "nf_active_flows": 10.0 + k, "nf_new_flows": 2.0, "tshark_pkts": 100.0} for k in range(5)]
    off = pd.DataFrame({"second": range(5), "nf_active_flows": [10.0, 11.0, 12.0, 14.0, 14.0], "nf_new_flows": 2.0,
                        "tshark_pkts": 100.0})
    out = compare_live_with_offline(live, off)
    assert out["seconds_compared"] == 5
    assert out["columns"]["nf_new_flows"]["mean_abs_diff"] == 0.0
    assert out["columns"]["nf_active_flows"]["max_abs_diff"] == 1.0
    assert out["columns"]["nf_active_flows"]["correlation"] > 0.9
