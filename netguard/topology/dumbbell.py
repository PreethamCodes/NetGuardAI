"""Dumbbell topology for Mininet.

    h1 --\\                         /-- h5
    h2 ---- s1 ===(bottleneck)=== s2 ---- h6
    h3 --/                         \\-- h7      (hosts_per_side = 3..4)

* ``s1`` / ``s2`` are Open vSwitch bridges in ``standalone`` fail mode, i.e.
  plain learning switches. No SDN controller is needed or started.
* Hosts connect to their switch over *access* links (default 100 Mbit/s).
* The only inter-switch link is the *bottleneck* (default 10 Mbit/s, 20 ms
  one-way delay in each direction, so ~40 ms RTT). Rate, delay and loss are
  applied with Linux tc (netem + tbf), see :mod:`netguard.topology.tc`.

Mininet is imported lazily, so this module can be imported (and its pure
helpers tested) on machines without Mininet. Building the network requires
Linux, root privileges, Mininet and Open vSwitch.
"""

from __future__ import annotations

import logging
import re
import shlex
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, List, Sequence, Tuple

from netguard.config.environment import LinkParams, LinksConfig, TopologyConfig
from netguard.exceptions import TopologyError
from netguard.topology import tc

logger = logging.getLogger(__name__)

try:  # Mininet only exists inside the Ubuntu VM
    from mininet.topo import Topo as _TopoBase
except ImportError:  # pragma: no cover - exercised on Windows / CI without Mininet
    _TopoBase = object  # type: ignore[assignment,misc]


def mininet_available() -> bool:
    """True if the Mininet Python package can be imported."""
    try:
        import mininet.net  # noqa: F401
    except ImportError:
        return False
    return True


def _require_mininet() -> None:
    if not mininet_available():
        raise TopologyError(
            "Mininet is not importable. Run this inside the Ubuntu VM where Mininet is installed "
            "(see README: 'Running inside Ubuntu')."
        )


# --------------------------------------------------------------- pure layout logic
@dataclass(frozen=True)
class ShapedInterface:
    """An interface that receives tc shaping, and the role of its link."""

    node: str
    dev: str
    link: LinkParams
    role: str  # "access" | "bottleneck"


def trunk_port(topology: TopologyConfig) -> int:
    """Switch port number of the inter-switch link (after all host ports)."""
    return topology.hosts_per_side + 1


def bottleneck_interfaces(topology: TopologyConfig) -> Tuple[str, str]:
    """Interface names of the two ends of the bottleneck link."""
    port = trunk_port(topology)
    return f"{topology.left_switch}-eth{port}", f"{topology.right_switch}-eth{port}"


def shaping_targets(topology: TopologyConfig, links: LinksConfig) -> List[ShapedInterface]:
    """Every interface to shape, with the parameters it must get.

    Shaping both ends of each link shapes both directions (tc only limits
    egress traffic).
    """
    targets: List[ShapedInterface] = []
    sides = ((topology.left_switch, topology.left_hosts), (topology.right_switch, topology.right_hosts))
    for switch, hosts in sides:
        for port, host in enumerate(hosts, start=1):
            targets.append(ShapedInterface(host, f"{host}-eth0", links.access, "access"))
            targets.append(ShapedInterface(switch, f"{switch}-eth{port}", links.access, "access"))
    left_dev, right_dev = bottleneck_interfaces(topology)
    targets.append(ShapedInterface(topology.left_switch, left_dev, links.bottleneck, "bottleneck"))
    targets.append(ShapedInterface(topology.right_switch, right_dev, links.bottleneck, "bottleneck"))
    return targets


# ---------------------------------------------------------------- Mininet topology
class DumbbellTopo(_TopoBase):  # type: ignore[misc,valid-type]
    """Mininet ``Topo`` describing the dumbbell (no tc shaping, see :func:`apply_shaping`)."""

    def build(self, topology: TopologyConfig, links: LinksConfig) -> None:  # noqa: D102
        _require_mininet()
        left = self.addSwitch(topology.left_switch, failMode=topology.fail_mode)
        right = self.addSwitch(topology.right_switch, failMode=topology.fail_mode)
        for switch, hosts in ((left, topology.left_hosts), (right, topology.right_hosts)):
            for port, name in enumerate(hosts, start=1):
                host = self.addHost(name, ip=topology.host_cidr(name))
                self.addLink(host, switch, port2=port)
        port = trunk_port(topology)
        self.addLink(left, right, port1=port, port2=port)


def build_network(topology: TopologyConfig, links: LinksConfig) -> Any:
    """Create (but do not start) a Mininet network for the dumbbell.

    Plain ``Link`` objects are used on purpose: all shaping is done by this
    package with explicit tc commands instead of Mininet's TCLink.
    """
    _require_mininet()
    from mininet.link import Link
    from mininet.net import Mininet
    from mininet.node import OVSSwitch

    topo = DumbbellTopo(topology=topology, links=links)
    return Mininet(
        topo=topo, switch=OVSSwitch, controller=None, link=Link,
        autoSetMacs=True, waitConnected=False,
    )


def node_runner(node: Any) -> tc.Runner:
    """Adapt a Mininet node to the ``run(argv) -> (rc, output)`` callable used by tc.py."""

    def run(argv: Sequence[str]) -> Tuple[int, str]:
        out = node.cmd(shlex.join(list(argv)) + "; echo __rc=$?")
        match = re.search(r"__rc=(\d+)\s*$", out)
        if not match:
            return 1, out
        return int(match.group(1)), out[: match.start()]

    return run


def apply_shaping(net: Any, topology: TopologyConfig, links: LinksConfig) -> None:
    """Apply netem+tbf to every link of a started network."""
    for target in shaping_targets(topology, links):
        node = net.get(target.node)
        logger.info(
            "tc %s %s: %.3f Mbit/s, delay %.1f ms, loss %.2f %%",
            target.role, target.dev, target.link.bandwidth_mbps, target.link.delay_ms, target.link.loss_pct,
        )
        tc.apply_link(node_runner(node), target.dev, target.link, disable_offload=links.disable_offload)


def verify_shaping(net: Any, topology: TopologyConfig, links: LinksConfig) -> List[str]:
    """Read the qdiscs back from the kernel; returns a list of problems (empty = OK)."""
    problems: List[str] = []
    for target in shaping_targets(topology, links):
        state = tc.read_qdisc(node_runner(net.get(target.node)), target.dev)
        problems += [f"{target.dev} ({target.role}): {p}" for p in tc.check_link_state(state, target.link)]
    return problems


def start_network(topology: TopologyConfig, links: LinksConfig) -> Any:
    """Build, start and shape the network; the caller must call ``net.stop()``."""
    net = build_network(topology, links)
    try:
        net.start()
        apply_shaping(net, topology, links)
        problems = verify_shaping(net, topology, links)
        if problems:
            raise TopologyError("tc configuration does not match the request:\n  " + "\n  ".join(problems))
    except BaseException:
        net.stop()
        raise
    return net


@contextmanager
def running_network(topology: TopologyConfig, links: LinksConfig) -> Iterator[Any]:
    """Context manager: started + shaped network, always stopped on exit."""
    net = start_network(topology, links)
    try:
        yield net
    finally:
        net.stop()
