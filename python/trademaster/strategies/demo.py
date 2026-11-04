"""Daily close decisions. Apply each target no earlier than the next eligible open."""

from collections import deque
from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, Decimal
from math import isfinite
from statistics import fmean
from typing import TypeGuard

type CloseSeries = Sequence[float | None]
type Target = dict[str, Decimal] | None


def _usable_close(value: float | None) -> TypeGuard[float]:
    return value is not None and isfinite(value) and value > 0


def cross_sectional_momentum(
    closes_by_symbol: Mapping[str, CloseSeries], *, lookback: int = 20, top_n: int = 2
) -> list[Target]:
    """Rank close-to-close returns over an exact session lag and equally weight the top N.

    All series share one ordered trading calendar. Keep missing sessions as None.
    Return a full target including exits, or None when fewer than N returns are valid.
    """
    if lookback < 1 or top_n < 1:
        raise ValueError("lookback and top_n must be positive")
    if not closes_by_symbol:
        return []
    count = len(next(iter(closes_by_symbol.values())))
    if any(len(series) != count for series in closes_by_symbol.values()):
        raise ValueError("close series must be aligned on the same trading calendar")

    symbols = sorted(closes_by_symbol)
    weight = (Decimal(1) / top_n).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    decisions: list[Target] = [None] * count
    for i in range(lookback, count):
        ranked = []
        for symbol in symbols:
            series = closes_by_symbol[symbol]
            past, now = series[i - lookback], series[i]
            if _usable_close(past) and _usable_close(now):
                score = now / past - 1
                if isfinite(score):
                    ranked.append((-score, symbol))
        if len(ranked) < top_n:
            continue
        ranked.sort()
        targets = dict.fromkeys(symbols, Decimal(0))
        for _, symbol in ranked[:top_n]:
            targets[symbol] = weight
        targets[ranked[0][1]] += Decimal(1) - top_n * weight
        decisions[i] = targets
    return decisions


def _average(values: CloseSeries, period: int, *, exponential: bool) -> list[float | None]:
    """Use the first full window as the seed. A missing observation restarts the window."""
    window: deque[float] = deque(maxlen=period)
    average = None
    output = []
    alpha = 2 / (period + 1)
    for value in values:
        if value is None:
            window.clear()
            average = None
        else:
            window.append(value)
            if len(window) == period:
                if exponential and average is not None:
                    average += alpha * (value - average)
                else:
                    average = fmean(window)
        output.append(average)
    return output


def _difference(left: CloseSeries, right: CloseSeries) -> list[float | None]:
    return [
        a - b if a is not None and b is not None else None for a, b in zip(left, right, strict=True)
    ]


def _crossings(symbol: str, differences: CloseSeries) -> list[Target]:
    decisions: list[Target] = [None] * len(differences)
    for i in range(1, len(differences)):
        previous, current = differences[i - 1], differences[i]
        if previous is None or current is None:
            continue
        if previous <= 0 < current:
            decisions[i] = {symbol: Decimal(1)}
        elif previous >= 0 > current:
            decisions[i] = {symbol: Decimal(0)}
    return decisions


def _average_spread(
    closes: CloseSeries, fast: int, slow: int, *, exponential: bool
) -> list[float | None]:
    if not 0 < fast < slow:
        raise ValueError("averages require 0 < fast < slow")
    prices = [value if _usable_close(value) else None for value in closes]
    return _difference(
        _average(prices, fast, exponential=exponential),
        _average(prices, slow, exponential=exponential),
    )


def moving_average_cross(
    symbol: str, closes: CloseSeries, *, fast: int = 5, slow: int = 20
) -> list[Target]:
    """Go long when the fast SMA crosses above the slow SMA, and flat when it crosses below."""
    return _crossings(symbol, _average_spread(closes, fast, slow, exponential=False))


def macd_cross(
    symbol: str, closes: CloseSeries, *, fast: int = 12, slow: int = 26, signal: int = 9
) -> list[Target]:
    """Go long/flat on upward/downward MACD-line crossings of its signal EMA.

    The MACD line is EMA(fast) minus EMA(slow). Each EMA starts with a full-window
    arithmetic mean. A crossing requires two consecutive observations of both lines.
    """
    if signal < 1:
        raise ValueError("the signal EMA period must be positive")
    line = _average_spread(closes, fast, slow, exponential=True)
    signal_line = _average(line, signal, exponential=True)
    return _crossings(symbol, _difference(line, signal_line))
