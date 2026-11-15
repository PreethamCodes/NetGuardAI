"""Build the full traffic plan of a scenario."""

from __future__ import annotations

import logging
import random
from dataclasses import replace
from typing import List

from netguard.config.environment import Environment
from netguard.exceptions import TrafficError
from netguard.scenarios.models import ScenarioConfig
from netguard.traffic.events import TrafficEvent, TrafficPlan, validate_event
from netguard.traffic.patterns import PATTERN_PLANNERS, PlanContext
from netguard.traffic.types import Tool

logger = logging.getLogger(__name__)


def effective_environment(scenario: ScenarioConfig, env: Environment) -> Environment:
    """``env`` with the scenario's ``link_overrides`` applied to the bottleneck."""
    return env.with_link_overrides(scenario.link_overrides)


def build_plan(scenario: ScenarioConfig, env: Environment) -> TrafficPlan:
    """Expand ``scenario`` into a deterministic, validated list of events.

    The same scenario, seed and configuration always produce the same plan.
    """
    env = effective_environment(scenario, env)
    limits = env.traffic.limits
    ctx = PlanContext(scenario, env.topology, env.links.bottleneck.bandwidth_mbps)

    drafts: List[TrafficEvent] = []
    for comp in scenario.components:
        if comp.clients > limits.max_clients:
            raise TrafficError(f"component {comp.name!r}: clients={comp.clients} exceeds limit {limits.max_clients}")
        seed = comp.seed if comp.seed is not None else scenario.seed
        rng = random.Random(f"{seed}:{comp.name}")  # str seeding is stable across runs
        planned = PATTERN_PLANNERS[comp.pattern](comp, ctx, rng)
        logger.debug("component %s (%s): %d events", comp.name, comp.pattern.value, len(planned))
        drafts.extend(planned)

    drafts.sort(key=lambda e: (e.start_s, e.component, e.src, e.dst, e.duration_s))
    if len(drafts) > limits.max_events:
        raise TrafficError(f"plan has {len(drafts)} events, above the limit of {limits.max_events}")

    events: List[TrafficEvent] = []
    next_port = env.traffic.iperf3.base_port
    for index, draft in enumerate(drafts):
        port = None
        if draft.tool is Tool.IPERF3:
            port, next_port = next_port, next_port + 1
        event = replace(draft, event_id=f"{scenario.name}-{index:04d}", port=port)
        validate_event(event, limits, env.topology)
        events.append(event)
    if next_port > 65535:
        raise TrafficError("not enough TCP ports for the iperf3 servers of this plan")

    plan = TrafficPlan(scenario.name, scenario.seed, scenario.duration_s, tuple(events))
    logger.info("Planned %d events for scenario %r (seed %d, %.0f s)", len(events), plan.scenario, plan.seed, plan.duration_s)
    return plan
