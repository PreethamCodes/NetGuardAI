"""Configuration validation: YAML loading, topology, links, traffic settings and scenarios."""

from __future__ import annotations

import pytest

from netguard.config import LinkParams, LinksConfig, TopologyConfig, TrafficDefaults, load_environment, load_yaml
from netguard.exceptions import ConfigError
from netguard.scenarios import ScenarioConfig, list_scenarios, load_scenario
from netguard.traffic.planner import build_plan
from tests.conftest import minimal_component, minimal_scenario


# ------------------------------------------------------------------ YAML loading
class TestYamlLoading:
    def test_default_environment_matches_spec(self, env):
        assert env.topology.hosts_per_side == 4
        assert env.links.bottleneck.bandwidth_mbps == 10
        assert env.links.bottleneck.delay_ms == 20
        assert env.links.bottleneck.loss_pct == 0
        assert env.links.access.bandwidth_mbps > env.links.bottleneck.bandwidth_mbps

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_yaml(tmp_path / "nope.yaml")

    def test_malformed_yaml(self, write_yaml):
        with pytest.raises(ConfigError, match="invalid YAML"):
            load_yaml(write_yaml("bad.yaml", "a: [unclosed"))

    def test_top_level_must_be_mapping(self, write_yaml):
        with pytest.raises(ConfigError, match="mapping"):
            load_yaml(write_yaml("list.yaml", "- a\n- b\n"))

    def test_empty_file_is_empty_mapping(self, write_yaml):
        assert load_yaml(write_yaml("empty.yaml", "")) == {}

    def test_environment_error_names_directory(self, tmp_path):
        with pytest.raises(ConfigError, match="topology.yaml"):
            load_environment(tmp_path)


# ---------------------------------------------------------------------- topology
class TestTopologyConfig:
    @pytest.mark.parametrize("n", [3, 4])
    def test_valid_host_counts(self, n):
        topo = TopologyConfig(hosts_per_side=n)
        assert len(topo.left_hosts) == len(topo.right_hosts) == n
        assert set(topo.left_hosts).isdisjoint(topo.right_hosts)

    @pytest.mark.parametrize("n", [0, 1, 2, 5, 8])
    def test_invalid_host_counts(self, n):
        with pytest.raises(ConfigError, match="hosts_per_side"):
            TopologyConfig(hosts_per_side=n)

    def test_host_naming_and_addressing(self):
        topo = TopologyConfig(hosts_per_side=3)
        assert topo.left_hosts == ("h1", "h2", "h3")
        assert topo.right_hosts == ("h4", "h5", "h6")
        assert topo.host_ip("h4") == "10.0.0.4"
        assert topo.host_cidr("h4") == "10.0.0.4/24"
        with pytest.raises(ConfigError):
            topo.host_ip("h7")

    @pytest.mark.parametrize("subnet", ["8.8.8.0/24", "203.0.113.0/24", "not-a-subnet", "10.0.0.1/24", "fd00::/64"])
    def test_rejects_public_or_invalid_subnets(self, subnet):
        with pytest.raises(ConfigError):
            TopologyConfig(subnet=subnet)

    def test_rejects_tiny_subnet(self):
        with pytest.raises(ConfigError, match="too small"):
            TopologyConfig(subnet="10.0.0.0/29")

    def test_switch_names_must_differ(self):
        with pytest.raises(ConfigError):
            TopologyConfig(left_switch="s1", right_switch="s1")

    def test_from_mapping_rejects_unknown_keys(self):
        with pytest.raises(ConfigError, match="unknown key"):
            TopologyConfig.from_mapping({"hosts_per_sdie": 4})

    def test_from_mapping_rejects_non_integer(self):
        with pytest.raises(ConfigError):
            TopologyConfig.from_mapping({"hosts_per_side": 3.5})


# ------------------------------------------------------------------------- links
class TestLinkConfig:
    def test_defaults_are_filled(self):
        link = LinkParams.from_mapping({"bandwidth_mbps": 5}, "x")
        assert (link.delay_ms, link.loss_pct, link.jitter_ms, link.queue_packets) == (0, 0, 0, 50)

    @pytest.mark.parametrize("key,value", [
        ("bandwidth_mbps", 0), ("bandwidth_mbps", -1), ("bandwidth_mbps", "fast"),
        ("delay_ms", -1), ("loss_pct", -0.1), ("loss_pct", 100.5), ("jitter_ms", -2),
        ("queue_packets", 0), ("queue_packets", 2.5), ("mtu", 100),
    ])
    def test_invalid_values(self, key, value):
        data = {"bandwidth_mbps": 10, key: value}
        with pytest.raises(ConfigError):
            LinkParams.from_mapping(data, "x")

    def test_bandwidth_is_required(self):
        with pytest.raises(ConfigError, match="required"):
            LinkParams.from_mapping({"delay_ms": 5}, "x")

    def test_bool_is_not_a_number(self):
        with pytest.raises(ConfigError):
            LinkParams.from_mapping({"bandwidth_mbps": True}, "x")

    def test_access_must_exceed_bottleneck(self):
        slow = LinkParams(bandwidth_mbps=10)
        with pytest.raises(ConfigError, match="remains the bottleneck"):
            LinksConfig(bottleneck=slow, access=slow)

    def test_both_sections_required(self):
        with pytest.raises(ConfigError, match="both"):
            LinksConfig.from_mapping({"bottleneck": {"bandwidth_mbps": 10}})

    def test_overrides_keep_other_fields(self, env):
        links = env.links.with_bottleneck_overrides({"delay_ms": 60, "loss_pct": 3})
        assert links.bottleneck.delay_ms == 60 and links.bottleneck.loss_pct == 3
        assert links.bottleneck.bandwidth_mbps == env.links.bottleneck.bandwidth_mbps
        assert env.links.bottleneck.delay_ms == 20  # original is immutable

    def test_override_cannot_break_bottleneck_rule(self, env):
        with pytest.raises(ConfigError, match="remains the bottleneck"):
            env.links.with_bottleneck_overrides({"bandwidth_mbps": 500})

    def test_derived_tbf_values(self):
        link = LinkParams(bandwidth_mbps=10, queue_packets=50, mtu=1500)
        assert link.rate_kbit == 10_000
        assert link.burst_bytes == 12_500  # 10 ms of data
        assert link.limit_bytes == 75_000

    def test_burst_never_below_two_mtu(self):
        link = LinkParams(bandwidth_mbps=0.1, mtu=1500)
        assert link.burst_bytes == 3000 and link.limit_bytes >= link.burst_bytes


class TestTrafficDefaults:
    def test_shipped_file_loads(self, env):
        assert env.traffic.limits.max_packet_size <= 1472
        assert env.traffic.dns.domains

    def test_empty_mapping_gives_defaults(self):
        assert TrafficDefaults.from_mapping({}).iperf3.base_port == 5201

    def test_unknown_section(self):
        with pytest.raises(ConfigError, match="unknown key"):
            TrafficDefaults.from_mapping({"nmap": {}})

    @pytest.mark.parametrize("section,key,value", [
        ("iperf3", "base_port", 80), ("http", "port", 70000), ("dns", "domains", []),
        ("limits", "max_clients", 0), ("limits", "max_packet_size", 70000), ("executor", "grace_s", -1),
    ])
    def test_invalid_values(self, section, key, value):
        with pytest.raises(ConfigError):
            TrafficDefaults.from_mapping({section: {key: value}})


# --------------------------------------------------------------------- scenarios
class TestShippedScenarios:
    def test_expected_scenarios_exist(self, config_dir):
        names = set(list_scenarios(config_dir))
        assert {"normal", "burst", "periodic", "random", "ramp", "flash_crowd", "udp_flood"} <= names

    @pytest.mark.parametrize("name", list_scenarios())
    def test_loads_and_plans(self, name, env):
        scenario = load_scenario(name)
        assert scenario.name == name
        assert build_plan(scenario, env).events

    def test_unknown_scenario_lists_available(self):
        with pytest.raises(ConfigError, match="available"):
            load_scenario("does-not-exist")

    def test_load_by_path(self, write_yaml):
        path = write_yaml("custom.yaml", minimal_scenario())
        assert load_scenario(path).name == "t"


class TestScenarioValidation:
    @staticmethod
    def parse(*components, **overrides):
        return ScenarioConfig.from_mapping(minimal_scenario(*components, **overrides))

    def test_minimal_is_valid(self):
        assert self.parse().components[0].protocol.value == "tcp"

    def test_missing_components(self):
        with pytest.raises(ConfigError, match="components"):
            ScenarioConfig.from_mapping({"name": "t", "duration_s": 10})

    def test_unknown_scenario_key(self):
        with pytest.raises(ConfigError, match="unknown key"):
            self.parse(colour="red")

    def test_unknown_component_key(self):
        with pytest.raises(ConfigError, match="unknown key"):
            self.parse(minimal_component(rate_mbit=5))

    def test_duplicate_names(self):
        with pytest.raises(ConfigError, match="duplicate"):
            self.parse(minimal_component(), minimal_component())

    @pytest.mark.parametrize("duration", [0, -5, "300"])
    def test_bad_duration(self, duration):
        with pytest.raises(ConfigError):
            self.parse(duration_s=duration)

    def test_invalid_pattern(self):
        with pytest.raises(ConfigError, match="pattern"):
            self.parse(minimal_component(pattern="TSUNAMI"))

    def test_tool_not_allowed_for_pattern(self):
        c = minimal_component(pattern="BURST", tool="ping", protocol="icmp", request_rate=1, rate_mbps=None)
        with pytest.raises(ConfigError, match="supports tools"):
            self.parse(c)

    def test_protocol_incompatible_with_tool(self):
        c = minimal_component(tool="ping", protocol="tcp", rate_mbps=None, request_rate=2)
        with pytest.raises(ConfigError, match="supports"):
            self.parse(c)

    def test_udp_requires_rate(self):
        with pytest.raises(ConfigError, match="rate_mbps"):
            self.parse(minimal_component(protocol="udp", rate_mbps=None))

    def test_unpaced_tcp_is_allowed(self):
        assert self.parse(minimal_component(rate_mbps=None)).components[0].rate_mbps is None

    def test_request_tools_need_request_rate(self):
        with pytest.raises(ConfigError, match="request_rate"):
            self.parse(minimal_component(tool="dns", protocol=None, rate_mbps=None))

    def test_rate_kind_mismatch(self):
        c = minimal_component(tool="http", protocol=None, rate_mbps=5, request_rate=1)
        with pytest.raises(ConfigError, match="not applicable"):
            self.parse(c)

    @pytest.mark.parametrize("selector", ["10.0.0.5", "8.8.8.8", "google.com", "h", "h1;rm", ["h1", "10.0.0.2"]])
    def test_ip_or_arbitrary_destinations_rejected(self, selector):
        with pytest.raises(ConfigError, match="selector"):
            self.parse(minimal_component(dst=selector))

    def test_start_must_be_inside_scenario(self):
        with pytest.raises(ConfigError, match="start_s"):
            self.parse(minimal_component(start_s=500))

    @pytest.mark.parametrize("field,value", [
        ("clients", 0), ("connections", 0), ("rate_mbps", -1), ("packet_size", 0), ("seed", -3), ("duration_s", 0),
    ])
    def test_bad_numeric_fields(self, field, value):
        with pytest.raises(ConfigError):
            self.parse(minimal_component(**{field: value}))

    def test_packet_size_rejected_for_http(self):
        c = minimal_component(tool="http", protocol=None, rate_mbps=None, request_rate=1, packet_size=100)
        with pytest.raises(ConfigError, match="packet_size"):
            self.parse(c)

    def test_connections_rejected_for_ping(self):
        c = minimal_component(tool="ping", protocol=None, rate_mbps=None, request_rate=1, connections=2)
        with pytest.raises(ConfigError, match="connections"):
            self.parse(c)

    def test_udp_flood_requires_udp(self):
        with pytest.raises(ConfigError, match="UDP_FLOOD requires"):
            self.parse(minimal_component(pattern="UDP_FLOOD", protocol="tcp"))

    def test_flash_crowd_requires_tcp(self):
        with pytest.raises(ConfigError, match="TCP"):
            self.parse(minimal_component(pattern="FLASH_CROWD", protocol="udp"))

    def test_burst_needs_interval_for_repeats(self):
        c = minimal_component(pattern="BURST", protocol="udp", options={"burst_count": 3})
        with pytest.raises(ConfigError, match="burst_interval_s"):
            self.parse(c)

    def test_burst_interval_must_cover_burst(self):
        c = minimal_component(pattern="BURST", protocol="udp", burst_interval_s=5,
                              options={"burst_count": 2, "burst_duration_s": 10})
        with pytest.raises(ConfigError, match="burst_duration_s"):
            self.parse(c)

    def test_burst_interval_only_for_burst(self):
        with pytest.raises(ConfigError, match="only valid for pattern BURST"):
            self.parse(minimal_component(burst_interval_s=10))

    def test_duration_rejected_where_unused(self):
        c = minimal_component(pattern="BURST", protocol="udp", duration_s=20)
        with pytest.raises(ConfigError, match="not used by pattern"):
            self.parse(c)

    def test_periodic_requires_period(self):
        with pytest.raises(ConfigError, match="period_s"):
            self.parse(minimal_component(pattern="PERIODIC", protocol="udp"))

    def test_periodic_on_duration_within_period(self):
        c = minimal_component(pattern="PERIODIC", protocol="udp", options={"period_s": 10, "on_duration_s": 20})
        with pytest.raises(ConfigError, match="on_duration_s"):
            self.parse(c)

    def test_random_requires_ranges(self):
        with pytest.raises(ConfigError, match="rate_range_mbps"):
            self.parse(minimal_component(pattern="RANDOM", rate_mbps=None))

    def test_random_takes_rates_from_options_only(self):
        c = minimal_component(pattern="RANDOM", options={"rate_range_mbps": [1, 2], "duration_range_s": [1, 2]})
        with pytest.raises(ConfigError, match="takes its rates from"):
            self.parse(c)

    @pytest.mark.parametrize("bad", [[2, 1], [1], "1-2", [0, 1], [-1, 2]])
    def test_random_range_validation(self, bad):
        c = minimal_component(pattern="RANDOM", rate_mbps=None, protocol="udp",
                              options={"rate_range_mbps": bad, "duration_range_s": [1, 2]})
        with pytest.raises(ConfigError):
            self.parse(c)

    def test_unknown_option_rejected(self):
        with pytest.raises(ConfigError, match="unknown key"):
            self.parse(minimal_component(options={"start_jitter": 3}))

    def test_ramp_load_ordering(self):
        c = minimal_component(pattern="RAMP", protocol="udp", rate_mbps=None,
                              options={"start_load": 1.2, "onset_load": 1.0, "max_load": 1.3})
        with pytest.raises(ConfigError, match="start_load"):
            self.parse(c)

    def test_link_overrides_validated(self):
        with pytest.raises(ConfigError):
            self.parse(link_overrides={"loss_pct": 200})
        with pytest.raises(ConfigError, match="unknown key"):
            self.parse(link_overrides={"speed": 3})
        assert self.parse(link_overrides={"delay_ms": 50}).link_overrides == {"delay_ms": 50}

    def test_with_duration_revalidates(self):
        scenario = self.parse(minimal_component(start_s=100))
        assert scenario.with_duration(200).duration_s == 200
        with pytest.raises(ConfigError, match="start_s"):
            scenario.with_duration(50)

    def test_with_seed(self):
        assert self.parse().with_seed(99).seed == 99
        assert self.parse().with_seed(None).seed == 1
