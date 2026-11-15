"""Collector parsing, per-second derivations, timestamp alignment, start/stop behaviour."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

from netguard.experiments.collectors.base import PeriodicCollector
from netguard.experiments.collectors.capture import (
    classify_packets, derive_packet_metrics, tshark_capture_command,
)
from netguard.experiments.collectors.connections import (
    ConnectionCollector, derive_connection_metrics, parse_ss,
)
from netguard.experiments.collectors.flows import derive_flow_metrics
from netguard.experiments.collectors.interface import (
    InterfaceCollector, derive_interface_metrics, parse_ip_link_json,
)
from netguard.experiments.collectors.probe import derive_probe_metrics, parse_ping_line
from netguard.experiments.collectors.queue import (
    QueueCollector, derive_queue_metrics, parse_tc_qdisc_json,
)
from netguard.experiments.collectors.resources import (
    ResourceCollector, derive_resource_metrics, saturated_seconds,
)
from netguard.experiments.timeline import Timeline

NAN = math.nan


def ip_json(**devs: Dict[str, int]) -> str:
    links = []
    for name, c in devs.items():
        links.append({"ifname": name, "stats64": {
            "rx": {"bytes": c.get("rx_bytes", 0), "packets": c.get("rx_packets", 0), "dropped": 0},
            "tx": {"bytes": c["tx_bytes"], "packets": c["tx_packets"], "dropped": c.get("tx_dropped", 0)}}})
    return json.dumps(links)


# ----------------------------------------------------------------------- timeline
def test_timeline_ticks_and_seconds():
    t = Timeline(anchor_wall=1000.0, duration_s=10, pre_roll_s=3, tail_s=2)
    ticks = list(t.ticks())
    assert ticks[0] == -3 and ticks[-1] == 12 and len(ticks) == 16
    assert t.tick_time(0) == 1000.0 and t.start_wall == 997.0 and t.end_wall == 1012.0
    assert t.second_of(1000.0) == 0 and t.second_of(1000.999) == 0 and t.second_of(999.5) == -1


# ----------------------------------------------------------------- interface / throughput
def test_parse_ip_link_json_selects_requested_devices():
    text = ip_json(**{"s1-eth5": {"tx_bytes": 10, "tx_packets": 1}, "lo": {"tx_bytes": 5, "tx_packets": 1}})
    assert parse_ip_link_json(text, ["s1-eth5"])["s1-eth5"]["tx_bytes"] == 10
    with pytest.raises(ValueError, match="not found"):
        parse_ip_link_json(text, ["s2-eth5"])


def samples(direction: str, values: List[int], pkts: List[int], start_tick: int = 0, dt: float = 1.0):
    return [{"direction": direction, "tick": start_tick + i, "ts": 100.0 + (start_tick + i) * dt,
             "tx_bytes": b, "tx_packets": p, "tx_dropped": 0} for i, (b, p) in enumerate(zip(values, pkts))]


def test_throughput_pps_and_utilisation():
    # 1.25 MB/s = 10 Mbit/s on a 10 Mbit/s link -> 100 %; second 1 carries half of it
    raw = samples("fwd", [0, 1_250_000, 1_875_000], [0, 1000, 1500]) + samples("rev", [0, 0, 0], [0, 0, 0])
    out = derive_interface_metrics(raw, 2, capacity_mbps=10.0)
    assert out["fwd_throughput_mbps"].tolist() == pytest.approx([10.0, 5.0])
    assert out["fwd_tx_bytes_per_s"].tolist() == pytest.approx([1_250_000, 625_000])
    assert out["fwd_pps"].tolist() == pytest.approx([1000, 500])
    assert out["fwd_utilisation_pct"].tolist() == pytest.approx([100.0, 50.0])
    assert out["rev_utilisation_pct"].tolist() == [0.0, 0.0]


def test_rate_uses_the_actual_elapsed_time():
    raw = samples("fwd", [0, 1_250_000], [0, 1], dt=1.25)  # same bytes over 1.25 s -> 8 Mbit/s
    out = derive_interface_metrics(raw, 1, capacity_mbps=10.0)
    assert out["fwd_throughput_mbps"][0] == pytest.approx(8.0)


def test_missing_ticks_and_counter_resets_are_not_invented():
    raw = samples("fwd", [0, 100, 50, 150], [0, 1, 0, 2])  # tick 2 is lower than tick 1: counter reset
    out = derive_interface_metrics(raw, 3, 10.0)
    assert out["fwd_throughput_mbps"].isna().tolist() == [False, True, False]
    gap = [r for r in samples("fwd", [0, 100, 200, 300], [0, 1, 2, 3]) if r["tick"] != 2]
    assert derive_interface_metrics(gap, 3, 10.0)["fwd_throughput_mbps"].isna().tolist() == [False, True, True]
    late = samples("fwd", [0, 100], [0, 1], dt=3.0)  # samples 3 s apart: not a per-second measurement
    assert derive_interface_metrics(late, 1, 10.0)["fwd_throughput_mbps"].isna().all()
    assert derive_interface_metrics([], 2, 10.0)["fwd_utilisation_pct"].isna().all()


def test_interface_collector_reads_counters_each_tick(monkeypatch):
    out = {"n": 0}

    def run(argv):
        out["n"] += 1
        n = out["n"]
        return 0, ip_json(**{"s1-eth5": {"tx_bytes": 1000 * n, "tx_packets": n},
                             "s2-eth5": {"tx_bytes": 10 * n, "tx_packets": n}})

    timeline = Timeline(time.time() + 0.05, 2, pre_roll_s=0, tail_s=0)
    # accelerate: tick spacing is 1 s, so test the sampling function directly
    collector = InterfaceCollector(timeline, {"fwd": "s1-eth5", "rev": "s2-eth5"}, runner=run)
    rows = collector.sample(0, 1.0)
    assert {r["direction"] for r in rows} == {"fwd", "rev"} and rows[0]["tx_bytes"] == 1000


# ----------------------------------------------------------------------- queue (tc)
TC_NETEM_TBF = json.dumps([
    {"kind": "netem", "handle": "1:", "root": True, "options": {"limit": 1000}, "bytes": 5000, "packets": 40,
     "drops": 5, "overlimits": 0, "requeues": 0, "backlog": 3000, "qlen": 2},
    {"kind": "tbf", "handle": "10:", "parent": "1:1", "options": {"rate": 1250000, "burst": 12500, "lat": 50000},
     "bytes": 5000, "packets": 40, "drops": 3, "overlimits": 9, "requeues": 0, "backlog": 15000, "qlen": 10}])


def test_parse_tc_qdisc_json_finds_netem_and_tbf():
    parsed = parse_tc_qdisc_json(TC_NETEM_TBF)
    assert parsed["tbf"]["backlog"] == 15000 and parsed["netem"]["drops"] == 5
    with pytest.raises(ValueError, match="no tbf"):
        parse_tc_qdisc_json(json.dumps([{"kind": "noqueue"}]))


def qsample(direction, tick, backlog, tbf_drops, netem_drops, packets, ts=None):
    return {"direction": direction, "dev": "x", "tick": tick, "ts": 100.0 + tick if ts is None else ts,
            "tbf_backlog_bytes": backlog, "tbf_qlen": backlog // 1500, "tbf_drops": tbf_drops, "tbf_overlimits": 0,
            "tbf_packets": packets, "tbf_bytes": 0, "tbf_rate_bytes_per_s": 1_250_000.0,
            "netem_drops": netem_drops, "netem_packets": packets, "netem_backlog_bytes": 0}


def test_queue_delay_is_backlog_over_rate_and_loss_counts_all_drops():
    raw = [qsample("fwd", 0, 0, 0, 0, 0), qsample("fwd", 1, 62_500, 20, 25, 475)]
    out = derive_queue_metrics(raw, 1, capacity_mbps=10.0)
    assert out["fwd_queue_delay_ms"][0] == pytest.approx(50.0)  # 62.5 kB at 1.25 MB/s
    assert out["fwd_queue_backlog_bytes"][0] == 62_500 and out["fwd_queue_backlog_packets"][0] == 41
    assert out["fwd_queue_drops_per_s"][0] == pytest.approx(20)
    assert out["fwd_random_drops_per_s"][0] == pytest.approx(5)  # netem total minus queue overflow
    assert out["fwd_tc_loss_pct"][0] == pytest.approx(25 / (475 + 25) * 100)
    assert out["rev_queue_delay_ms"].isna().all()  # no samples for the other direction


def test_queue_idle_second_has_zero_loss_not_nan_and_counter_reset_is_nan():
    idle = [qsample("fwd", 0, 0, 7, 7, 100), qsample("fwd", 1, 0, 7, 7, 100)]
    assert derive_queue_metrics(idle, 1, 10.0)["fwd_tc_loss_pct"][0] == 0.0
    reset = [qsample("fwd", 0, 0, 7, 7, 100), qsample("fwd", 1, 0, 0, 0, 5)]
    assert derive_queue_metrics(reset, 1, 10.0)["fwd_tc_loss_pct"].isna().all()


def test_queue_collector_runs_tc_per_device():
    calls = []

    def run(argv):
        calls.append(list(argv))
        return 0, TC_NETEM_TBF

    rows = QueueCollector(Timeline(0, 1), {"fwd": "s1-eth5", "rev": "s2-eth5"}, runner=run).sample(0, 0.0)
    assert [c[-1] for c in calls] == ["s1-eth5", "s2-eth5"] and calls[0][:3] == ["tc", "-s", "-j"]
    assert rows[0]["tbf_backlog_bytes"] == 15000


# --------------------------------------------------------------- connections (ss -ti)
SS_OUT = (
    "ESTAB 0 0 10.0.0.1:41000 10.0.0.5:5201 \n"
    "\t cubic rto:240 rtt:41.2/1.0 mss:1448 cwnd:10 retrans:0/3 bytes_sent:100\n"
    "ESTAB 0 0 10.0.0.1:41002 10.0.0.6:8000 \n"
    "\t cubic rto:240 rtt:42/1 cwnd:10 bytes_sent:100\n"
    "TIME-WAIT 0 0 10.0.0.1:41004 10.0.0.6:8000 \n"
)


def test_parse_ss_extracts_state_endpoints_and_total_retransmissions():
    socks = parse_ss(SS_OUT)
    assert [s["state"] for s in socks] == ["ESTAB", "ESTAB", "TIME-WAIT"]
    assert socks[0]["local"] == ("10.0.0.1", 41000) and socks[0]["peer"] == ("10.0.0.5", 5201)
    assert [s["retrans_total"] for s in socks] == [3, 0, 0]


def test_connections_deduplicate_both_endpoints_and_diff_retransmissions():
    server_view = "ESTAB 0 0 10.0.0.5:5201 10.0.0.1:41000 \n\t cubic retrans:0/1\n"
    state = {"tick": 0}

    def run(argv):
        host_pid = int(argv[2])
        retr = 3 + 2 * state["tick"]
        client = SS_OUT.replace("retrans:0/3", f"retrans:0/{retr}")
        return 0, client if host_pid == 1 else server_view

    collector = ConnectionCollector(Timeline(0, 2), {"h1": 1, "h5": 2}, runner=run)
    first = collector.sample(0, 0.0)[0]
    assert first["active_connections"] == 2  # h1's two ESTAB sockets; h5 duplicates the first one
    assert first["tcp_sockets"] == 4 and first["retrans_new"] == 0  # baseline tick: no previous reading
    state["tick"] = 1
    assert collector.sample(1, 1.0)[0]["retrans_new"] == 2  # 3 -> 5 on one socket


def test_connection_metrics_align_with_seconds():
    raw = [{"tick": k, "ts": float(k), "active_connections": 10 + k, "tcp_sockets": 0, "retrans_new": k}
           for k in range(4)]
    out = derive_connection_metrics(raw, 3)
    assert out["active_connections"].tolist() == [11, 12, 13]  # state at the end of second k
    assert out["tcp_retransmissions"].tolist() == [1, 2, 3]
    assert derive_connection_metrics([], 2)["active_connections"].isna().all()


# ------------------------------------------------------------- ping: RTT, jitter, loss
def test_parse_ping_line():
    r = parse_ping_line("[1700000010.250000] 64 bytes from 10.0.0.8: icmp_seq=7 ttl=64 time=41.3 ms")
    assert r["seq"] == 7 and r["rtt_ms"] == 41.3
    assert r["send_ts"] == pytest.approx(1700000010.25 - 0.0413)
    assert parse_ping_line("[1700000011.0] no answer yet for icmp_seq=8") is None
    assert parse_ping_line("PING 10.0.0.8 (10.0.0.8) 56(84) bytes of data.") is None


def reply(seq: int, send: float, rtt: float) -> Dict[str, float]:
    return {"seq": seq, "send_ts": send, "recv_ts": send + rtt / 1000, "rtt_ms": rtt}


def test_probe_rtt_jitter_and_loss_per_second():
    anchor = 1000.0
    # 5 probes/s at 0.2 s spacing; second 0 loses seq 3 and 4; second 1 has all five
    replies = [reply(1, 1000.00, 40.0), reply(2, 1000.20, 42.0), reply(5, 1000.80, 41.0)]
    replies += [reply(6 + i, 1001.0 + 0.2 * i, 40.0 + i) for i in range(5)]
    out = derive_probe_metrics(replies, 2, anchor, 0.2, total_sent=10)
    assert out["probe_sent"].tolist() == [5, 5]
    assert out["probe_received"].tolist() == [3, 5]
    assert out["probe_loss_pct"].tolist() == pytest.approx([40.0, 0.0])
    assert out["rtt_mean_ms"][0] == pytest.approx((40 + 42 + 41) / 3)
    assert out["rtt_min_ms"][0] == 40 and out["rtt_max_ms"][0] == 42
    # jitter = mean |rtt_i - rtt_(i-1)| over consecutive received probes: (|42-40| + |41-42|) / 2
    assert out["jitter_ms"][0] == pytest.approx(1.5)
    assert out["jitter_ms"][1] == pytest.approx((1 + 1 + 1 + 1 + abs(40 - 41)) / 5)  # first diff vs. previous second


def test_probe_total_loss_in_a_second_is_visible_and_unknown_seconds_stay_nan():
    anchor = 0.0
    replies = [reply(1, 0.0, 40.0), reply(11, 2.0, 40.0)]  # seconds 0 and 1 mostly lost
    out = derive_probe_metrics(replies, 4, anchor, 0.2)
    assert out["probe_loss_pct"][1] == 100.0 and out["rtt_mean_ms"].isna()[1]
    assert out["probe_sent"][3] != out["probe_sent"][3]  # after the last known probe: unknown, not zero
    assert derive_probe_metrics([], 3, 0.0)["probe_sent"].isna().all()


def test_probe_trailing_losses_are_counted_from_the_ping_summary():
    replies = [reply(i, 0.2 * (i - 1), 40.0) for i in range(1, 6)]  # seq 1..5 -> second 0 fully answered
    out = derive_probe_metrics(replies, 2, 0.0, 0.2, total_sent=10)  # ping says it sent 10: 6..10 unanswered
    assert out["probe_loss_pct"].tolist() == [0.0, 100.0]


# ------------------------------------------------------------------------- resources
class FakePsutil:
    def __init__(self, cpu):
        self.cpu = list(cpu)
        self.calls = 0

    def cpu_percent(self, interval=None):
        self.calls += 1
        return self.cpu.pop(0) if self.cpu else 0.0

    def virtual_memory(self):
        return type("M", (), {"percent": 33.0, "used": 2_000_000_000})()


def test_resource_collector_primes_cpu_and_reports_percent():
    ps = FakePsutil([0.0, 12.5])
    collector = ResourceCollector(Timeline(0, 1), psutil_module=ps)
    collector.prepare()
    row = collector.sample(1, 1.0)[0]
    assert ps.calls == 2 and row["cpu_percent"] == 12.5 and row["ram_percent"] == 33.0
    assert row["ram_used_mb"] == pytest.approx(2000.0)


def test_resource_metrics_and_saturation_detection():
    raw = [{"tick": k, "ts": float(k), "cpu_percent": c, "ram_percent": 30.0, "ram_used_mb": 1.0}
           for k, c in enumerate([5.0, 40.0, 100.0, 99.9, 50.0])]
    out = derive_resource_metrics(raw, 4)
    assert out["cpu_percent"].tolist() == [40.0, 100.0, 99.9, 50.0]
    assert saturated_seconds(raw, 100.0, 1, 4) == 1  # only the 100.0 sample
    assert saturated_seconds(raw, 99.0, 1, 4) == 2
    assert saturated_seconds(raw, 100.0, 3, 4) == 0  # restricted to the measurement window


# ---------------------------------------------------------------- TShark / NFStream
def test_tshark_command_is_header_only_and_bounded(tmp_path):
    argv = tshark_capture_command("s1-eth5", tmp_path / "x.pcap", 96)
    assert argv[:3] == ["tshark", "-i", "s1-eth5"] and argv[argv.index("-s") + 1] == "96"
    assert "-w" in argv and "-q" in argv
    assert "-a" not in argv  # unbounded unless a duration is given
    bounded = tshark_capture_command("s1-eth5", tmp_path / "x.pcap", 96, duration_s=52.2)
    assert bounded[bounded.index("-a") + 1] == "duration:53"  # ends (and flushes) by itself: no signal needed


def packets(rows):
    cols = ["frame.time_epoch", "frame.len", "ip.proto", "tcp.srcport", "tcp.dstport", "udp.srcport",
            "udp.dstport", "dns.flags.response", "dns.flags.rcode"]
    return pd.DataFrame(rows, columns=cols).astype(str).assign(**{"frame.time_epoch": [r[0] for r in rows],
                                                                   "frame.len": [r[1] for r in rows]})


def test_packets_are_classified_by_protocol_and_counted_per_second():
    rows = [
        (1000.1, 1500, "6", "41000", "5201", "", "", "", ""),  # TCP
        (1000.2, 100, "6", "41002", "8000", "", "", "", ""),  # HTTP (TCP port 8000)
        (1000.3, 80, "17", "", "", "40000", "53", "0", ""),  # DNS query
        (1000.4, 120, "17", "", "", "53", "40000", "1", "3"),  # DNS response, NXDOMAIN
        (1001.5, 98, "1", "", "", "", "", "", ""),  # ICMP
        (1001.6, 1200, "17", "", "", "5001", "5001", "", ""),  # plain UDP
        (1009.0, 50, "6", "1", "2", "", "", "", ""),  # outside the 2 s window
    ]
    classified = classify_packets(packets(rows), http_port=8000)
    out = derive_packet_metrics(classified, 2, anchor_wall=1000.0)
    assert out["tshark_pkts"].tolist() == [4, 2]
    assert out["tshark_bytes"].tolist() == [1800, 1298]
    assert out["tshark_tcp_pkts"].tolist() == [2, 0] and out["tshark_http_pkts"].tolist() == [1, 0]
    assert out["tshark_udp_pkts"].tolist() == [2, 1] and out["tshark_dns_pkts"].tolist() == [2, 0]
    assert out["tshark_dns_queries"].tolist() == [1, 0] and out["tshark_dns_responses"].tolist() == [1, 0]
    assert out["tshark_dns_errors"].tolist() == [1, 0] and out["tshark_icmp_pkts"].tolist() == [0, 1]


def test_flow_metrics_count_active_and_new_flows():
    flows = pd.DataFrame({
        "bidirectional_first_seen_ms": [1_000_100, 1_000_900, 1_002_500],
        "bidirectional_last_seen_ms": [1_002_100, 1_001_100, 1_002_600],
    })
    out = derive_flow_metrics(flows, 4, anchor_wall=1000.0)
    assert out["nf_new_flows"].tolist() == [2, 0, 1, 0]
    assert out["nf_active_flows"].tolist() == [2, 2, 2, 0]  # flow1: seconds 0-2, flow2: 0-1, flow3: 2
    assert derive_flow_metrics(pd.DataFrame(columns=flows.columns), 2, 0.0)["nf_active_flows"].tolist() == [0, 0]


# --------------------------------------------------------- collector start / stop
class CountingCollector(PeriodicCollector):
    name = "counting"

    def __init__(self, timeline, fail_ticks=(), **kw):
        super().__init__(timeline, **kw)
        self.fail_ticks = set(fail_ticks)
        self.started_at: List[float] = []
        self.finished = False

    def sample(self, tick, ts):
        if tick in self.fail_ticks:
            raise RuntimeError("boom")
        self.started_at.append(ts)
        return [{"value": tick}]

    def finish(self):
        self.finished = True


class FastClock:
    """Virtual clock: ``wait`` jumps time forward, so 1 s ticks cost no real time."""

    def __init__(self, start: float):
        self.now = start

    def __call__(self):
        return self.now


def test_periodic_collector_samples_every_tick_on_the_grid():
    anchor = time.time() + 0.1
    timeline = Timeline(anchor, 2, pre_roll_s=0, tail_s=0)
    # use a tiny real timeline: 3 ticks, one second apart
    c = CountingCollector(timeline)
    c.start()
    assert c.wait_done(timeout=6)
    c.stop()
    assert [r["tick"] for r in c.raw_samples()] == [0, 1, 2]
    for tick, ts in zip(range(3), c.started_at):
        assert abs(ts - (anchor + tick)) < 0.15  # sampled at the grid instant, all collectors share it
    health = c.health()
    assert health.ok and health.attempted == 3 and c.finished


def test_collector_errors_are_recorded_and_flag_the_collector_unhealthy():
    timeline = Timeline(time.time() + 0.05, 2, pre_roll_s=0, tail_s=0)
    c = CountingCollector(timeline, fail_ticks=(0, 1, 2))
    c.start()
    c.wait_done(timeout=6)
    c.stop()
    h = c.health()
    assert not h.ok and h.failed == 3 and "boom" in h.errors[0] and c.raw_samples() == []


def test_stop_interrupts_a_collector_waiting_for_a_future_tick():
    c = CountingCollector(Timeline(time.time() + 3600, 5, pre_roll_s=0, tail_s=0))
    c.start()
    t0 = time.time()
    c.stop()
    assert time.time() - t0 < 2 and c.finished and c.raw_samples() == []


# ------------------------------------------------- TShark start-up failures are detected
class FakePopen:
    """tshark that prints 'Capturing on' and then dies because it cannot open the output file."""

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.returncode = None
        self.stderr = iter(["Capturing on 'x'\n", "tshark: The file ... could not be opened: Permission denied.\n",
                            "0 packets captured\n"])
        self._alive_polls = 0

    def poll(self):
        return 1 if self.returncode is not None else None

    def send_signal(self, sig):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_tshark_that_dies_after_announcing_the_capture_is_a_start_failure(monkeypatch, tmp_path):
    import netguard.experiments.collectors.capture as capture
    from netguard.exceptions import NetGuardError

    def popen(argv, **kw):
        proc = FakePopen(argv, **kw)
        proc.returncode = 1  # already exited by the time the settle period is over
        return proc

    monkeypatch.setattr(capture.subprocess, "Popen", popen)
    collector = capture.TsharkCollector("s1-eth5", tmp_path / "x.pcap", settle_s=0.05)
    with pytest.raises(NetGuardError, match="Permission denied"):
        collector.start()
    assert not collector.health().ok


def test_tshark_stop_does_not_need_permission_to_signal_the_process(monkeypatch, tmp_path):
    """AppArmor makes kill() on tshark fail with EACCES: stop() must fall back to waiting for -a duration."""
    import netguard.experiments.collectors.capture as capture

    class Confined(FakePopen):
        def send_signal(self, sig):
            raise PermissionError(13, "Permission denied")

        def wait(self, timeout=None):
            self.returncode = 0  # the duration limit ended the capture
            return 0

        def kill(self):
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(capture.subprocess, "Popen", lambda argv, **kw: Confined(argv, **kw))
    collector = capture.TsharkCollector("s1-eth5", tmp_path / "x.pcap", settle_s=0.05, duration_s=5)
    collector.start()
    collector.stop()  # must not raise
    assert collector._proc.returncode == 0
