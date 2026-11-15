"""VM CPU and RAM (psutil), sampled once per tick.

``cpu_percent(interval=None)`` reports the utilisation since the previous call,
so the reading at tick ``k + 1`` covers dataset second ``k`` exactly. A CPU value
at or above the configured saturation level (default 100 %) invalidates the
experiment: collectors and traffic generators then compete for the CPU and the
measurements can no longer be trusted.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Sequence

import pandas as pd

from netguard.experiments.collectors.base import PeriodicCollector
from netguard.experiments.timeline import Timeline


class ResourceCollector(PeriodicCollector):
    name = "resources"

    def __init__(self, timeline: Timeline, psutil_module: Any = None, **kw: Any):
        super().__init__(timeline, **kw)
        if psutil_module is None:
            import psutil as psutil_module  # noqa: PLC0415 - optional at import time
        self._psutil = psutil_module

    def prepare(self) -> None:
        self._psutil.cpu_percent(interval=None)  # prime: the first call always returns 0.0

    def sample(self, tick: int, ts: float) -> List[Dict[str, Any]]:
        mem = self._psutil.virtual_memory()
        return [{
            "cpu_percent": float(self._psutil.cpu_percent(interval=None)),
            "ram_percent": float(mem.percent),
            "ram_used_mb": float(mem.used) / 1e6,
        }]


def derive_resource_metrics(raw: Sequence[Mapping[str, Any]], n_seconds: int) -> pd.DataFrame:
    nan = [math.nan] * n_seconds
    out = {"cpu_percent": list(nan), "ram_percent": list(nan), "ram_used_mb": list(nan)}
    if raw:
        by_tick = pd.DataFrame(list(raw)).drop_duplicates("tick", keep="last").set_index("tick")
        for k in range(n_seconds):
            if k in by_tick.index and (k + 1) in by_tick.index:  # tick k is the interval's start
                for column in out:
                    out[column][k] = float(by_tick.loc[k + 1, column])
    return pd.DataFrame(out)


def saturated_seconds(raw: Sequence[Mapping[str, Any]], threshold_pct: float, first_tick: int, last_tick: int) -> int:
    """Number of CPU samples in ``first_tick..last_tick`` at or above ``threshold_pct``."""
    return sum(1 for r in raw if first_tick <= r["tick"] <= last_tick and r["cpu_percent"] >= threshold_pct - 1e-9)
