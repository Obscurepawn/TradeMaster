"""At the weekly close, allocate 80% to the instrument with the highest score."""

from collections.abc import Mapping
from decimal import Decimal
from math import isfinite
from typing import Protocol


class DecisionSlice(Protocol):
    def scores(self, name: str) -> Mapping[str, float | None]: ...


class WeeklyRotation:
    def on_close(self, day: DecisionSlice) -> dict[str, Decimal] | None:
        # Rank by descending score, then by instrument ID.
        ranking = (
            (-score, instrument)
            for instrument, score in day.scores("rotation_score").items()
            if score is not None and isfinite(score)
        )
        first = min(ranking, default=None)
        if first is None:
            return None  # Keep the current target until it expires.
        _, winner = first
        return {winner: Decimal("0.80")}


if __name__ == "__main__":
    from types import MappingProxyType

    class FridaySnapshotSlice:
        def scores(self, name: str) -> Mapping[str, float | None]:
            if name != "rotation_score":
                raise KeyError(name)
            return MappingProxyType({"A": 0.35, "B": 0.72})

    day = FridaySnapshotSlice()
    print("Weekly closing scores", dict(day.scores("rotation_score")))
    targets = WeeklyRotation().on_close(day)
    if targets is not None:
        for instrument, weight in targets.items():
            print(f"Target weight {instrument} {weight:.0%}")
