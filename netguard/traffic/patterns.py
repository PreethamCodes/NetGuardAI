"""Traffic patterns: turn a scenario component into timed traffic events.

All planners are pure functions of ``(component, context, rng)``. With the
same seed they return identical events, which makes experiments reproducible
and testable without Mininet.

Rate conventions: ``rate_mbps`` / ``request_rate`` of a component are *per
client*. RAMP is the exception: its load options are fractions of the
bottleneck capacity and describe the *aggregate* offered load.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from netguard.config.environment import TopologyConfig
from netguard.exceptions import ConfigError
from netguard.scenarios.models import ScenarioConfig, TrafficComponent
from netguard.traffic.events import MIN_EVENT_S, TrafficEvent
from netguard.traffic.types import Pattern, Protocol, Tool


@dataclass(frozen=True)
class PlanContext:
    """Everything a pattern planner needs besides the component itself."""

    scenario: ScenarioConfig
    topology: TopologyConfig
    bottleneck_mbps: float

    @property
    def end_s(self) -> float:
        return self.scenario.duration_s


# ----------------------------------------------------------------------- helpers
def resolve_hosts(selectors: Sequence[str], topology: TopologyConfig, where: str) -> List[str]:
    """Expand ``left`` / ``right`` / ``all`` / ``hN`` selectors to host names."""
    hosts: List[str] = []
    for sel in selectors:
        if sel == "left":
            group = list(topology.left_hosts)
        elif sel == "right":
            group = list(topology.right_hosts)
        elif sel == "all":
            group = list(topology.all_hosts)
        elif sel in topology.all_hosts:
            group = [sel]
        else:
            raise ConfigError(f"{where}: host {sel!r} does not exist; topology hosts are {list(topology.all_hosts)}")
        hosts.extend(h for h in group if h not in hosts)
    return hosts


def make_pairs(comp: TrafficComponent, ctx: PlanContext, n: int) -> List[Tuple[str, str]]:
    """Deterministic (src, dst) pairs for ``n`` clients; never src == dst."""
    srcs = resolve_hosts(comp.src, ctx.topology, f"component {comp.name!r} src")
    dsts = resolve_hosts(comp.dst, ctx.topology, f"component {comp.name!r} dst")
    pairs: List[Tuple[str, str]] = []
    for i in range(n):
        src = srcs[i % len(srcs)]
        candidates = [dsts[(i + k) % len(dsts)] for k in range(len(dsts))]
        dst = next((d for d in candidates if d != src), None)
        if dst is None:
            raise ConfigError(f"component {comp.name!r}: no destination different from source {src}")
        pairs.append((src, dst))
    return pairs


def _clip(start: float, duration: Optional[float], end: float) -> float:
    """Duration limited to the scenario end; ``None`` means "until the end"."""
    available = end - start
    return round(available if duration is None else min(duration, available), 3)


def _draft(
    comp: TrafficComponent, rng: random.Random, src: str, dst: str, start: float, duration: float,
    *, rate_mbps: Optional[float] = None, request_rate: Optional[float] = None,
    protocol: Optional[Protocol] = None, parallel: Optional[int] = None,
) -> TrafficEvent:
    """Create an event without id/port (the planner assigns those)."""
    return TrafficEvent(
        event_id="", component=comp.name, pattern=comp.pattern.value, tool=comp.tool,
        protocol=protocol or comp.protocol, src=src, dst=dst,
        start_s=round(start, 3), duration_s=round(duration, 3),
        rate_mbps=None if rate_mbps is None else round(rate_mbps, 3),
        request_rate=request_rate, packet_size=comp.packet_size,
        parallel=parallel or comp.connections, seed=rng.randrange(2 ** 31),
    )


def _uniform_rate(comp: TrafficComponent) -> Dict[str, Optional[float]]:
    return {"rate_mbps": comp.rate_mbps, "request_rate": comp.request_rate}


# ---------------------------------------------------------------------- planners
def plan_normal(comp: TrafficComponent, ctx: PlanContext, rng: random.Random) -> List[TrafficEvent]:
    """Steady flows; each client starts at a random offset (``start_jitter_s``)."""
    events = []
    for src, dst in make_pairs(comp, ctx, comp.clients):
        start = comp.start_s + rng.uniform(0.0, comp.options["start_jitter_s"])
        if start >= ctx.end_s - MIN_EVENT_S:
            continue
        duration = _clip(start, comp.duration_s, ctx.end_s)
        events.append(_draft(comp, rng, src, dst, start, duration, **_uniform_rate(comp)))
    return events


def plan_burst(comp: TrafficComponent, ctx: PlanContext, rng: random.Random) -> List[TrafficEvent]:
    """Optional steady base load plus ``burst_count`` bursts every ``burst_interval_s``.

    ``rate_mbps`` is the burst rate per client; the base load (``base_rate_mbps``
    per client) keeps running before, between and after bursts, so the load
    drops back to the base level once a burst ends.
    """
    events = []
    pairs = make_pairs(comp, ctx, comp.clients)
    base = comp.options["base_rate_mbps"]
    if base > 0:
        for src, dst in pairs:
            events.append(_draft(comp, rng, src, dst, 0.0, _clip(0.0, None, ctx.end_s), rate_mbps=base))
    for k in range(comp.options["burst_count"]):
        start = comp.start_s + k * (comp.burst_interval_s or 0.0)
        if start >= ctx.end_s - MIN_EVENT_S:
            break
        duration = _clip(start, comp.options["burst_duration_s"], ctx.end_s)
        for src, dst in pairs:
            events.append(_draft(comp, rng, src, dst, start, duration, **_uniform_rate(comp)))
    return events


def plan_periodic(comp: TrafficComponent, ctx: PlanContext, rng: random.Random) -> List[TrafficEvent]:
    """Identical transfers of ``on_duration_s`` repeated every ``period_s``."""
    events = []
    pairs = make_pairs(comp, ctx, comp.clients)
    period, on = comp.options["period_s"], comp.options["on_duration_s"]
    k = 0
    while True:
        start = comp.start_s + k * period
        if start >= ctx.end_s - MIN_EVENT_S:
            break
        duration = _clip(start, on, ctx.end_s)
        for src, dst in pairs:
            events.append(_draft(comp, rng, src, dst, start, duration, **_uniform_rate(comp)))
        k += 1
    return events


def plan_random(comp: TrafficComponent, ctx: PlanContext, rng: random.Random) -> List[TrafficEvent]:
    """Each client slot runs random flows (random rate, duration, protocol, peer).

    A slot alternates between a flow and a random gap until the scenario ends.
    """
    o = comp.options
    srcs = resolve_hosts(comp.src, ctx.topology, f"component {comp.name!r} src")
    dsts = resolve_hosts(comp.dst, ctx.topology, f"component {comp.name!r} dst")
    events = []
    for slot in range(comp.clients):
        src = srcs[slot % len(srcs)]
        peers = [d for d in dsts if d != src]
        if not peers:
            raise ConfigError(f"component {comp.name!r}: no destination different from source {src}")
        t = comp.start_s + rng.uniform(*o["gap_range_s"])
        while t < ctx.end_s - MIN_EVENT_S:
            duration = _clip(t, rng.uniform(*o["duration_range_s"]), ctx.end_s)
            rate = rng.uniform(*o["rate_range_mbps"])
            protocol = Protocol(rng.choice(o["protocols"]))
            dst = rng.choice(peers)
            events.append(_draft(comp, rng, src, dst, t, duration, rate_mbps=rate, protocol=protocol))
            t += duration + rng.uniform(*o["gap_range_s"])
    return events


def ramp_load_fraction(rel_t: float, start_load: float, onset_load: float, max_load: float, onset_s: float) -> float:
    """Aggregate offered load (fraction of capacity) ``rel_t`` seconds into the ramp.

    Linear from ``start_load`` at 0 to ``onset_load`` at ``onset_s``, continuing
    with the same slope and then held at ``max_load``.
    """
    slope = (onset_load - start_load) / onset_s
    return min(max_load, start_load + slope * rel_t)


def plan_ramp(comp: TrafficComponent, ctx: PlanContext, rng: random.Random) -> List[TrafficEvent]:
    """Staircase approximation of a linear ramp that crosses capacity at ``onset_s``.

    Every ``step_s`` the aggregate load is raised; each step is realised by
    ``clients`` flows that each carry an equal share. Only ``clients`` flows
    are active at any moment, which keeps the VM CPU load low.
    """
    o = comp.options
    step, onset = o["step_s"], o["onset_s"]
    pairs = make_pairs(comp, ctx, comp.clients)
    span = ctx.end_s - comp.start_s
    times = {k * step for k in range(int(span // step) + 1)}
    times.add(onset)
    boundaries = sorted(t for t in times if t < span)
    events = []
    for i, rel in enumerate(boundaries):
        next_rel = boundaries[i + 1] if i + 1 < len(boundaries) else span
        if next_rel - rel < MIN_EVENT_S:
            continue  # sliver between an off-grid onset and the next step
        fraction = ramp_load_fraction(rel, o["start_load"], o["onset_load"], o["max_load"], onset)
        per_client = fraction * ctx.bottleneck_mbps / comp.clients
        if per_client < 0.001:
            continue
        for src, dst in pairs:
            events.append(_draft(comp, rng, src, dst, comp.start_s + rel, next_rel - rel, rate_mbps=per_client))
    return events


def plan_flash_crowd(comp: TrafficComponent, ctx: PlanContext, rng: random.Random) -> List[TrafficEvent]:
    """Many legitimate TCP clients arriving within ``spread_s`` and leaving together."""
    crowd_end = comp.start_s + _clip(comp.start_s, comp.duration_s, ctx.end_s)
    spread = comp.options["spread_s"]
    events = []
    for src, dst in make_pairs(comp, ctx, comp.clients):
        start = comp.start_s + rng.uniform(0.0, spread)
        duration = round(crowd_end - start, 3)
        if duration < MIN_EVENT_S:
            continue
        events.append(_draft(comp, rng, src, dst, start, duration, **_uniform_rate(comp)))
    return events


def plan_udp_flood(comp: TrafficComponent, ctx: PlanContext, rng: random.Random) -> List[TrafficEvent]:
    """Controlled, rate-limited UDP streams between Mininet hosts only."""
    duration = _clip(comp.start_s, comp.duration_s, ctx.end_s)
    if duration < MIN_EVENT_S:
        return []
    return [
        _draft(comp, rng, src, dst, comp.start_s, duration, **_uniform_rate(comp))
        for src, dst in make_pairs(comp, ctx, comp.clients)
    ]


PATTERN_PLANNERS: Dict[Pattern, Callable[[TrafficComponent, PlanContext, random.Random], List[TrafficEvent]]] = {
    Pattern.NORMAL: plan_normal,
    Pattern.BURST: plan_burst,
    Pattern.PERIODIC: plan_periodic,
    Pattern.RANDOM: plan_random,
    Pattern.RAMP: plan_ramp,
    Pattern.FLASH_CROWD: plan_flash_crowd,
    Pattern.UDP_FLOOD: plan_udp_flood,
}
