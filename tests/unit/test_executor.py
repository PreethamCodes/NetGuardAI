"""TrafficExecutor logic with fake Mininet hosts and fake processes (nothing is executed)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional

import pytest

from netguard.config.environment import ExecutorSettings
from netguard.exceptions import TrafficError
from netguard.traffic.events import TrafficEvent, TrafficPlan
from netguard.traffic.executor import TrafficExecutor
from netguard.traffic.types import Protocol, Tool

IPERF_UDP = json.dumps({"start": {"test_start": {"protocol": "UDP"}},
                        "end": {"sum": {"bits_per_second": 5e6, "lost_percent": 0.0, "jitter_ms": 0.1}}})
HTTP_SUMMARY = json.dumps({"attempts": 3, "errors": 0, "successes": 3})


class FakeProc:
    def __init__(self, argv: List[str], net: "FakeNet", host: str) -> None:
        self.argv, self.net, self.host = argv, net, host
        self.returncode: Optional[int] = None
        self.terminated = False
        self.killed = False
        joined = " ".join(argv)
        self.is_server = ("iperf3" in argv and "-s" in argv) or "http_server" in joined or "dns_server" in joined
        self.tool = "iperf3" if argv[0] == "iperf3" else ("http" if "http_client" in joined else (
            "dns" if "dns_client" in joined else argv[0]))
        if not self.is_server and self.tool not in net.hang_tools:
            self.returncode = net.client_rc.get(self.tool, 0)
        if self.is_server and net.server_dies:
            self.returncode = 1

    def poll(self) -> Optional[int]:
        return self.returncode

    def communicate(self, timeout: Optional[float] = None):
        if self.tool in self.net.hang_tools and not self.killed:
            raise subprocess.TimeoutExpired(self.argv, timeout or 0)
        if self.is_server:
            return "", "Address already in use" if self.returncode else ""
        out = {"iperf3": IPERF_UDP, "http": HTTP_SUMMARY, "dns": HTTP_SUMMARY}.get(self.tool, "")
        err = "client failed" if self.returncode else ""
        return out, err

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15 if self.returncode is None else self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeNode:
    def __init__(self, name: str, ip: str, net: "FakeNet") -> None:
        self.name, self._ip, self.net = name, ip, net

    def IP(self) -> str:  # noqa: N802 - Mininet API
        return self.net.ip_overrides.get(self.name, self._ip)

    def popen(self, argv: List[str], **kwargs: Any) -> FakeProc:
        proc = FakeProc(list(argv), self.net, self.name)
        self.net.procs.append(proc)
        return proc


class FakeNet:
    def __init__(self, topology) -> None:
        self.nodes = {h: FakeNode(h, topology.host_ip(h), self) for h in topology.all_hosts}
        self.procs: List[FakeProc] = []
        self.client_rc: Dict[str, int] = {}
        self.hang_tools: set = set()
        self.server_dies = False
        self.ip_overrides: Dict[str, str] = {}

    def get(self, name: str) -> FakeNode:
        return self.nodes[name]


def ev(eid: str, tool=Tool.IPERF3, protocol=Protocol.UDP, start=0.0, dur=1.0, src="h1", dst="h5", **kw) -> TrafficEvent:
    defaults: Dict[str, Any] = {}
    if tool is Tool.IPERF3:
        defaults.update(rate_mbps=5.0, port=5201)
    elif tool in (Tool.HTTP, Tool.DNS, Tool.PING):
        defaults.update(request_rate=2.0)
    defaults.update(kw)
    return TrafficEvent(eid, "c", "NORMAL", tool, protocol, src, dst, start, dur, **defaults)


@pytest.fixture
def fast_env(env):
    return replace(env, traffic=replace(env.traffic, executor=ExecutorSettings(server_start_lead_s=0.05, grace_s=0.5)))


def run_plan(fast_env, events, net=None, duration=3.0):
    net = net or FakeNet(fast_env.topology)
    plan = TrafficPlan("t", 1, duration, tuple(events))
    return net, TrafficExecutor(net, plan, fast_env, python="python3").run()


class TestExecutor:
    def test_all_events_complete(self, fast_env):
        events = [
            ev("e1", port=5201),
            ev("e2", tool=Tool.HTTP, protocol=Protocol.HTTP, start=0.2, dst="h6"),
            ev("e3", tool=Tool.PING, protocol=Protocol.ICMP, start=0.1, dst="h7"),
            ev("e4", tool=Tool.DNS, protocol=Protocol.DNS, start=0.15, dst="h8"),
        ]
        net, results = run_plan(fast_env, events)
        assert [r.status for r in results] == ["ok"] * 4
        assert results[0].detail["bits_per_second"] == 5e6
        assert results[1].detail["successes"] == 3

    def test_server_starts_before_client_and_everything_is_cleaned_up(self, fast_env):
        net, _ = run_plan(fast_env, [ev("e1", port=5300)])
        order = [(p.host, p.is_server) for p in net.procs]
        assert order.index(("h5", True)) < order.index(("h1", False))
        iperf_server = next(p for p in net.procs if p.is_server)
        assert iperf_server.argv == ["iperf3", "-s", "-p", "5300", "-1"]
        assert all(p.terminated for p in net.procs if p.is_server)

    def test_http_and_dns_servers_started_on_destinations_only_once(self, fast_env):
        events = [ev("a", tool=Tool.HTTP, protocol=Protocol.HTTP, dst="h5"),
                  ev("b", tool=Tool.HTTP, protocol=Protocol.HTTP, dst="h5", src="h2"),
                  ev("c", tool=Tool.DNS, protocol=Protocol.DNS, dst="h6")]
        net, results = run_plan(fast_env, events)
        servers = [(p.host, "http" if "http_server" in " ".join(p.argv) else "dns") for p in net.procs if p.is_server]
        assert sorted(servers) == [("h5", "http"), ("h6", "dns")]
        assert all(r.status == "ok" for r in results)

    def test_client_runs_on_source_and_targets_destination_ip(self, fast_env):
        net, _ = run_plan(fast_env, [ev("e1", src="h2", dst="h7")])
        client = next(p for p in net.procs if not p.is_server)
        assert client.host == "h2" and "10.0.0.7" in client.argv

    def test_client_failure_is_reported_not_hidden(self, fast_env):
        net = FakeNet(fast_env.topology)
        net.client_rc["ping"] = 1
        events = [ev("ok", port=5201), ev("bad", tool=Tool.PING, protocol=Protocol.ICMP, dst="h6")]
        _, results = run_plan(fast_env, events, net)
        by_id = {r.event_id: r for r in results}
        assert by_id["ok"].status == "ok"
        assert by_id["bad"].status == "failed" and by_id["bad"].returncode == 1 and by_id["bad"].error

    def test_iperf_server_that_cannot_start_fails_the_event(self, fast_env):
        net = FakeNet(fast_env.topology)
        net.server_dies = True
        _, results = run_plan(fast_env, [ev("e1")], net)
        assert results[0].status == "failed" and "exited" in results[0].error
        assert not any(not p.is_server for p in net.procs)  # client was never launched

    def test_overrunning_client_is_killed(self, fast_env):
        net = FakeNet(fast_env.topology)
        net.hang_tools.add("iperf3")
        _, results = run_plan(fast_env, [ev("e1")], net)
        assert results[0].status == "failed" and "still running" in results[0].error
        assert any(p.killed for p in net.procs)

    def test_wrong_host_ip_aborts_before_starting_anything(self, fast_env):
        net = FakeNet(fast_env.topology)
        net.ip_overrides["h5"] = "8.8.8.8"
        with pytest.raises(TrafficError, match="expected 10.0.0.5"):
            run_plan(fast_env, [ev("e1")], net)
        assert net.procs == []

    def test_host_outside_private_subnet_is_rejected(self, fast_env):
        net = FakeNet(fast_env.topology)
        net.nodes["h5"]._ip = "8.8.8.8"
        net.ip_overrides["h5"] = "8.8.8.8"
        executor = TrafficExecutor(net, TrafficPlan("t", 1, 3, (ev("e1"),)), fast_env)
        with pytest.raises(TrafficError):
            executor.check_isolation()

    def test_stop_skips_pending_events_and_returns_promptly(self, fast_env):
        net = FakeNet(fast_env.topology)
        plan = TrafficPlan("t", 1, 30, (ev("now"), ev("later", start=20.0, port=5202)))
        executor = TrafficExecutor(net, plan, fast_env, python="python3")
        holder: Dict[str, Any] = {}
        thread = threading.Thread(target=lambda: holder.setdefault("r", executor.run()))
        started = time.monotonic()
        thread.start()
        time.sleep(0.5)
        executor.stop()
        thread.join(10)
        assert not thread.is_alive() and time.monotonic() - started < 8
        statuses = {r.event_id: r.status for r in holder["r"]}
        assert statuses["now"] == "ok" and statuses["later"] == "skipped"

    def test_results_never_invent_success(self, fast_env):
        net = FakeNet(fast_env.topology)
        executor = TrafficExecutor(net, TrafficPlan("t", 1, 3, (ev("e1"),)), fast_env)
        assert [r.status for r in executor.results()] == ["skipped"]
