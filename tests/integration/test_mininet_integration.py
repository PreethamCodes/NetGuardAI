"""Mininet integration test. Run INSIDE the Ubuntu VM, as root:

    sudo mn -c                                   # clean up leftovers of earlier runs
    sudo .venv/bin/python -m pytest -m integration tests/integration -v -s

It builds the real dumbbell (Open vSwitch + tc), and checks topology creation,
host connectivity, Open vSwitch operation, the bottleneck configuration and
basic TCP/UDP traffic generation. Skipped (with the reason shown by ``-rs``)
when not on Linux, not root, or when Mininet / Open vSwitch / iperf3 are missing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from typing import Any, Optional

import pytest

from netguard.config.environment import ExecutorSettings
from netguard.topology import tc
from netguard.topology.dumbbell import (
    bottleneck_interfaces,
    mininet_available,
    node_runner,
    running_network,
    trunk_port,
    verify_shaping,
)
from netguard.traffic import commands as cmd
from netguard.traffic.events import TrafficEvent, TrafficPlan
from netguard.traffic.executor import TrafficExecutor
from netguard.traffic.types import Protocol, Tool
from netguard.utils.system import is_linux, is_root, missing_binaries, module_available


def _skip_reason() -> Optional[str]:
    if not is_linux():
        return "needs Linux (run inside the Ubuntu VM)"
    if not is_root():
        return "needs root (run with sudo)"
    if not mininet_available():
        return "Mininet is not importable by this Python (use a venv with --system-site-packages)"
    missing = missing_binaries(["ovs-vsctl", "ovs-ofctl", "tc", "iperf3", "ping"])
    if missing:
        return f"missing programs: {missing}"
    probe = subprocess.run(["ovs-vsctl", "--timeout=5", "show"], capture_output=True, text=True)
    if probe.returncode != 0:
        return "Open vSwitch is not running (sudo systemctl start openvswitch-switch): " + probe.stderr.strip()
    return None


_REASON = _skip_reason()
pytestmark = [pytest.mark.integration, pytest.mark.skipif(_REASON is not None, reason=_REASON or "")]


@pytest.fixture(scope="module")
def net(env):
    with running_network(env.topology, env.links) as network:
        yield network


def _ping_avg_ms(node: Any, target_ip: str, count: int = 5) -> float:
    out = node.cmd(f"ping -c {count} -i 0.2 -q {target_ip}")
    match = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)", out)
    assert match, f"could not parse ping output:\n{out}"
    return float(match.group(2))


def _iperf(net, env, *, protocol, duration=4, rate=None, size=None, port, src="h1", dst="h5"):
    """Run one iperf3 test with the package's own command builders; returns the parsed report."""
    event = TrafficEvent("it-%d" % port, "it", "NORMAL", Tool.IPERF3, protocol, src, dst, 0.0, float(duration),
                         rate_mbps=rate, packet_size=size, port=port)
    server = net.get(dst).popen(cmd.iperf3_server_command(port), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
    try:
        time.sleep(0.7)
        assert server.poll() is None, f"iperf3 server died: {server.communicate()}"
        client = net.get(src).popen(cmd.iperf3_client_command(event, env.topology.host_ip(dst), env.traffic),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        out, err = client.communicate(timeout=duration + 30)
        assert client.returncode == 0, f"iperf3 client failed: {err or out}"
        return cmd.parse_iperf3_json(out)
    finally:
        if server.poll() is None:
            server.terminate()
        server.wait(timeout=5)


# --------------------------------------------------------------- 1. topology
def test_topology_creation(net, env):
    from mininet.node import OVSSwitch

    topo = env.topology
    assert {h.name for h in net.hosts} == set(topo.all_hosts)
    assert {s.name for s in net.switches} == {topo.left_switch, topo.right_switch}
    assert all(isinstance(s, OVSSwitch) for s in net.switches)
    assert len(net.links) == 2 * topo.hosts_per_side + 1  # host links + one trunk
    for name in topo.all_hosts:
        assert net.get(name).IP() == topo.host_ip(name)
    trunks = [l for l in net.links if {l.intf1.node.name, l.intf2.node.name} == {topo.left_switch, topo.right_switch}]
    assert len(trunks) == 1
    assert {trunks[0].intf1.name, trunks[0].intf2.name} == set(bottleneck_interfaces(topo))


# ------------------------------------------------------------ 2. connectivity
def test_host_connectivity(net, env):
    assert net.pingAll() == 0, "pingAll lost packets"
    h = {n: net.get(n) for n in env.topology.all_hosts}
    across = _ping_avg_ms(h["h1"], env.topology.host_ip("h5"))
    same_side = _ping_avg_ms(h["h1"], env.topology.host_ip("h2"))
    expected_rtt = 2 * env.links.bottleneck.delay_ms
    print(f"\nRTT across bottleneck: {across:.1f} ms (expected ~{expected_rtt:g}); same side: {same_side:.2f} ms")
    assert expected_rtt - 2 <= across <= expected_rtt + 40
    assert same_side < 10


# ------------------------------------------------------------ 3. Open vSwitch
def _ovs(*args: str) -> str:
    done = subprocess.run(["ovs-vsctl", "--timeout=5", *args], capture_output=True, text=True)
    assert done.returncode == 0, f"ovs-vsctl {' '.join(args)} failed: {done.stderr}"
    return done.stdout.strip()


def test_open_vswitch_operation(net, env):
    topo = env.topology
    assert set(_ovs("list-br").split()) == {topo.left_switch, topo.right_switch}
    for switch in (topo.left_switch, topo.right_switch):
        assert _ovs("get-fail-mode", switch) == topo.fail_mode
        assert len(_ovs("list-ports", switch).split()) == topo.hosts_per_side + 1
        ofctl = subprocess.run(["ovs-ofctl", "show", switch], capture_output=True, text=True)
        assert ofctl.returncode == 0, ofctl.stderr
    trunk_dev = f"{topo.left_switch}-eth{trunk_port(topo)}"
    assert _ovs("iface-to-br", trunk_dev) == topo.left_switch


# ---------------------------------------------------------------- 4. bottleneck
def test_bottleneck_link_configuration(net, env):
    topo, links = env.topology, env.links
    assert verify_shaping(net, topo, links) == []
    left_dev, right_dev = bottleneck_interfaces(topo)
    for switch, dev in ((topo.left_switch, left_dev), (topo.right_switch, right_dev)):
        state = tc.read_qdisc(node_runner(net.get(switch)), dev)
        print(f"\n{dev}: rate={state.rate_mbps} Mbit delay={state.delay_ms} ms loss={state.loss_pct}% "
              f"burst={state.burst_bytes} B")
        assert state.rate_mbps == pytest.approx(links.bottleneck.bandwidth_mbps, rel=0.01)
        assert state.delay_ms == pytest.approx(links.bottleneck.delay_ms, abs=0.5)
        assert state.loss_pct == pytest.approx(links.bottleneck.loss_pct, abs=0.1)
    access = tc.read_qdisc(node_runner(net.get("h1")), "h1-eth0")
    assert access.rate_mbps == pytest.approx(links.access.bandwidth_mbps, rel=0.01)
    assert access.rate_mbps > links.bottleneck.bandwidth_mbps


def test_bottleneck_limits_tcp_throughput(net, env):
    report = _iperf(net, env, protocol=Protocol.TCP, duration=5, port=5301)
    print(f"\nTCP h1->h5 over bottleneck: {report.mbps:.2f} Mbit/s, retransmits={report.retransmits}")
    cap = env.links.bottleneck.bandwidth_mbps
    assert 0.4 * cap <= report.mbps <= 1.02 * cap


# -------------------------------------------------------- 5. TCP and UDP traffic
def test_udp_below_capacity_is_delivered(net, env):
    report = _iperf(net, env, protocol=Protocol.UDP, duration=4, rate=5.0, size=1200, port=5302)
    print(f"\nUDP 5 Mbit/s: {report.mbps:.2f} Mbit/s, loss={report.lost_percent}%")
    assert report.mbps == pytest.approx(5.0, rel=0.1)
    assert report.lost_percent is not None and report.lost_percent < 3.0


def test_udp_overload_is_limited_by_bottleneck(net, env):
    offered = 2 * env.links.bottleneck.bandwidth_mbps
    report = _iperf(net, env, protocol=Protocol.UDP, duration=4, rate=offered, size=1200, port=5303)
    print(f"\nUDP {offered:g} Mbit/s offered: loss={report.lost_percent}% (expect ~50%)")
    assert report.lost_percent is not None and 35.0 <= report.lost_percent <= 65.0


def test_traffic_executor_runs_all_tools(net, env):
    """Drive a short plan through TrafficExecutor: TCP, UDP, HTTP, DNS, ping (+ hping3 if installed)."""
    fast = replace(env, traffic=replace(env.traffic, executor=ExecutorSettings(server_start_lead_s=2.0, grace_s=15.0)))

    def e(eid, tool, proto, src, dst, **kw):
        return TrafficEvent(eid, "it", "NORMAL", tool, proto, src, dst, kw.pop("start", 0.0), 4.0, **kw)

    events = [
        e("tcp", Tool.IPERF3, Protocol.TCP, "h1", "h5", rate_mbps=2.0, port=5401),
        e("udp", Tool.IPERF3, Protocol.UDP, "h2", "h6", rate_mbps=1.0, packet_size=1200, port=5402, start=0.5),
        e("http", Tool.HTTP, Protocol.HTTP, "h3", "h7", request_rate=4.0, parallel=2, seed=1),
        e("ping", Tool.PING, Protocol.ICMP, "h1", "h6", request_rate=5.0),
    ]
    if module_available("scapy"):
        events.append(e("dns", Tool.DNS, Protocol.DNS, "h4", "h8", request_rate=5.0, seed=2))
    if shutil.which("hping3"):
        events.append(e("hping3", Tool.HPING3, Protocol.UDP, "h2", "h7", rate_mbps=1.0, packet_size=512))

    plan = TrafficPlan("integration", 1, 8.0, tuple(events))
    results = TrafficExecutor(net, plan, fast, python=sys.executable).run()
    for r in results:
        print(f"\n{r.event_id}: {r.status} rc={r.returncode} detail={r.detail} error={r.error}")
    assert [r.event_id for r in results if r.status != "ok"] == []
    by_id = {r.event_id: r for r in results}
    assert by_id["http"].detail["errors"] == 0 and by_id["http"].detail["attempts"] >= 5
    assert by_id["tcp"].detail["bits_per_second"] > 0.5e6
