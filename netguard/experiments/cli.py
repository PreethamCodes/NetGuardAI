"""``python -m netguard experiment ...`` and ``... dataset ...`` sub-commands (Phase 2)."""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_environment
from netguard.exceptions import NetGuardError

logger = logging.getLogger("netguard.cli")
DEFAULT_CAMPAIGN = DEFAULT_CONFIG_DIR / "experiments" / "phase2.yaml"


def register(sub: Any) -> None:
    exp = sub.add_parser("experiment", help="Phase 2: run experiments, calibrate X, relabel, report")
    esub = exp.add_subparsers(dest="exp_command", required=True)

    sp = esub.add_parser("run", help="run a campaign (Ubuntu VM, root)")
    sp.add_argument("config", nargs="?", default=str(DEFAULT_CAMPAIGN), help="campaign YAML")
    sp.add_argument("--scenario", action="append", help="only this scenario (repeatable)")
    sp.add_argument("--repetition", action="append", type=int, help="only this repetition number (repeatable)")
    sp.add_argument("--duration", type=float, default=None, help="override the duration of every run (seconds)")
    sp.add_argument("--no-resume", action="store_true", help="re-run runs that already succeeded")
    sp.add_argument("--no-auto-calibrate", action="store_true", help="do not calibrate X after the NORMAL runs")
    sp.add_argument("--dry-run", action="store_true", help="validate and list the runs, change nothing")

    sp = esub.add_parser("calibrate", help="derive the queue-delay threshold X from the NORMAL experiments")
    sp.add_argument("config", nargs="?", default=str(DEFAULT_CAMPAIGN))
    sp.add_argument("--force", action="store_true", help="replace an existing calibration")

    sp = esub.add_parser("label", help="(re)compute the labels of all stored experiments with the fixed X")
    sp.add_argument("config", nargs="?", default=str(DEFAULT_CAMPAIGN))

    sp = esub.add_parser("report", help="write the validation report (results/validation_report.md)")
    sp.add_argument("config", nargs="?", default=str(DEFAULT_CAMPAIGN))

    ds = sub.add_parser("dataset", help="Phase 2: build the consolidated dataset")
    dsub = ds.add_subparsers(dest="ds_command", required=True)
    sp = dsub.add_parser("build", help="concatenate all successful experiments into processed/dataset.parquet")
    sp.add_argument("config", nargs="?", default=str(DEFAULT_CAMPAIGN))


def _load(args: argparse.Namespace):
    from netguard.experiments.config import load_experiment_config, load_labeling

    cfg = load_experiment_config(args.config)
    if getattr(args, "duration", None) is not None:
        from dataclasses import replace

        cfg = replace(cfg, duration_s=args.duration)
    return cfg, load_labeling(args.config_dir)


def cmd_experiment(args: argparse.Namespace) -> int:
    from netguard.experiments import dataset as ds
    from netguard.experiments.calibration import effective_labeling
    from netguard.experiments.orchestrator import ExperimentOrchestrator, plan_runs
    from netguard.experiments.postprocess import calibrate, relabel_all
    from netguard.experiments.report import write_report
    from netguard.scenarios.loader import list_scenarios
    from netguard.utils.system import is_linux, is_root

    cfg, labeling = _load(args)
    available = list_scenarios(args.config_dir)
    unknown = [s for s in cfg.scenarios if s not in available]
    if unknown:
        raise NetGuardError(f"campaign scenarios not found in {args.config_dir}/scenarios: {unknown}")
    data_dir = cfg.output.data_dir

    if args.exp_command == "calibrate":
        result = calibrate(data_dir, labeling, campaign=cfg.name, force=args.force)
        print(json.dumps(result, indent=2))
        return 0
    if args.exp_command == "label":
        done = relabel_all(data_dir, effective_labeling(labeling, data_dir))
        print(f"relabelled {len(done)} experiment(s)")
        return 0
    if args.exp_command == "report":
        print(f"wrote {write_report(cfg, labeling)}")
        return 0

    runs = plan_runs(cfg, args.scenario, args.repetition)
    env = load_environment(args.config_dir)
    orch = ExperimentOrchestrator(cfg, env, effective_labeling(labeling, data_dir), Path(args.config_dir))
    if args.dry_run:
        done = orch.completed()
        for r in runs:
            state = "done" if (r.scenario, r.repetition) in done else "todo"
            print(f"{r.scenario:<12} rep {r.repetition} seed {r.seed:<4} [{state}]")
        print(f"{len(runs)} run(s) planned; data dir {data_dir}")
        return 0
    if not is_linux() or not is_root():
        raise NetGuardError("experiments need Linux and root (run with sudo inside the Ubuntu VM)")
    results = orch.run_campaign(runs, resume=not args.no_resume, auto_calibrate=not args.no_auto_calibrate)
    counts = Counter(m.status for m in results)
    for m in results:
        extra = f" {m.errors[0]}" if m.errors else (f" {m.invalid_reasons}" if m.invalid_reasons else "")
        print(f"{m.experiment_id}: {m.status}{extra}")
    print(f"summary: {dict(counts)}")
    write_report(cfg, labeling)
    return 0 if not (counts[ds.STATUS_FAILED] or counts[ds.STATUS_INVALID]) else 1


def cmd_dataset(args: argparse.Namespace) -> int:
    from netguard.experiments.dataset import build_dataset

    cfg, _ = _load(args)
    print(f"wrote {build_dataset(cfg.output.data_dir)}")
    return 0
