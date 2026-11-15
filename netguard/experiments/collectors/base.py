"""Common machinery of the collectors.

Every collector

* runs in its own thread (or subprocess) and is independent of the others,
* keeps its **raw samples** (list of dicts, written to ``data/raw`` unchanged),
* records its errors instead of raising into the orchestrator, and
* exposes ``health()`` so the orchestrator can mark the experiment failed when a
  required collector did not deliver (a missing measurement is never replaced
  by an invented value).
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from netguard.experiments.timeline import Timeline

logger = logging.getLogger(__name__)

#: ``run(argv) -> (returncode, stdout)``; replaced by fakes in tests.
CommandRunner = Callable[[Sequence[str]], Tuple[int, str]]


def run_command(argv: Sequence[str], timeout: float = 5.0) -> Tuple[int, str]:
    """Run ``argv`` and return ``(returncode, stdout)``; never raises for ordinary failures."""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return done.returncode, done.stdout if done.returncode == 0 else (done.stderr or done.stdout)


@dataclass
class Health:
    name: str
    started: bool
    attempted: int = 0
    failed: int = 0
    errors: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.started and self.attempted > 0 and self.failed <= 0.1 * self.attempted

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "started": self.started, "attempted": self.attempted,
                "failed": self.failed, "ok": self.ok, "errors": list(self.errors)}


class Collector:
    """Interface shared by all collectors."""

    name = "collector"

    def start(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def raw_samples(self) -> List[Dict[str, Any]]:  # pragma: no cover - interface
        raise NotImplementedError

    def health(self) -> Health:  # pragma: no cover - interface
        raise NotImplementedError


class PeriodicCollector(Collector):
    """Calls :meth:`sample` once per timeline tick, at the tick's wall-clock instant."""

    max_error_log = 5

    def __init__(self, timeline: Timeline, *, clock: Callable[[], float] = time.time) -> None:
        self.timeline = timeline
        self._clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._samples: List[Dict[str, Any]] = []
        self._errors: List[str] = []
        self._attempted = 0
        self._failed = 0
        self._started = False

    # ----------------------------------------------------------------- subclass hooks
    def prepare(self) -> None:
        """Called once from :meth:`start` (raise to report a start-up failure)."""

    def sample(self, tick: int, ts: float) -> Optional[List[Dict[str, Any]]]:
        """Take the sample of ``tick``; return raw rows (or raise on failure)."""
        raise NotImplementedError

    def finish(self) -> None:
        """Called after the last tick or on stop."""

    # ------------------------------------------------------------------------ control
    def start(self) -> None:
        self.prepare()
        self._started = True
        self._thread = threading.Thread(target=self._loop, name=f"collector-{self.name}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        try:
            for tick in self.timeline.ticks():
                if self._stop.wait(max(0.0, self.timeline.tick_time(tick) - self._clock())):
                    break
                ts = self._clock()
                self._attempted += 1
                try:
                    rows = self.sample(tick, ts)
                except Exception as exc:  # noqa: BLE001 - one bad sample must not kill the collector
                    self._failed += 1
                    if len(self._errors) < self.max_error_log:
                        self._errors.append(f"tick {tick}: {type(exc).__name__}: {exc}")
                    logger.warning("%s: sample at tick %d failed: %s", self.name, tick, exc)
                    continue
                for row in rows or ():
                    row.setdefault("tick", tick)
                    row.setdefault("ts", ts)
                    self._samples.append(row)
        finally:
            self.finish()

    def stop(self) -> None:
        """Stop sampling and wait for the thread (the sampling loop is quick to react)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():  # pragma: no cover - defensive
                logger.error("%s: thread did not stop", self.name)

    def wait_done(self, timeout: Optional[float] = None) -> bool:
        """Block until the loop has processed every tick (or ``timeout``)."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def raw_samples(self) -> List[Dict[str, Any]]:
        return list(self._samples)

    def health(self) -> Health:
        return Health(self.name, self._started, self._attempted, self._failed, tuple(self._errors))
