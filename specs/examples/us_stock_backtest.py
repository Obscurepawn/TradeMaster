"""Run a close-time momentum strategy on a synthetic USD stock data snapshot."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from trademaster.backtest import BacktestDay, DailyPrice, Instrument, Policy, Snapshot, backtest


class CloseMomentum:
    """Target 80% after a rising close and cash after a flat or falling close."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def on_close(self, day: BacktestDay) -> dict[str, Decimal] | None:
        prices = day.closes(self.symbol, adjusted=True, limit=2)
        if len(prices) < 2 or any(price is None for price in prices):
            return None
        previous, current = prices
        assert previous is not None and current is not None
        return {self.symbol: Decimal("0.80")} if current > previous else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/us-stock-demo"))
    root = parser.parse_args().output
    # Explicit fixture sessions; production loaders read the exchange calendar.
    sessions = [date(2024, 1, day) for day in (2, 3, 4, 5, 8)]
    opens = (100, 100, 102, 101, 99)
    closes = (100, 102, 101, 99, 98)
    symbol = "DEMO.US"
    snapshot = Snapshot.create(
        root / "snapshot",
        sessions=sessions,
        next_session=date(2024, 1, 9),
        instruments=[
            Instrument(
                symbol,
                venue="nasdaq",
                currency="USD",
                buy_lot_size=1,
                tick_size=Decimal("0.01"),
                settlement="t0",
            )
        ],
        daily=[
            DailyPrice(
                session,
                symbol,
                opening,
                closing,
                evidence="synthetic fixture: both sides tradable at the open",
                adjusted_close=float(closing),
            )
            for session, opening, closing in zip(sessions, opens, closes, strict=True)
        ],
        close_times={
            day: datetime.combine(day, time(16), ZoneInfo("America/New_York")) for day in sessions
        },
        source_evidence={"synthetic_fixture": "us_stock_backtest:v1"},
    )
    # Zero fees are an explicit example assumption, not a US brokerage schedule.
    policy = Policy(
        enforce_price_limits=False,
        commission_ppm=0,
        minimum_commission=0,
        sell_stamp_duty_ppm=0,
        sse_transfer_fee_ppm=0,
        slippage_ppm=0,
        buy_cap_ppm=20_000,
    )
    result = backtest(
        snapshot,
        CloseMomentum(symbol),
        initial_cash=10_000,
        policy=policy,
        detail=True,
    )
    result.save(root / "run")
    for fill in result.fills:
        price = Decimal(fill["price"]) / 100_000_000
        print(f"{fill['session']} {fill['side']} {fill['quantity']} @ {price:.2f} USD")
    print(f"Final equity: {result.equity[-1]['equity']:.2f} USD")


if __name__ == "__main__":
    main()
