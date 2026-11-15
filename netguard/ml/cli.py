"""``python -m netguard train`` - Phase 4/5 training and evaluation."""

from __future__ import annotations

import argparse
from typing import Any

from netguard.experiments.cli import DEFAULT_CAMPAIGN
from netguard.experiments.config import load_experiment_config
from netguard.ml.run import train_all


def register(sub: Any) -> None:
    sp = sub.add_parser("train", help="Phase 4/5: train and evaluate classification, anomaly and congestion models")
    sp.add_argument("config", nargs="?", default=str(DEFAULT_CAMPAIGN))
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--n-jobs", type=int, default=2, help="CPU cores the models may use")


def cmd_train(args: argparse.Namespace) -> int:
    cfg = load_experiment_config(args.config)
    root = cfg.output.data_dir.parent
    report = train_all(cfg.output.data_dir, cfg.output.results_dir, root / "models", args.seed, args.n_jobs)
    print(f"models in {root / 'models'}; results in {cfg.output.results_dir / 'ml_results.md'}")
    return 0
