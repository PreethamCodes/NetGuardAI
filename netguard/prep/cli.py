"""``python -m netguard prepare`` - Phase 3 dataset preparation."""

from __future__ import annotations

import argparse
from typing import Any

from netguard.experiments.config import load_experiment_config, load_labeling
from netguard.experiments.cli import DEFAULT_CAMPAIGN
from netguard.prep.pipeline import prepare
from netguard.prep.splits import SCHEMES


def register(sub: Any) -> None:
    sp = sub.add_parser("prepare", help="Phase 3: validate runs, build features, split by run, fit the scaler")
    sp.add_argument("config", nargs="?", default=str(DEFAULT_CAMPAIGN), help="campaign YAML (for the data/results folders)")
    sp.add_argument("--scheme", choices=sorted(SCHEMES), default="three_way", help="how runs are assigned to splits")
    sp.add_argument("--expected-rows", type=int, default=None, help="require this many seconds per run (e.g. 300)")


def cmd_prepare(args: argparse.Namespace) -> int:
    cfg = load_experiment_config(args.config)
    result = prepare(cfg.output.data_dir, cfg.output.results_dir, load_labeling(args.config_dir),
                     scheme=args.scheme, expected_rows=args.expected_rows)
    r = result.report
    print(f"runs included {r['runs_included']}, excluded {r['runs_excluded']}; {r['rows']} rows, {r['feature_count']} features")
    for name, s in r["splits"].items():
        print(f"  {name}: {s['runs']} runs, {s['prediction_rows']} prediction rows, {s['prediction_positive']} positives")
    print(f"features: {result.features_path}")
    return 0
