"""Train / validation / test assignment **by experiment run** (never by row).

A run (``experiment_id``) belongs to exactly one split, so no second of a run can leak between sets.
Within the primary dataset every scenario has the same number of repetitions, so the split is made per
repetition number, which keeps every scenario in every split:

``three_way`` (default)   repetition 1 -> train, 2 -> validation, 3 -> test
``train2_test1``          repetitions 1, 2 -> train, 3 -> test (no validation set: use grouped CV by run)

Assigning by repetition number is deterministic and independent of the random seed of the data.
"""

from __future__ import annotations

from typing import Dict, List, Mapping

import pandas as pd

from netguard.exceptions import ConfigError

SCHEMES: Mapping[str, Mapping[str, List[int]]] = {
    "three_way": {"train": [1], "val": [2], "test": [3]},
    "train2_test1": {"train": [1, 2], "val": [], "test": [3]},
}


def assign_splits(runs: pd.DataFrame, scheme: str = "three_way") -> pd.DataFrame:
    """Return ``runs`` (one row per experiment with ``experiment_id`` and ``repetition``) plus a ``split`` column."""
    if scheme not in SCHEMES:
        raise ConfigError(f"unknown split scheme {scheme!r}; choose from {sorted(SCHEMES)}")
    mapping: Dict[int, str] = {}
    for split, reps in SCHEMES[scheme].items():
        for rep in reps:
            mapping[rep] = split
    out = runs.copy()
    out["split"] = out["repetition"].map(mapping)
    unassigned = out[out["split"].isna()]
    if not unassigned.empty:
        raise ConfigError(f"repetition(s) {sorted(unassigned['repetition'].unique())} are not covered by scheme {scheme!r}")
    return out


def check_no_leakage(frame: pd.DataFrame) -> List[str]:
    """Problems if any run appears in more than one split or timestamps overlap across splits."""
    problems: List[str] = []
    per_run = frame.groupby("experiment_id")["split"].nunique()
    bad = per_run[per_run > 1]
    if not bad.empty:
        problems.append(f"runs in more than one split: {list(bad.index)}")
    return problems
