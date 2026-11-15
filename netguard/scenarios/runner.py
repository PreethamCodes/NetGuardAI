"""Run one scenario end to end: plan -> topology -> traffic -> teardown.

This is intentionally *not* the experiment orchestrator (Phase 2): it records
no dataset, labels or collector output; it only builds the network, runs the
planned traffic once and tears everything down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from netguard.config.environment import Environment
from netguard.exceptions import NetGuardError, TopologyError
from netguard.scenarios.models import ScenarioConfig
from netguard.topology.dumbbell import running_network
from netguard.traffic.events import TrafficPlan
from netguard.traffic.executor import EventResult, TrafficExecutor
from netguard.traffic.planner import build_plan, effective_environment
from netguard.utils.system import preflight

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
    plan: TrafficPlan
    results: List[EventResult]

    @property
    def ok(self) -> bool:
        return all(r.status == "ok" for r in self.results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "results": [r.__dict__ for r in self.results],
        }


def check_connectivity(net: Any, tolerate_loss: bool) -> None:
    """All-pairs ping. With random link loss configured a lossy result is only a warning."""
    loss = net.pingAll()
    if loss > 0:
        message = f"pingAll dropped {loss:.0f}% of probes"
        if tolerate_loss:
            logger.warning("%s (bottleneck loss is configured, continuing)", message)
        else:
            raise TopologyError(message)
    else:
        logger.info("pingAll: 0%% dropped")


def run_scenario(scenario: ScenarioConfig, env: Environment, *, check_ping: bool = True) -> RunSummary:
    """Execute ``scenario`` once. Requires root, Mininet and Open vSwitch."""
    plan = build_plan(scenario, env)  # fail on bad configuration before touching the system
    problems = preflight(plan)
    if problems:
        raise NetGuardError("pre-flight checks failed:\n  - " + "\n  - ".join(problems))

    eff = effective_environment(scenario, env)
    with running_network(eff.topology, eff.links) as net:
        if check_ping:
            check_connectivity(net, tolerate_loss=eff.links.bottleneck.loss_pct > 0)
        executor = TrafficExecutor(net, plan, eff)
        try:
            results = executor.run()
        except KeyboardInterrupt:
            logger.warning("interrupted: stopping traffic and tearing down the network")
            executor.stop()
            raise
    return RunSummary(plan, results)
