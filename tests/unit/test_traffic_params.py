"""Traffic generator parameter validation and pattern behaviour (planning only)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from netguard.config import Environment, TopologyConfig, TrafficDefaults
from netguard.exceptions import ConfigError, TrafficError
from netguard.scenarios import ScenarioConfig, load_scenario
from netguard.traffic.events import TrafficEvent, validate_event
from netguard.traffic.patterns import ramp_load_fraction
from netguard.traffic.planner import build_plan
from netguard.traffic.types import Protocol, Tool
from tests.conftest import minimal_component, minimal_scenario

CAPACITY = 10.0


def plan_for(env, *components, duration=120, seed=1, **kw):
    return build_plan(ScenarioConfig.from_mapping(minimal_scenario(*components, duration_s=duration, seed=seed, **kw)), env)


def event(**kw) -> TrafficEvent:
    base = dict(event_id="e", component="c", pattern="NORMAL", tool=Tool.IPERF3, protocol=Protocol.TCP,
                src="h1", dst="h5", start_s=0.0, duration_s=10.0, port=5201)
    base.update(kw)
    return TrafficEvent(**base)


# ------------------------------------------------------------ event validation
class TestEventValidation:
    def check(self, env, **kw):
        validate_event(event(**kw), env.traffic.limits, env.topology)

    def test_valid_event(self, env):
        self.check(env, rate_mbps=2)

    @pytest.mark.parametrize("kw,msg", [
        (dict(src="h99"), "unknown source"),
        (dict(dst="10.0.0.5"), "unknown destination"),
        (dict(dst="h1"), "must differ"),
        (dict(src="h5", dst="h6", rate_mbps=1, start_s=-1), "start_s"),
        (dict(duration_s=0), "duration_s"),
        (dict(parallel=0), "parallel"),
        (dict(parallel=1000), "parallel"),
        (dict(rate_mbps=0), "rate_mbps"),
        (dict(rate_mbps=1000), "rate_mbps"),
        (dict(protocol=Protocol.UDP), "rate_mbps is required"),
        (dict(rate_mbps=1, request_rate=2), "not request_rate"),
        (dict(rate_mbps=1, packet_size=8), "packet_size"),
        (dict(rate_mbps=1, packet_size=9000), "packet_size"),
        (dict(rate_mbps=1, protocol=Protocol.DNS), "cannot generate"),
        (dict(tool=Tool.HPING3, protocol=Protocol.UDP), "rate_mbps is required"),
        (dict(tool=Tool.HPING3, protocol=Protocol.UDP, rate_mbps=100, packet_size=16), "hping3 would need"),
        (dict(tool=Tool.HTTP, protocol=Protocol.HTTP, rate_mbps=1), "request_rate"),
        (dict(tool=Tool.DNS, protocol=Protocol.DNS), "request_rate"),
        (dict(tool=Tool.DNS, protocol=Protocol.DNS, request_rate=10_000), "request_rate"),
        (dict(tool=Tool.PING, protocol=Protocol.ICMP, request_rate=1, rate_mbps=1), "not rate_mbps"),
    ])
    def test_rejects(self, env, kw, msg):
        with pytest.raises(TrafficError, match=msg):
            self.check(env, **kw)

    def test_limits_come_from_configuration(self, env):
        strict = TrafficDefaults.from_mapping({"limits": {"max_rate_mbps": 5}})
        with pytest.raises(TrafficError, match="rate_mbps"):
            validate_event(event(rate_mbps=6), strict.limits, env.topology)


class TestPlannerLimits:
    def test_clients_limit(self, env):
        tight = Environment(env.topology, env.links, TrafficDefaults.from_mapping({"limits": {"max_clients": 2}}))
        with pytest.raises(TrafficError, match="clients"):
            plan_for(tight, minimal_component(clients=3))

    def test_event_count_limit(self, env):
        tight = Environment(env.topology, env.links, TrafficDefaults.from_mapping({"limits": {"max_events": 5}}))
        with pytest.raises(TrafficError, match="above the limit"):
            plan_for(tight, minimal_component(clients=6))

    def test_rate_limit_applies_to_plan(self, env):
        with pytest.raises(TrafficError, match="rate_mbps"):
            plan_for(env, minimal_component(rate_mbps=500))

    def test_unknown_host_in_scenario(self, env):
        with pytest.raises(ConfigError, match="does not exist"):
            plan_for(env, minimal_component(dst="h9"))

    def test_three_host_topology_hosts_are_respected(self, env):
        env3 = Environment(TopologyConfig(hosts_per_side=3), env.links, env.traffic)
        plan = plan_for(env3, minimal_component(clients=6))
        assert {e.src for e in plan.events} <= {"h1", "h2", "h3"}
        assert {e.dst for e in plan.events} <= {"h4", "h5", "h6"}
        with pytest.raises(ConfigError, match="does not exist"):
            plan_for(env3, minimal_component(dst="h8"))

    def test_source_never_equals_destination(self, env):
        plan = plan_for(env, minimal_component(src="all", dst="all", clients=16))
        assert all(e.src != e.dst for e in plan.events)

    def test_single_host_loop_rejected(self, env):
        with pytest.raises(ConfigError, match="different from source"):
            plan_for(env, minimal_component(src="h1", dst="h1"))


# ------------------------------------------------------------- plan invariants
@pytest.mark.parametrize("name", ["normal", "burst", "periodic", "random", "ramp", "flash_crowd", "udp_flood",
                                  "udp_flood_hping3", "degraded"])
class TestPlanInvariants:
    def test_invariants(self, name, env):
        scenario = load_scenario(name)
        plan = build_plan(scenario, env)
        ids = [e.event_id for e in plan.events]
        assert len(set(ids)) == len(ids)
        assert [e.start_s for e in plan.events] == sorted(e.start_s for e in plan.events)
        ports = [e.port for e in plan.events if e.tool is Tool.IPERF3]
        assert len(set(ports)) == len(ports) and all(p is not None and 1024 <= p < 65536 for p in ports)
        for e in plan.events:
            assert 0 <= e.start_s and e.end_s <= scenario.duration_s + 1e-6
            assert e.duration_s >= 1.0

    def test_same_seed_same_plan(self, name, env):
        a = build_plan(load_scenario(name), env)
        b = build_plan(load_scenario(name), env)
        assert a.to_dict() == b.to_dict()


class TestReproducibility:
    def test_random_seed_controls_plan(self, env):
        s = load_scenario("random")
        a = build_plan(s, env)
        assert a.to_dict() == build_plan(s, env).to_dict()
        assert a.to_dict() != build_plan(s.with_seed(s.seed + 1), env).to_dict()

    def test_component_seed_overrides_scenario_seed(self, env):
        comp = dict(pattern="RANDOM", protocol=None, rate_mbps=None, clients=2,
                    options={"rate_range_mbps": [0.5, 2], "duration_range_s": [3, 8]})
        a = plan_for(env, minimal_component(seed=5, **comp), seed=1)
        b = plan_for(env, minimal_component(seed=5, **comp), seed=2)
        c = plan_for(env, minimal_component(seed=6, **comp), seed=1)
        assert [e.to_dict() for e in a.events] == [e.to_dict() for e in b.events]
        assert [e.to_dict() for e in a.events] != [e.to_dict() for e in c.events]

    def test_random_respects_ranges(self, env):
        s = load_scenario("random")
        plan = build_plan(s, env)
        flows = [e for e in plan.events if e.component == "random-flows"]
        assert len(flows) > 5
        for e in flows:
            assert 0.2 <= e.rate_mbps <= 3.0
            assert e.duration_s <= 25.0 + 1e-6
            assert e.protocol in (Protocol.TCP, Protocol.UDP)
        assert {e.protocol for e in flows} == {Protocol.TCP, Protocol.UDP}
        assert len({(e.src, e.dst) for e in flows}) > 3  # varied peers
        # a slot never overlaps itself
        for src in {e.src for e in flows}:
            mine = sorted((e for e in flows if e.src == src), key=lambda e: e.start_s)
            assert all(a.end_s <= b.start_s + 1e-6 for a, b in zip(mine, mine[1:]))


# ------------------------------------------------------------ pattern behaviour
class TestNormal:
    def test_mix_covers_all_protocols(self, env):
        plan = build_plan(load_scenario("normal"), env)
        assert {e.tool for e in plan.events} == {Tool.IPERF3, Tool.HTTP, Tool.DNS, Tool.PING}
        assert {e.protocol for e in plan.events} >= {Protocol.TCP, Protocol.UDP, Protocol.HTTP, Protocol.DNS, Protocol.ICMP}

    def test_low_to_medium_load(self, env):
        plan = build_plan(load_scenario("normal"), env)
        peak = max(plan.offered_load_mbps(t) for t in range(0, 300, 5))
        assert 0 < peak < 0.5 * CAPACITY

    def test_start_jitter_bounds(self, env):
        plan = plan_for(env, minimal_component(clients=4, options={"start_jitter_s": 7}))
        assert all(0 <= e.start_s <= 7 for e in plan.events)
        assert len({e.start_s for e in plan.events}) > 1

    def test_explicit_duration(self, env):
        plan = plan_for(env, minimal_component(duration_s=30, options={"start_jitter_s": 0}))
        assert [e.duration_s for e in plan.events] == [30.0]


class TestBurst:
    def comp(self, **kw):
        base = dict(pattern="BURST", protocol="udp", rate_mbps=4.0, clients=2, start_s=20, burst_interval_s=40,
                    options={"base_rate_mbps": 0.5, "burst_duration_s": 10, "burst_count": 3})
        base.update(kw)
        return minimal_component(**base)

    def test_burst_times_and_count(self, env):
        plan = plan_for(env, self.comp(), duration=200)
        bursts = [e for e in plan.events if e.rate_mbps == 4.0]
        assert sorted({e.start_s for e in bursts}) == [20, 60, 100]
        assert all(e.duration_s == 10 for e in bursts) and len(bursts) == 6

    def test_load_rises_then_returns_to_base(self, env):
        plan = plan_for(env, self.comp(), duration=200)
        assert plan.offered_load_mbps(5) == pytest.approx(1.0)  # base: 2 clients x 0.5
        assert plan.offered_load_mbps(25) == pytest.approx(9.0)  # base + 2 x 4.0
        assert plan.offered_load_mbps(35) == pytest.approx(1.0)  # reduced again

    def test_no_base_load(self, env):
        plan = plan_for(env, self.comp(options={"base_rate_mbps": 0, "burst_duration_s": 10, "burst_count": 1}), duration=100)
        assert plan.offered_load_mbps(5) == 0 and plan.offered_load_mbps(25) > 0

    def test_bursts_beyond_scenario_end_are_dropped(self, env):
        plan = plan_for(env, self.comp(), duration=70)
        assert sorted({e.start_s for e in plan.events if e.rate_mbps == 4.0}) == [20, 60]
        assert all(e.end_s <= 70 for e in plan.events)


class TestPeriodic:
    def test_recurrence(self, env):
        c = minimal_component(pattern="PERIODIC", protocol="udp", rate_mbps=2, clients=1, start_s=10,
                              options={"period_s": 30, "on_duration_s": 5})
        plan = plan_for(env, c, duration=100)
        assert [e.start_s for e in plan.events] == [10, 40, 70]
        assert all(e.duration_s == 5 for e in plan.events)

    def test_last_event_is_clipped(self, env):
        c = minimal_component(pattern="PERIODIC", protocol="udp", rate_mbps=2, options={"period_s": 30, "on_duration_s": 20})
        plan = plan_for(env, c, duration=100)
        assert plan.events[-1].end_s == 100


class TestRamp:
    def test_load_fraction_function(self):
        f = lambda t: ramp_load_fraction(t, 0.1, 1.05, 1.3, 60)  # noqa: E731
        assert f(0) == pytest.approx(0.1)
        assert f(60) == pytest.approx(1.05)
        assert f(30) == pytest.approx(0.575)
        assert f(1000) == 1.3  # capped

    def test_congestion_onset_near_60_seconds(self, env):
        plan = build_plan(load_scenario("ramp"), env)
        assert plan.offered_load_mbps(0) == pytest.approx(0.1 * CAPACITY, abs=0.05)
        assert plan.offered_load_mbps(55) < CAPACITY  # not yet over capacity
        assert plan.offered_load_mbps(60) > CAPACITY  # over capacity at the target onset
        first_over = min(t for t in range(0, 300) if plan.offered_load_mbps(t) > CAPACITY)
        assert first_over == 60

    def test_load_is_monotonic_and_capped(self, env):
        plan = build_plan(load_scenario("ramp"), env)
        loads = [plan.offered_load_mbps(t) for t in range(0, 300)]
        assert all(b >= a - 1e-6 for a, b in zip(loads, loads[1:]))
        assert max(loads) == pytest.approx(1.3 * CAPACITY, abs=0.05)

    def test_only_a_few_flows_active_at_once(self, env):
        plan = build_plan(load_scenario("ramp"), env)
        for t in (1, 33, 61, 150):
            assert sum(1 for e in plan.events if e.start_s <= t < e.end_s) == 4

    def test_scales_with_bottleneck_capacity(self, env):
        s = ScenarioConfig.from_mapping({**_ramp_mapping(), "link_overrides": {"bandwidth_mbps": 20}})
        plan = build_plan(s, env)
        assert plan.offered_load_mbps(60) == pytest.approx(1.05 * 20, abs=0.1)

    def test_off_grid_onset_is_still_a_boundary(self, env):
        c = minimal_component(pattern="RAMP", protocol="udp", rate_mbps=None, clients=1,
                              options={"onset_s": 62, "step_s": 5})
        plan = plan_for(env, c, duration=100)
        assert plan.offered_load_mbps(62) == pytest.approx(1.05 * CAPACITY, abs=0.05)


def _ramp_mapping():
    from netguard.config import load_yaml
    from netguard.config.loader import DEFAULT_CONFIG_DIR
    return load_yaml(DEFAULT_CONFIG_DIR / "scenarios" / "ramp.yaml")


class TestFlashCrowd:
    def test_clients_arrive_together_and_leave_together(self, env):
        plan = build_plan(load_scenario("flash_crowd"), env)
        crowd = [e for e in plan.events if e.pattern == "FLASH_CROWD"]
        assert len(crowd) == 20  # 12 http + 8 tcp
        assert all(100 <= e.start_s <= 103 for e in crowd)
        assert {round(e.end_s, 2) for e in crowd} == {160.0}
        assert all(e.protocol in (Protocol.HTTP, Protocol.TCP) for e in crowd)

    def test_connections_multiply(self, env):
        plan = build_plan(load_scenario("flash_crowd"), env)
        tcp = [e for e in plan.events if e.component == "crowd-tcp"]
        assert all(e.parallel == 3 for e in tcp)
        assert sum(e.parallel for e in tcp) == 24  # 8 clients x 3 connections

    def test_sudden_increase(self, env):
        plan = build_plan(load_scenario("flash_crowd"), env)
        active = lambda t: sum(1 for e in plan.events if e.start_s <= t < e.end_s)  # noqa: E731
        assert active(104) >= active(90) + 20


class TestUdpFlood:
    def test_parameters_are_passed_through(self, env):
        plan = build_plan(load_scenario("udp_flood"), env)
        flood = [e for e in plan.events if e.pattern == "UDP_FLOOD"]
        assert len(flood) == 1
        e = flood[0]
        assert (e.src, e.dst, e.start_s, e.duration_s, e.rate_mbps, e.packet_size) == ("h1", "h5", 120, 60, 20, 1200)
        assert e.protocol is Protocol.UDP

    def test_hping3_variant(self, env):
        plan = build_plan(load_scenario("udp_flood_hping3"), env)
        assert any(e.tool is Tool.HPING3 for e in plan.events)

    def test_multiple_clients(self, env):
        c = minimal_component(pattern="UDP_FLOOD", protocol="udp", rate_mbps=5, clients=3, start_s=10, duration_s=20)
        plan = plan_for(env, c)
        assert len(plan.events) == 3 and len({e.src for e in plan.events}) == 3

    def test_rate_cap_applies_to_floods(self, env):
        c = minimal_component(pattern="UDP_FLOOD", protocol="udp", rate_mbps=5000, duration_s=20)
        with pytest.raises(TrafficError, match="rate_mbps"):
            plan_for(env, c)


class TestDegraded:
    def test_scenario_degrades_only_the_link(self, env):
        s = load_scenario("degraded")
        assert s.link_overrides == {"delay_ms": 60, "loss_pct": 3}
        assert env.links.bottleneck.loss_pct == 0  # shipped default is untouched
        plan = build_plan(s, env)
        peak = max(plan.offered_load_mbps(t) for t in range(0, 300, 5))
        assert peak < 0.5 * CAPACITY  # degradation without high load
