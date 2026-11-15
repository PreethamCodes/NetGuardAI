"""Command line interface.

    python -m netguard validate                 # check all YAML files (any OS)
    python -m netguard list                     # list scenarios (any OS)
    python -m netguard plan ramp [--events]     # dry run: show the traffic plan (any OS)
    sudo python -m netguard topology --pingall  # build the dumbbell (Ubuntu VM, root)
    sudo python -m netguard run ramp            # run one scenario (Ubuntu VM, root)
    sudo python -m netguard experiment run      # Phase 2 campaign: collectors + Parquet dataset (Ubuntu VM, root)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional, Sequence

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_environment
from netguard.exceptions import NetGuardError
from netguard.scenarios.loader import list_scenarios, load_scenario
from netguard.scenarios.models import ScenarioConfig
from netguard.traffic.events import TrafficPlan
from netguard.traffic.planner import build_plan, effective_environment
from netguard.utils.logging_setup import configure_logging

logger = logging.getLogger("netguard.cli")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="netguard", description="NetGuard AI - simulation (Phase 1) and experiment orchestration (Phase 2)")
    p.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR), help="directory with the YAML files")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--log-file", default=None, help="also write logs to this file")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate all YAML configuration files")
    sub.add_parser("list", help="list available scenarios")

    def link_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--bandwidth-mbps", type=float, default=None, help="override the bottleneck bandwidth")
        sp.add_argument("--delay-ms", type=float, default=None, help="override the bottleneck one-way delay")
        sp.add_argument("--loss-pct", type=float, default=None, help="override the bottleneck packet loss (percent)")

    def scenario_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("scenario", help="scenario name (configs/scenarios/<name>.yaml) or a YAML path")
        sp.add_argument("--seed", type=int, default=None, help="override the scenario seed")
        sp.add_argument("--duration", type=float, default=None, help="override duration_s (seconds)")
        link_args(sp)

    sp = sub.add_parser("plan", help="show the traffic plan without running anything")
    scenario_args(sp)
    sp.add_argument("--events", action="store_true", help="list every event")
    sp.add_argument("--json", action="store_true", help="print the plan as JSON")

    sp = sub.add_parser("run", help="run a scenario in Mininet (Ubuntu VM, root)")
    scenario_args(sp)
    sp.add_argument("--no-ping-check", action="store_true", help="skip the initial pingAll")
    sp.add_argument("--results-json", default=None, help="write plan + per-event outcomes to this file")

    sp = sub.add_parser("topology", help="build the dumbbell and inspect it (Ubuntu VM, root)")
    link_args(sp)
    sp.add_argument("--pingall", action="store_true", help="run pingAll")
    sp.add_argument("--cli", action="store_true", help="open the Mininet CLI afterwards")

    from netguard.experiments.cli import register  # Phase 2 sub-commands

    register(sub)

    from netguard.prep.cli import register as register_prep  # Phase 3 sub-command

    register_prep(sub)

    from netguard.ml.cli import register as register_ml  # Phase 4/5 sub-command

    register_ml(sub)
    return p


def _describe_plan(plan: TrafficPlan, show_events: bool) -> str:
    lines = [f"scenario {plan.scenario!r}: seed={plan.seed} duration={plan.duration_s:g}s events={len(plan.events)}"]
    per_component = Counter((e.component, e.pattern, e.tool.value, e.protocol.value) for e in plan.events)
    for (component, pattern, tool, proto), count in sorted(per_component.items()):
        lines.append(f"  {component:<18} {pattern:<12} {tool:<7} {proto:<5} x{count}")
    step = 10 if plan.duration_s <= 300 else 30
    loads = [f"{t}s:{plan.offered_load_mbps(t):.1f}" for t in range(0, int(plan.duration_s), step)]
    lines.append("planned paced load, Mbit/s (unpaced TCP not counted): " + " ".join(loads))
    if show_events:
        for e in plan.events:
            rate = f"{e.rate_mbps:g}Mbps" if e.rate_mbps is not None else (
                f"{e.request_rate:g}/s" if e.request_rate is not None else "unpaced")
            lines.append(f"  {e.event_id} t={e.start_s:7.2f}+{e.duration_s:<7g} {e.src}->{e.dst} "
                         f"{e.tool.value}/{e.protocol.value} {rate} x{e.parallel}")
    return "\n".join(lines)


def _cli_link_overrides(args: argparse.Namespace) -> Dict[str, float]:
    """Bottleneck overrides given on the command line (take precedence over the scenario's)."""
    pairs = (("bandwidth_mbps", args.bandwidth_mbps), ("delay_ms", args.delay_ms), ("loss_pct", args.loss_pct))
    return {key: value for key, value in pairs if value is not None}


def _load_scenario(args: argparse.Namespace) -> ScenarioConfig:
    scenario = load_scenario(args.scenario, args.config_dir).with_duration(args.duration).with_seed(args.seed)
    overrides = _cli_link_overrides(args)
    if overrides:
        scenario = replace(scenario, link_overrides={**scenario.link_overrides, **overrides})
        build_env = load_environment(args.config_dir)
        effective_environment(scenario, build_env)  # validate now: clear error before anything runs
    return scenario


def cmd_validate(args: argparse.Namespace) -> int:
    env = load_environment(args.config_dir)
    print(f"environment OK: {env.describe()}")
    names = list_scenarios(args.config_dir)
    for name in names:
        scenario = load_scenario(name, args.config_dir)
        plan = build_plan(scenario, env)
        print(f"scenario OK: {name:<22} {len(plan.events):>4} events, {scenario.duration_s:g} s")
    print(f"{len(names)} scenario(s) validated")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for name in list_scenarios(args.config_dir):
        print(f"{name:<24} {load_scenario(name, args.config_dir).description}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    env = load_environment(args.config_dir)
    plan = build_plan(_load_scenario(args), env)
    print(json.dumps(plan.to_dict(), indent=2) if args.json else _describe_plan(plan, args.events))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from netguard.scenarios.runner import run_scenario  # needs a Linux host with Mininet

    env = load_environment(args.config_dir)
    summary = run_scenario(_load_scenario(args), env, check_ping=not args.no_ping_check)
    counts = Counter(r.status for r in summary.results)
    print(f"events: {dict(counts)}")
    for r in summary.results:
        if r.status != "ok":
            print(f"  {r.event_id}: {r.status} {r.error or ''}")
    if args.results_json:
        Path(args.results_json).write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
        print(f"wrote {args.results_json}")
    return 0 if summary.ok else 1


def cmd_topology(args: argparse.Namespace) -> int:
    from netguard.topology.dumbbell import running_network
    from netguard.utils.system import is_linux, is_root

    if not is_linux() or not is_root():
        raise NetGuardError("the topology command needs Linux and root privileges (use sudo in the Ubuntu VM)")
    env = load_environment(args.config_dir).with_link_overrides(_cli_link_overrides(args))
    with running_network(env.topology, env.links) as net:
        print("topology started; tc configuration verified against links.yaml")
        if args.pingall:
            loss = net.pingAll()
            print(f"pingAll dropped {loss:.0f}%")
        if args.cli:
            from mininet.cli import CLI
            CLI(net)
    return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    from netguard.experiments.cli import cmd_experiment as handler

    return handler(args)


def cmd_dataset(args: argparse.Namespace) -> int:
    from netguard.experiments.cli import cmd_dataset as handler

    return handler(args)


def cmd_prepare(args: argparse.Namespace) -> int:
    from netguard.prep.cli import cmd_prepare as handler

    return handler(args)


def cmd_train(args: argparse.Namespace) -> int:
    from netguard.ml.cli import cmd_train as handler

    return handler(args)


_COMMANDS = {"validate": cmd_validate, "list": cmd_list, "plan": cmd_plan, "run": cmd_run, "topology": cmd_topology,
             "experiment": cmd_experiment, "dataset": cmd_dataset, "prepare": cmd_prepare, "train": cmd_train}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(args.log_level, args.log_file)
    try:
        return _COMMANDS[args.command](args)
    except NetGuardError as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
