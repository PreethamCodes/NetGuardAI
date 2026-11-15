"""Traffic command construction: exact argv, safety properties, iperf3 output parsing."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from netguard.exceptions import TrafficError
from netguard.scenarios import list_scenarios, load_scenario
from netguard.traffic import commands as cmd
from netguard.traffic.events import TrafficEvent, TrafficPlan
from netguard.traffic.planner import build_plan
from netguard.traffic.types import Protocol, Tool

ROOT = Path("/opt/netguard")
DST = "10.0.0.5"


def event(**kw) -> TrafficEvent:
    base = dict(event_id="e-0001", component="c", pattern="NORMAL", tool=Tool.IPERF3, protocol=Protocol.TCP,
                src="h1", dst="h5", start_s=0.0, duration_s=30.0, port=5201, seed=7)
    base.update(kw)
    return TrafficEvent(**base)


class TestIperf3:
    def test_server_is_one_shot(self):
        assert cmd.iperf3_server_command(5202) == ["iperf3", "-s", "-p", "5202", "-1"]

    def test_tcp_paced_with_packet_size(self, env):
        argv = cmd.iperf3_client_command(event(rate_mbps=2.0, packet_size=1000), DST, env.traffic)
        assert argv == ["iperf3", "-c", DST, "-p", "5201", "-t", "30", "-J", "--connect-timeout", "5000",
                        "-b", "2000K", "-l", "1000"]

    def test_unpaced_tcp_has_no_bandwidth_flag(self, env):
        assert "-b" not in cmd.iperf3_client_command(event(), DST, env.traffic)

    def test_udp_flag_and_rate(self, env):
        argv = cmd.iperf3_client_command(event(protocol=Protocol.UDP, rate_mbps=5.5, packet_size=1200), DST, env.traffic)
        assert "-u" in argv
        assert argv[argv.index("-b") + 1] == "5500K"
        assert argv[argv.index("-l") + 1] == "1200"

    def test_rate_is_split_over_parallel_streams(self, env):
        argv = cmd.iperf3_client_command(event(rate_mbps=3.0, parallel=3), DST, env.traffic)
        assert argv[argv.index("-b") + 1] == "1000K"  # iperf3 -b is per stream
        assert argv[argv.index("-P") + 1] == "3"

    def test_single_stream_has_no_parallel_flag(self, env):
        assert "-P" not in cmd.iperf3_client_command(event(rate_mbps=1), DST, env.traffic)

    def test_duration_rounds_to_whole_seconds_min_one(self, env):
        assert cmd.iperf3_client_command(event(duration_s=0.4), DST, env.traffic)[6] == "1"
        assert cmd.iperf3_client_command(event(duration_s=9.6), DST, env.traffic)[6] == "10"

    def test_missing_port_is_an_error(self, env):
        with pytest.raises(TrafficError, match="port"):
            cmd.iperf3_client_command(event(port=None), DST, env.traffic)


class TestPingAndHping3:
    def test_ping(self, env):
        e = event(tool=Tool.PING, protocol=Protocol.ICMP, request_rate=5, duration_s=10, packet_size=100)
        assert cmd.ping_command(e, DST, env.traffic) == ["ping", "-n", "-q", "-c", "50", "-i", "0.2", "-W", "1", "-s", "100", DST]

    def test_ping_without_size(self, env):
        e = event(tool=Tool.PING, protocol=Protocol.ICMP, request_rate=2, duration_s=10)
        assert "-s" not in cmd.ping_command(e, DST, env.traffic)

    def test_hping3_is_rate_limited_and_bounded(self, env):
        e = event(tool=Tool.HPING3, protocol=Protocol.UDP, rate_mbps=15, packet_size=512, duration_s=60)
        argv = cmd.hping3_command(e, DST, env.traffic)
        # 15 Mbit/s / (8 * 512 B) = 3662 pkt/s -> one packet every 273 us; 60 s -> 219727 packets
        assert argv == ["hping3", "--udp", "-p", "5001", "-d", "512", "-i", "u273", "-c", "219727", "-q", DST]

    def test_hping3_never_floods_or_spoofs(self, env):
        e = event(tool=Tool.HPING3, protocol=Protocol.UDP, rate_mbps=50, duration_s=5)
        argv = cmd.hping3_command(e, DST, env.traffic)
        for forbidden in ("--flood", "-f", "--rand-source", "--rand-dest", "-a", "--spoof"):
            assert forbidden not in argv
        assert "-c" in argv and "-i" in argv


class TestPythonWorkers:
    def test_http_client(self, env):
        e = event(tool=Tool.HTTP, protocol=Protocol.HTTP, request_rate=2.5, parallel=4, duration_s=12.5, seed=99)
        argv = cmd.http_client_command(e, DST, env.traffic, "python3", ROOT)
        assert argv[:5] == ["env", f"PYTHONPATH={ROOT}", "python3", "-m", "netguard.traffic.workers.http_client"]
        assert argv[5:] == ["--url", f"http://{DST}:8000/payload", "--rate", "2.5", "--duration", "12.5",
                            "--clients", "4", "--seed", "99", "--timeout", "5"]

    def test_http_server(self, env):
        argv = cmd.http_server_command(DST, env.traffic, "python3", ROOT)
        assert argv[4] == "netguard.traffic.workers.http_server"
        assert argv[5:] == ["--bind", DST, "--port", "8000", "--payload-kb", "64"]

    def test_dns_client_and_server(self, env):
        e = event(tool=Tool.DNS, protocol=Protocol.DNS, request_rate=4, duration_s=20)
        client = cmd.dns_client_command(e, DST, env.traffic, "python3", ROOT)
        assert client[4] == "netguard.traffic.workers.dns_client"
        assert client[client.index("--server") + 1] == DST
        assert client[client.index("--domains") + 1] == ",".join(env.traffic.dns.domains)
        server = cmd.dns_server_command(DST, env.traffic, "python3", ROOT)
        assert server[4] == "netguard.traffic.workers.dns_server" and server[-2:] == ["--port", "53"]

    def test_dispatch_covers_every_tool(self, env):
        cases = {
            Tool.IPERF3: event(rate_mbps=1),
            Tool.PING: event(tool=Tool.PING, protocol=Protocol.ICMP, request_rate=1),
            Tool.HPING3: event(tool=Tool.HPING3, protocol=Protocol.UDP, rate_mbps=1),
            Tool.HTTP: event(tool=Tool.HTTP, protocol=Protocol.HTTP, request_rate=1),
            Tool.DNS: event(tool=Tool.DNS, protocol=Protocol.DNS, request_rate=1),
        }
        assert set(cases) == set(Tool)
        for tool, e in cases.items():
            argv = cmd.build_client_command(e, DST, env.traffic)
            expected_head = tool.value if tool in (Tool.IPERF3, Tool.PING, Tool.HPING3) else "env"
            assert argv[0] == expected_head


class TestServers:
    def test_required_servers_are_deduplicated(self, env):
        events = (
            event(event_id="a", tool=Tool.HTTP, protocol=Protocol.HTTP, request_rate=1, dst="h5"),
            event(event_id="b", tool=Tool.HTTP, protocol=Protocol.HTTP, request_rate=1, dst="h5", src="h2"),
            event(event_id="c", tool=Tool.DNS, protocol=Protocol.DNS, request_rate=1, dst="h6"),
            event(event_id="d", rate_mbps=1),  # iperf3 servers are per event, not listed
        )
        specs = cmd.required_servers(TrafficPlan("t", 0, 60, events))
        assert specs == [cmd.ServerSpec("http", "h5"), cmd.ServerSpec("dns", "h6")]

    def test_build_server_command_dispatch(self, env):
        assert "http_server" in cmd.build_server_command(cmd.ServerSpec("http", "h5"), DST, env.traffic)[4]
        assert "dns_server" in cmd.build_server_command(cmd.ServerSpec("dns", "h5"), DST, env.traffic)[4]
        with pytest.raises(TrafficError):
            cmd.build_server_command(cmd.ServerSpec("ftp", "h5"), DST, env.traffic)


# Minimal documents shaped like the iperf3 -J client report.
IPERF_TCP = json.dumps({
    "start": {"test_start": {"protocol": "TCP"}},
    "end": {"sum_sent": {"bits_per_second": 9.9e6, "retransmits": 4},
            "sum_received": {"bits_per_second": 9.4e6}},
})
IPERF_UDP = json.dumps({
    "start": {"test_start": {"protocol": "UDP"}},
    "end": {"sum": {"bits_per_second": 5.0e6, "jitter_ms": 0.2, "lost_percent": 1.5}},
})


class TestIperf3Output:
    def test_tcp(self):
        r = cmd.parse_iperf3_json(IPERF_TCP)
        assert r.protocol == "tcp" and r.mbps == pytest.approx(9.4) and r.retransmits == 4

    def test_udp(self):
        r = cmd.parse_iperf3_json(IPERF_UDP)
        assert r.protocol == "udp" and r.mbps == pytest.approx(5.0) and r.lost_percent == 1.5

    def test_udp_report_that_also_has_sum_received(self):
        doc = json.loads(IPERF_UDP)
        doc["end"]["sum_received"] = {"bits_per_second": 4.0e6}
        assert cmd.parse_iperf3_json(json.dumps(doc)).protocol == "udp"

    @pytest.mark.parametrize("text", ["", "not json", "{}", '{"end": {}}', '{"error": "unable to connect"}'])
    def test_bad_reports(self, text):
        with pytest.raises(TrafficError):
            cmd.parse_iperf3_json(text)


class TestCommandSafety:
    """Properties that must hold for every shipped scenario: traffic stays inside Mininet."""

    FORBIDDEN = ("--flood", "--rand-source", "--rand-dest", "--spoof", "-a", "--fast", "--faster", "--ttl")

    @pytest.mark.parametrize("name", list_scenarios())
    def test_destinations_are_topology_hosts(self, name, env):
        plan = build_plan(load_scenario(name), env)
        topology_ips = {env.topology.host_ip(h) for h in env.topology.all_hosts}
        for e in plan.events:
            argv = cmd.build_client_command(e, env.topology.host_ip(e.dst), env.traffic)
            targets = [a for a in argv if a in topology_ips or a.startswith("http://")]
            assert targets, f"{e.event_id}: no topology address in {argv}"
            for a in argv:
                if a.startswith("http://"):
                    assert a.split("//")[1].split(":")[0] in topology_ips
            assert not any(flag in argv for flag in self.FORBIDDEN)

    @pytest.mark.parametrize("name", list_scenarios())
    def test_every_udp_stream_is_rate_limited(self, name, env):
        for e in build_plan(load_scenario(name), env).events:
            if e.protocol is Protocol.UDP and e.tool in (Tool.IPERF3, Tool.HPING3):
                assert e.rate_mbps is not None and e.rate_mbps <= env.traffic.limits.max_rate_mbps
                argv = cmd.build_client_command(e, env.topology.host_ip(e.dst), env.traffic)
                assert "-b" in argv or "-c" in argv

    def test_udp_flood_hosts_are_inside_topology(self, env):
        plan = build_plan(load_scenario("udp_flood"), env)
        flood = [e for e in plan.events if e.pattern == "UDP_FLOOD"]
        assert flood and all(e.src in env.topology.all_hosts and e.dst in env.topology.all_hosts for e in flood)
