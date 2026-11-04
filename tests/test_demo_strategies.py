from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trademaster.backtest import DailyPrice, Instrument, Snapshot
from trademaster.strategies.demo import cross_sectional_momentum, macd_cross, moving_average_cross


def test_momentum_ranks_the_same_sessions_and_breaks_ties_by_symbol() -> None:
    prices = {"B": [10.0, 20.0, 15.0], "A": [20.0, 5.0, 30.0], "C": [8.0, 9.0, 7.0]}
    decisions = cross_sectional_momentum(prices, lookback=2, top_n=1)

    assert decisions == [None, None, {"A": Decimal(1), "B": Decimal(0), "C": Decimal(0)}]
    assert (
        cross_sectional_momentum(dict(reversed(prices.items())), lookback=2, top_n=1) == decisions
    )


def test_momentum_keeps_missing_sessions_and_returns_exact_weights() -> None:
    prices: dict[str, list[float | None]] = {
        "A": [10.0, None, 12.0],
        "B": [10.0, 11.0, 11.0],
        "C": [11.0, 11.0, 11.0],
    }
    assert cross_sectional_momentum(prices, lookback=1, top_n=3)[2] is None
    assert cross_sectional_momentum(prices, lookback=2, top_n=3)[2] == {
        "A": Decimal("0.33333334"),
        "B": Decimal("0.33333333"),
        "C": Decimal("0.33333333"),
    }
    with pytest.raises(ValueError, match="aligned"):
        cross_sectional_momentum({"A": [10.0], "B": [10.0, 11.0]})


def test_moving_average_emits_only_upward_and_downward_crossings() -> None:
    closes = [10.0, 10.0, 10.0, 12.0, 13.0, 10.0, 8.0]
    assert moving_average_cross("A", closes, fast=2, slow=3) == [
        None,
        None,
        None,
        {"A": Decimal(1)},
        None,
        {"A": Decimal(0)},
        None,
    ]


def test_default_macd_waits_for_both_lines_then_finds_both_crossings() -> None:
    closes = [10.0] * 34 + [20.0, 20.0] + [1.0] * 8
    decisions = macd_cross("A", closes)
    assert [(i, d) for i, d in enumerate(decisions) if d is not None] == [
        (34, {"A": Decimal(1)}),
        (36, {"A": Decimal(0)}),
    ]


def test_daily_macd_matches_batch_decisions_before_and_after_a_price_gap(tmp_path: Path) -> None:
    from trademaster.strategies.demo import MacdTiming

    cycle = [10.0] * 34 + [20.0, 20.0] + [1.0] * 8
    adjusted = [*cycle, None, *cycle]
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(adjusted))]
    snapshot = Snapshot.create(
        tmp_path,
        sessions=days,
        next_session=days[-1] + timedelta(days=1),
        instruments=[Instrument("A")],
        daily=[
            DailyPrice(day, "A", 10, 10, adjusted_close=price, evidence="fixture")
            for day, price in zip(days, adjusted, strict=True)
        ],
    )
    strategy = MacdTiming("A")
    decisions = [strategy.on_close(snapshot.day(day)) for day in days]
    assert decisions == macd_cross("A", adjusted)
    assert [(i, d) for i, d in enumerate(decisions) if d is not None] == [
        (34, {"A": Decimal(1)}),
        (36, {"A": Decimal(0)}),
        (79, {"A": Decimal(1)}),
        (81, {"A": Decimal(0)}),
    ]


def test_timing_strategies_warm_up_again_after_a_missing_close() -> None:
    closes = [12.0] * 34 + [None, 9.0, 11.0, 15.0]
    assert moving_average_cross("A", closes, fast=2, slow=3)[34:] == [None] * 4
    assert macd_cross("A", closes)[34:] == [None] * 4


def test_future_prices_cannot_change_earlier_decisions() -> None:
    closes = [10.0] * 34 + [20.0, 20.0] + [1.0] * 8
    extended = [*closes, 100_000.0, 3.0]
    assert macd_cross("A", extended)[: len(closes)] == macd_cross("A", closes)
    assert moving_average_cross("A", extended)[: len(closes)] == moving_average_cross("A", closes)
    initial = {"A": closes, "B": [10.0] * len(closes)}
    later = {"A": extended, "B": [*initial["B"], 7.0, 8.0]}
    assert cross_sectional_momentum(later, top_n=1)[: len(closes)] == cross_sectional_momentum(
        initial, top_n=1
    )
