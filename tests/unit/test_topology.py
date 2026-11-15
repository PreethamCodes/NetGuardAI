"""Topology layout and tc command construction (no Mininet needed)."""

from __future__ import annotations

from typing import List, Sequence, Tuple

import pytest

from netguard.config import LinkParams, LinksConfig, TopologyConfig
from netguard.exceptions import ConfigError, TopologyError
from netguard.topology import tc
from netguard.topology.dumbbell import (
    bottleneck_interfaces,
    build_network,
    mininet_available,
    shaping_targets,
    trunk_port,
)

BOTTLENECK = LinkParams(bandwidth_mbps=10, delay_ms=20)


# ---------------------------------------------------------------------- layout
class TestLayout:
    @pytest.mark.parametrize("n", [3, 4])
    def test_trunk_port_follows_host_ports(self, n):
        topo = TopologyConfig(hosts_per_side=n)
        assert trunk_port(topo) == n + 1
        assert bottleneck_interfaces(topo) == (f"s1-eth{n + 1}", f"s2-eth{n + 1}")

    @pytest.mark.parametrize("n", [3, 4])
    def test_shaping_targets_cover_every_link_end(self, env, n):
        topo = TopologyConfig(hosts_per_side=n)
        targets = shaping_targets(topo, env.links)
        assert len(targets) == 2 * (2 * n) + 2  # both ends of each host link + both trunk ends
        assert len({t.dev for t in targets}) == len(targets)

    def test_only_trunk_gets_bottleneck_parameters(self, env):
        targets = shaping_targets(env.topology, env.links)
        bottleneck = [t for t in targets if t.role == "bottleneck"]
        assert sorted(t.dev for t in bottleneck) == sorted(bottleneck_interfaces(env.topology))
        assert all(t.link == env.links.bottleneck for t in bottleneck)
        assert all(t.link == env.links.access for t in targets if t.role == "access")

    def test_access_is_never_the_bottleneck(self, env):
        for t in shaping_targets(env.topology, env.links):
            if t.role == "access":
                assert t.link.bandwidth_mbps > env.links.bottleneck.bandwidth_mbps

    def test_hosts_are_split_across_both_switches(self, env):
        targets = shaping_targets(env.topology, env.links)
        left = {t.node for t in targets if t.node in env.topology.left_hosts}
        right = {t.node for t in targets if t.node in env.topology.right_hosts}
        assert left == set(env.topology.left_hosts) and right == set(env.topology.right_hosts)
        assert {t.node for t in targets if t.node.startswith("s")} == {"s1", "s2"}

    def test_scenario_overrides_reach_the_trunk(self, env):
        links = env.links.with_bottleneck_overrides({"delay_ms": 60, "loss_pct": 3})
        trunk = [t for t in shaping_targets(env.topology, links) if t.role == "bottleneck"]
        assert {(t.link.delay_ms, t.link.loss_pct) for t in trunk} == {(60.0, 3.0)}

    @pytest.mark.skipif(mininet_available(), reason="Mininet is installed here")
    def test_build_network_without_mininet_raises_clear_error(self, env):
        with pytest.raises(TopologyError, match="Mininet is not importable"):
            build_network(env.topology, env.links)


# ------------------------------------------------------------------ tc commands
class TestQdiscCommands:
    def test_default_bottleneck_commands(self):
        netem, tbf = tc.build_qdisc_commands("s1-eth5", BOTTLENECK)
        assert netem == ["tc", "qdisc", "add", "dev", "s1-eth5", "root", "handle", "1:", "netem",
                         "limit", "1000", "delay", "20ms"]
        assert tbf == ["tc", "qdisc", "add", "dev", "s1-eth5", "parent", "1:1", "handle", "10:", "tbf",
                       "rate", "10000kbit", "burst", "12500", "limit", "75000"]

    def test_loss_and_jitter_are_added(self):
        link = LinkParams(bandwidth_mbps=10, delay_ms=60, jitter_ms=5, loss_pct=2.5)
        netem = tc.build_qdisc_commands("d", link)[0]
        assert netem[-5:] == ["delay", "60ms", "5ms", "loss", "2.5%"]

    def test_no_delay_no_loss_gives_plain_netem(self):
        netem = tc.build_qdisc_commands("d", LinkParams(bandwidth_mbps=100))[0]
        assert "delay" not in netem and "loss" not in netem

    def test_fractional_values_are_formatted_cleanly(self):
        link = LinkParams(bandwidth_mbps=2.5, delay_ms=0.5, loss_pct=0.1)
        netem, tbf = tc.build_qdisc_commands("d", link)
        assert "0.5ms" in netem and "0.1%" in netem and "2500kbit" in tbf

    def test_netem_queue_grows_with_bdp(self):
        big = LinkParams(bandwidth_mbps=1000, delay_ms=200, mtu=1500)
        assert tc.netem_limit_packets(big) > tc.MIN_NETEM_LIMIT
        assert tc.netem_limit_packets(BOTTLENECK) == tc.MIN_NETEM_LIMIT

    def test_tbf_burst_covers_mtu_and_limit_covers_burst(self):
        for bw in (0.1, 1, 10, 100, 1000):
            link = LinkParams(bandwidth_mbps=bw, queue_packets=1)
            assert link.burst_bytes >= link.mtu
            assert link.limit_bytes >= link.burst_bytes


class FakeRun:
    """Records commands; ``fail_on`` makes matching commands return rc=2."""

    def __init__(self, fail_on: Sequence[str] = ()) -> None:
        self.calls: List[List[str]] = []
        self.fail_on = tuple(fail_on)

    def __call__(self, argv: Sequence[str]) -> Tuple[int, str]:
        self.calls.append(list(argv))
        if any(token in argv for token in self.fail_on):
            return 2, "RTNETLINK answers: error"
        return 0, ""


class TestApplyLink:
    def test_command_order(self):
        run = FakeRun()
        tc.apply_link(run, "s1-eth5", BOTTLENECK)
        programs = [c[:3] for c in run.calls]
        assert programs[0][0] == "ethtool"
        assert programs[1] == ["tc", "qdisc", "del"]
        assert programs[2][2] == programs[3][2] == "add"
        assert "netem" in run.calls[2] and "tbf" in run.calls[3]

    def test_offload_can_be_disabled(self):
        run = FakeRun()
        tc.apply_link(run, "d", BOTTLENECK, disable_offload=False)
        assert all(c[0] == "tc" for c in run.calls)

    def test_offload_failure_is_not_fatal(self):
        run = FakeRun(fail_on=["ethtool"])
        tc.apply_link(run, "d", BOTTLENECK)  # must not raise
        assert any("tbf" in c for c in run.calls)

    def test_reset_failure_is_ignored(self):
        tc.apply_link(FakeRun(fail_on=["del"]), "d", BOTTLENECK)

    @pytest.mark.parametrize("qdisc", ["netem", "tbf"])
    def test_qdisc_failure_raises(self, qdisc):
        with pytest.raises(TopologyError, match="command failed"):
            tc.apply_link(FakeRun(fail_on=[qdisc]), "d", BOTTLENECK)


# ------------------------------------------------------------ parsing / checking
# Hand-written samples in the format printed by iproute2's ``tc qdisc show``.
SHOW_OK = """\
qdisc netem 1: root refcnt 2 limit 1000 delay 20ms
qdisc tbf 10: parent 1:1 rate 10Mbit burst 12500b lat 60ms
"""
SHOW_LOSSY = """\
qdisc netem 1: root refcnt 2 limit 1000 delay 60ms  5ms loss 3%
qdisc tbf 10: parent 1:1 rate 10Mbit burst 12500b lat 60ms
"""


class TestQdiscParsing:
    def test_parse_default(self):
        s = tc.parse_qdisc_show(SHOW_OK)
        assert s.has_netem and s.has_tbf
        assert s.delay_ms == 20 and s.loss_pct == 0
        assert s.rate_mbps == pytest.approx(10)
        assert s.burst_bytes == 12500

    def test_parse_loss_jitter_and_units(self):
        s = tc.parse_qdisc_show(SHOW_LOSSY)
        assert (s.delay_ms, s.jitter_ms, s.loss_pct) == (60, 5, 3)
        assert tc.parse_qdisc_show("qdisc netem 1: root delay 500us").delay_ms == 0.5
        assert tc.parse_qdisc_show("qdisc tbf 10: rate 512Kbit burst 32Kb lat 50ms").rate_mbps == pytest.approx(0.512)
        assert tc.parse_qdisc_show("qdisc tbf 10: rate 1Gbit burst 32Kb").rate_mbps == 1000

    def test_parse_empty_and_foreign_qdisc(self):
        s = tc.parse_qdisc_show("qdisc noqueue 0: root refcnt 2")
        assert not s.has_netem and not s.has_tbf

    def test_check_accepts_matching_state(self):
        assert tc.check_link_state(tc.parse_qdisc_show(SHOW_OK), BOTTLENECK) == []
        lossy = LinkParams(bandwidth_mbps=10, delay_ms=60, jitter_ms=5, loss_pct=3)
        assert tc.check_link_state(tc.parse_qdisc_show(SHOW_LOSSY), lossy) == []

    def test_check_reports_each_mismatch(self):
        wrong = LinkParams(bandwidth_mbps=20, delay_ms=50, loss_pct=1)
        problems = tc.check_link_state(tc.parse_qdisc_show(SHOW_OK), wrong)
        assert len(problems) == 3
        assert any("rate" in p for p in problems) and any("delay" in p for p in problems)

    def test_check_reports_missing_qdiscs(self):
        problems = tc.check_link_state(tc.parse_qdisc_show(""), BOTTLENECK)
        assert "no netem qdisc found" in problems and "no tbf qdisc found" in problems

    def test_read_qdisc_error(self):
        with pytest.raises(TopologyError):
            tc.read_qdisc(FakeRun(fail_on=["show"]), "d")

    def test_read_qdisc_roundtrip(self):
        def run(argv):
            return 0, SHOW_OK
        assert tc.read_qdisc(run, "d").rate_mbps == pytest.approx(10)


class TestTopologyParameterValidation:
    """Parameter validation that matters for the topology (spec: 3-4 hosts per side, bottleneck rule)."""

    def test_links_config_from_mapping_roundtrip(self):
        cfg = LinksConfig.from_mapping({
            "bottleneck": {"bandwidth_mbps": 10, "delay_ms": 20},
            "access": {"bandwidth_mbps": 100},
        })
        assert cfg.bottleneck.delay_ms == 20 and cfg.disable_offload is True

    def test_unknown_link_parameter(self):
        with pytest.raises(ConfigError, match="unknown key"):
            LinksConfig.from_mapping({"bottleneck": {"bandwidth_mbps": 10, "latency": 5},
                                      "access": {"bandwidth_mbps": 100}})
