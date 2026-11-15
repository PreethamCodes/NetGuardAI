"""Shared pacing loop for the HTTP and DNS client workers."""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Optional


@dataclass
class ClientStats:
    attempts: int = 0
    errors: int = 0
    bytes: int = 0
    latency_sum_s: float = 0.0

    def summary(self, duration_s: float) -> Dict[str, object]:
        ok = self.attempts - self.errors
        data: Dict[str, object] = asdict(self)
        data["successes"] = ok
        data["mean_latency_ms"] = round(1000 * self.latency_sum_s / ok, 3) if ok else None
        data["duration_s"] = round(duration_s, 3)
        return data


def paced_interval(rate: float, clients: int) -> float:
    """Seconds between operations of one client thread for an aggregate ``rate``."""
    if rate <= 0 or clients < 1:
        raise ValueError("rate must be > 0 and clients >= 1")
    return clients / rate


def run_paced_clients(
    action: Callable[[int, random.Random], int],
    *,
    rate: float,
    clients: int,
    duration: float,
    seed: int,
) -> ClientStats:
    """Run ``clients`` threads, each calling ``action`` at ~``rate/clients`` Hz.

    ``action(thread_index, rng)`` returns the number of bytes received and
    raises on failure. Intervals are jittered by +-20 % (seeded) so threads do
    not synchronise.
    """
    interval = paced_interval(rate, clients)
    stats = ClientStats()
    lock = threading.Lock()
    start = time.monotonic()
    deadline = start + duration

    def worker(idx: int) -> None:
        rng = random.Random(seed * 1009 + idx)
        next_at = start + rng.uniform(0.0, interval)
        while next_at < deadline:
            delay = next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            t0 = time.monotonic()
            nbytes: Optional[int] = None
            try:
                nbytes = action(idx, rng)
            except Exception:  # noqa: BLE001 - any failure is counted, never hidden
                pass
            elapsed = time.monotonic() - t0
            with lock:
                stats.attempts += 1
                if nbytes is None:
                    stats.errors += 1
                else:
                    stats.bytes += nbytes
                    stats.latency_sum_s += elapsed
            next_at += interval * rng.uniform(0.8, 1.2)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return stats


def emit_summary(stats: ClientStats, duration_s: float) -> int:
    """Print the JSON summary and return the process exit code.

    Exit code is 1 only if every attempt failed (e.g. server unreachable).
    """
    print(json.dumps(stats.summary(duration_s)), flush=True)
    return 1 if stats.attempts > 0 and stats.errors == stats.attempts else 0
