"""Host-environment checks (read-only; nothing is installed or changed)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from typing import Iterable, List

from netguard.traffic.events import TrafficPlan
from netguard.traffic.types import Tool

_TOOL_BINARIES = {Tool.IPERF3: "iperf3", Tool.PING: "ping", Tool.HPING3: "hping3"}


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def missing_binaries(names: Iterable[str]) -> List[str]:
    """Names from ``names`` that are not on ``PATH``."""
    return sorted({n for n in names if shutil.which(n) is None})


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def required_binaries(plan: TrafficPlan) -> List[str]:
    """Executables needed to run ``plan`` (topology tools plus the tools it uses)."""
    names = ["tc", "ovs-vsctl", "ip"]
    names += [_TOOL_BINARIES[t] for t in plan.tools_used() if t in _TOOL_BINARIES]
    if any(t in (Tool.HTTP, Tool.DNS) for t in plan.tools_used()):
        names.append("env")
    return names


def preflight(plan: TrafficPlan) -> List[str]:
    """Problems that would prevent ``plan`` from running here (empty list = OK)."""
    problems: List[str] = []
    if not is_linux():
        problems.append("Mininet requires Linux; run this inside the Ubuntu VM")
        return problems
    if not is_root():
        problems.append("root privileges are required (run with sudo)")
    problems += [f"required program not found: {b}" for b in missing_binaries(required_binaries(plan))]
    if not module_available("mininet"):
        problems.append("Python package 'mininet' is not importable (check python3-mininet / system-site-packages)")
    if Tool.DNS in plan.tools_used() and not module_available("scapy"):
        problems.append("Python package 'scapy' is required for DNS traffic (pip install -r requirements.txt)")
    return problems
