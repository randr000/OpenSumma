"""Deterministic random draws: the same seed gives the same draws everywhere.

Python guarantees the sequence of ``random.Random.random`` for a given seed, but not
the algorithms behind ``randint``, ``choice``, ``shuffle``, or ``sample``, which have
changed between versions. Every draw here is therefore built from ``random()``
alone, with integer arithmetic and IEEE multiplication, never a transcendental
function whose last digit could differ between platforms.
"""

import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


class Rng:
    """A stream of draws for one purpose, such as planning sales.

    Each purpose has its own stream, so drawing more for one purpose never shifts
    the draws of another.
    """

    def __init__(self, seed: int, stream: str) -> None:
        # A str seed is hashed with SHA-512 (seeding version 2), which Python keeps
        # stable across versions and does not randomize per process.
        self._random = random.Random(f"opensumma:{seed}:{stream}")

    def fraction(self) -> float:
        """A number in [0, 1)."""
        return self._random.random()

    def integer(self, low: int, high: int) -> int:
        """An integer from ``low`` to ``high``, both inclusive."""
        if high < low:
            raise ValueError(f"empty range {low}..{high}")
        span = high - low + 1
        return low + min(int(self.fraction() * span), span - 1)

    def skewed(self, low: int, high: int) -> int:
        """An integer from ``low`` to ``high``, more often near ``low``, as amounts
        are: many small ones and a few large."""
        u = self.fraction()
        return low + min(int((high - low + 1) * u * u), high - low)

    def chance(self, probability: float) -> bool:
        return self.fraction() < probability

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("cannot choose from nothing")
        return items[self.integer(0, len(items) - 1)]

    def weighted(self, items: Sequence[T], weights: Sequence[int]) -> T:
        """One of ``items``, each as likely as its integer weight."""
        if len(items) != len(weights) or not items:
            raise ValueError("each item needs one weight")
        pick = self.integer(1, sum(weights))
        for item, weight in zip(items, weights, strict=True):
            pick -= weight
            if pick <= 0:
                return item
        raise AssertionError("unreachable")

    def shuffled(self, items: Sequence[T]) -> list[T]:
        """``items`` in a random order (Fisher-Yates)."""
        result = list(items)
        for i in range(len(result) - 1, 0, -1):
            j = self.integer(0, i)
            result[i], result[j] = result[j], result[i]
        return result

    def sample(self, items: Sequence[T], count: int) -> list[T]:
        """``count`` distinct items, in a random order."""
        if count > len(items):
            raise ValueError(f"cannot take {count} of {len(items)} items")
        return self.shuffled(items)[:count]
