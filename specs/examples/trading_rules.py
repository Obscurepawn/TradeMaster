"""Compare price-limit enforcement on the same synthetic USD data snapshot."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from trademaster.backtest import DailyPrice, Instrument, Policy, Snapshot, backtest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/trading-rules"))
    root = parser.parse_args().output
    sessions = [date(2024, 1, 5), date(2024, 1, 8)]
    snapshot = Snapshot.create(
        root / "snapshot",
        sessions=sessions,
        next_session=date(2024, 1, 9),
        instruments=[
            Instrument("DEMO", venue="nyse", currency="USD", buy_lot_size=1, settlement="t0")
        ],
        daily=[
            DailyPrice(sessions[0], "DEMO", 10, 10, evidence="synthetic fixture"),
            DailyPrice(
                sessions[1],
                "DEMO",
                10,
                10,
                buy_at_open="no",
                buy_reason="example_price_limit",
                buy_blocked_by_price_limit=True,
                evidence="synthetic fixture: price limit is the only buy restriction",
            ),
        ],
        close_times={
            day: datetime.combine(day, time(16), ZoneInfo("America/New_York")) for day in sessions
        },
    )
    for enforce in [True, False]:
        result = backtest(
            snapshot,
            targets={sessions[0]: {"DEMO": Decimal("0.70")}},
            initial_cash=100,
            policy=Policy(
                enforce_price_limits=enforce,
                commission_ppm=0,
                minimum_commission=0,
                sell_stamp_duty_ppm=0,
                sse_transfer_fee_ppm=0,
                buy_cap_ppm=0,
                max_attempts=1,
            ),
            detail=True,
        )
        result.save(root / ("enforced" if enforce else "disabled"))
        quantity = sum(p["quantity"] for p in result.positions if p["session"] == "2024-01-08")
        print(
            f"enforce_price_limits={enforce} fills={result.summary['fill_count']} "
            f"quantity={quantity} cash={result.equity[-1]['cash']:.2f} USD"
        )


if __name__ == "__main__":
    main()
