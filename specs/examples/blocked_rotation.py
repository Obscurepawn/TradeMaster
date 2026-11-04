"""Run the blocked-sale acceptance example against the Rust snapshot ledger."""

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from trademaster.backtest import (
    BacktestDay,
    DailyPrice,
    Feature,
    InitialAccount,
    InitialLot,
    Instrument,
    Policy,
    Snapshot,
    backtest,
)
from weekly_rotation import WeeklyRotation


class SwitchAfterSale:
    """Sell A first. Set B's target after the closing account confirms the sale."""

    def __init__(self) -> None:
        self.sell_requested = False
        self.buy_requested = False

    def on_decision(self, day: BacktestDay) -> dict[str, Decimal] | None:
        if day.account.quantity("A"):
            if not self.sell_requested:
                self.sell_requested = True
                return {}
        elif not self.buy_requested:
            self.buy_requested = True
            return {"B": Decimal("0.80")}
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("weekly", "after-sale"), default="weekly")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path(
        "artifacts/blocked-rotation" if args.strategy == "weekly" else "artifacts/after-sale"
    )
    friday, monday, tuesday, wednesday = sessions = [date(2024, 1, i) for i in (5, 8, 9, 10)]
    rows = []
    for day in sessions:
        price = 10 if day == friday else 9
        rows.extend(
            [
                DailyPrice(
                    day,
                    "A",
                    price,
                    price,
                    sell_at_open="no" if day == monday else "yes",
                    sell_reason="lower_limit" if day == monday else None,
                    evidence="example",
                ),
                DailyPrice(day, "B", 20, 20, evidence="example"),
            ]
        )
    cutoff = datetime(2024, 1, 5, 7, tzinfo=UTC)
    snapshot = Snapshot.create(
        output / "snapshot",
        sessions=sessions,
        next_session=date(2024, 1, 11),
        instruments=[Instrument("A"), Instrument("B")],
        daily=rows,
        features=[
            Feature("A", "rotation_score", 0.35, cutoff),
            Feature("B", "rotation_score", 0.72, cutoff),
        ],
    )
    result = backtest(
        snapshot,
        WeeklyRotation() if args.strategy == "weekly" else SwitchAfterSale(),
        schedule="week_end" if args.strategy == "weekly" else "daily_close",
        initial_account=InitialAccount(cash=20_000, lots=(InitialLot("A", 8_000, friday),)),
        policy=Policy(
            commission_ppm=0,
            minimum_commission=Decimal(5),
            sse_transfer_fee_ppm=0,
            sell_stamp_duty_ppm=0,
            slippage_ppm=0,
            buy_cap_ppm=20_000,
            max_attempts=3,
        ),
        detail=True,
    )
    expected_dates = [friday] if args.strategy == "weekly" else [friday, tuesday]
    assert [row["decision_session"] for row in result.targets] == [
        d.isoformat() for d in expected_dates
    ]
    unit = 100_000_000
    assert [(t["session"], t["instrument_id"], t["side"], t["quantity"]) for t in result.fills] == [
        (tuesday.isoformat(), "A", "sell", 8000),
        (wednesday.isoformat(), "B", "buy", 3600),
    ]
    assert [(d["cash"], d["equity"]) for d in result.equity] == [
        (20000, 100000),
        (20000, 92000),
        (91995, 91995),
        (19990, 91990),
    ]
    print("Session     Cash (CNY)   Equity (CNY)")
    for row in result.raw["daily"]:
        print(
            f"{row['session']}  {Decimal(row['cash']) / unit:10,.2f}   {Decimal(row['equity']) / unit:12,.2f}"
        )
    result.save(output)


if __name__ == "__main__":
    main()
