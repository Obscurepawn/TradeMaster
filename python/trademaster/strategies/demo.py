"""Daily close decisions. Apply each target no earlier than the next eligible open."""

from collections import deque
from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, Decimal
from math import isfinite
from statistics import fmean
from typing import TypeGuard

from trademaster.backtest import DecisionDay

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


def _simple_average(values: CloseSeries, period: int) -> list[float | None]:
    window: deque[float] = deque(maxlen=period)
    output = []
    for value in values:
        if value is None:
            window.clear()
        else:
            window.append(value)
        output.append(fmean(window) if len(window) == period else None)
    return output


class _Ema:
    """Seed with the first full window after each gap."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.alpha = 2 / (period + 1)
        self.seed: list[float] = []
        self.value: float | None = None

    def update(self, value: float | None) -> float | None:
        if value is None:
            self.seed.clear()
            self.value = None
        elif self.value is not None:
            self.value += self.alpha * (value - self.value)
        else:
            self.seed.append(value)
            if len(self.seed) == self.period:
                self.value = fmean(self.seed)
                self.seed.clear()
        return self.value


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


def moving_average_cross(
    symbol: str, closes: CloseSeries, *, fast: int = 5, slow: int = 20
) -> list[Target]:
    """Go long when the fast SMA crosses above the slow SMA, and flat when it crosses below."""
    if not 0 < fast < slow:
        raise ValueError("averages require 0 < fast < slow")
    prices = [value if _usable_close(value) else None for value in closes]
    averages = zip(_simple_average(prices, fast), _simple_average(prices, slow), strict=True)
    spreads = [a - b if a is not None and b is not None else None for a, b in averages]
    return _crossings(symbol, spreads)


class MacdSpread:
    """Update the MACD line minus its signal line with one adjusted close per session."""

    def __init__(self, *, fast: int, slow: int, signal: int) -> None:
        if signal < 1:
            raise ValueError("the signal EMA period must be positive")
        if not 0 < fast < slow:
            raise ValueError("averages require 0 < fast < slow")
        self._fast = _Ema(fast)
        self._slow = _Ema(slow)
        self._signal = _Ema(signal)

    def update(self, close: float | None) -> float | None:
        price = close if _usable_close(close) else None
        fast, slow = self._fast.update(price), self._slow.update(price)
        line = fast - slow if fast is not None and slow is not None else None
        signal = self._signal.update(line)
        return line - signal if line is not None and signal is not None else None


class MacdTiming:
    """Use a fresh instance for each daily-close backtest."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.macd = MacdSpread(fast=12, slow=26, signal=9)
        self.previous: float | None = None

    def on_close(self, day: DecisionDay) -> dict[str, Decimal] | None:
        close = day.closes(self.symbol, adjusted=True, limit=1)[-1]
        current = self.macd.update(close)
        previous = self.previous
        self.previous = current
        if previous is None or current is None:
            return None
        if previous <= 0 < current:
            return {self.symbol: Decimal(1)}
        if previous >= 0 > current:
            return {self.symbol: Decimal(0)}
        return None


def macd_cross(
    symbol: str, closes: CloseSeries, *, fast: int = 12, slow: int = 26, signal: int = 9
) -> list[Target]:
    """Go long/flat on upward/downward MACD-line crossings of its signal EMA.

    The MACD line is EMA(fast) minus EMA(slow). Each EMA starts with a full-window
    arithmetic mean. A crossing requires two consecutive observations of both lines.
    """
    macd = MacdSpread(fast=fast, slow=slow, signal=signal)
    return _crossings(symbol, [macd.update(close) for close in closes])
