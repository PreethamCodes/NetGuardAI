"""The shared clock of one experiment.

Every collector samples on the same wall-clock grid, so rows from different
collectors line up by construction. *Second ``k``* of the dataset is the
interval ``[anchor + k, anchor + k + 1)`` where ``anchor`` is the wall-clock
instant of scenario time 0 (the same instant the traffic executor uses).

Counter based metrics for second ``k`` come from the samples taken at ticks
``k`` and ``k + 1`` (rate = difference / actual elapsed time); instantaneous
metrics (queue backlog, CPU, RAM) are the sample at tick ``k + 1``, i.e. the
state at the *end* of the second. Ticks ``-pre_roll .. -1`` and
``duration .. duration + tail`` exist only in the raw collector output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass(frozen=True)
class Timeline:
    anchor_wall: float  # time.time() of scenario second 0
    duration_s: int  # number of dataset seconds (0 .. duration_s - 1)
    pre_roll_s: int = 5
    tail_s: int = 3

    @property
    def first_tick(self) -> int:
        return -self.pre_roll_s

    @property
    def last_tick(self) -> int:
        """Last tick: ``duration_s`` closes the final dataset second, the tail comes after it."""
        return self.duration_s + self.tail_s

    def ticks(self) -> Iterator[int]:
        return iter(range(self.first_tick, self.last_tick + 1))

    def tick_time(self, tick: int) -> float:
        return self.anchor_wall + tick

    @property
    def start_wall(self) -> float:
        return self.tick_time(self.first_tick)

    @property
    def end_wall(self) -> float:
        return self.tick_time(self.last_tick)

    def second_of(self, wall_time: float) -> int:
        """Dataset second containing ``wall_time`` (may be negative / >= duration_s)."""
        import math

        return int(math.floor(wall_time - self.anchor_wall))


#: ``(wall_time_seconds) -> None``; injectable for tests.
SleepUntil = Callable[[float], None]


def monotonic_to_wall(mono: float, ref_wall: float, ref_mono: float) -> float:
    """Convert a ``time.monotonic()`` value to wall-clock using a reference pair."""
    return ref_wall + (mono - ref_mono)


def now_pair() -> "tuple[float, float]":
    """``(time.time(), time.monotonic())`` taken back to back."""
    return time.time(), time.monotonic()
