"""Mininet / Open vSwitch lifecycle of one experiment: clean start, guaranteed clean end.

``mn -c`` semantics are used (``mininet.clean.cleanup``): it removes **every** Open vSwitch
bridge and Mininet veth/namespace on the machine, which is what the dedicated VM
expects (the README already tells users to run ``sudo mn -c`` after an interruption).
Cleanup runs before an experiment, after it (also after failures and Ctrl-C), and the
result is verified and stored in the experiment metadata.
"""

from __future__ import annotations

import logging
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, List, Optional

from netguard.config.environment import Environment
from netguard.exceptions import TopologyError
from netguard.topology.dumbbell import running_network

logger = logging.getLogger(__name__)

_LEFTOVER_IFACE = re.compile(r"^(s\d+|h\d+)-eth\d+(@\S+)?$")


@dataclass
class CleanupReport:
    ran: bool
    leftovers: List[str]

    @property
    def clean(self) -> bool:
        return self.ran and not self.leftovers

    def to_dict(self) -> dict:
        return {"ran": self.ran, "clean": self.clean, "leftovers": self.leftovers}


def mininet_cleanup() -> None:
    """Equivalent of ``sudo mn -c``."""
    from mininet.clean import cleanup

    cleanup()


def find_leftovers() -> List[str]:
    """Mininet remnants still present on the host (empty list = clean)."""
    leftovers: List[str] = []
    done = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True)
    for line in done.stdout.splitlines():
        parts = line.split(": ")
        if len(parts) > 1 and _LEFTOVER_IFACE.match(parts[1].strip()):
            leftovers.append(f"interface {parts[1].split('@')[0]}")
    done = subprocess.run(["ovs-vsctl", "--timeout=5", "list-br"], capture_output=True, text=True)
    if done.returncode == 0:
        leftovers += [f"ovs bridge {b}" for b in done.stdout.split()]
    done = subprocess.run(["pgrep", "-f", "mnexec"], capture_output=True, text=True)
    if done.returncode == 0 and done.stdout.split():
        leftovers.append(f"mnexec processes {done.stdout.split()}")
    return leftovers


class MininetLifecycle:
    """Builds, shapes and tears down the dumbbell; the cleanup functions are injectable for tests."""

    def __init__(self, env: Environment, *, cleanup: Callable[[], None] = mininet_cleanup,
                 leftovers: Callable[[], List[str]] = find_leftovers,
                 network_context: Optional[Callable[..., Any]] = None) -> None:
        self.env = env
        self._cleanup = cleanup
        self._leftovers = leftovers
        self._network_context = network_context or running_network
        self.last_report: Optional[CleanupReport] = None

    def cleanup(self) -> CleanupReport:
        ran = True
        try:
            self._cleanup()
        except Exception as exc:  # noqa: BLE001 - reported, then verified below
            logger.error("mininet cleanup raised %s: %s", type(exc).__name__, exc)
            ran = False
        report = CleanupReport(ran, self._leftovers())
        if report.leftovers:
            logger.warning("leftovers after cleanup: %s", report.leftovers)
        self.last_report = report
        return report

    @contextmanager
    def network(self) -> Iterator[Any]:
        """Clean, start and shape the network; always stop and clean afterwards."""
        pre = self.cleanup()
        if not pre.clean:
            raise TopologyError(f"could not obtain a clean Mininet state: {pre.leftovers}")
        try:
            with self._network_context(self.env.topology, self.env.links) as net:
                yield net
        finally:
            self.cleanup()
