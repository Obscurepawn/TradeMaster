"""Rank the stocks listed at each weekly close by their 20-session return.

For an all-A run, supply a stock-only data snapshot covering the historical
Shanghai, Shenzhen and Beijing A-share universe, including delisted stocks.
The same example can run on a smaller stock sample.
"""

import argparse
import json
from decimal import Decimal
from math import isfinite
from pathlib import Path

from trademaster.backtest import BacktestDay, Snapshot, backtest


class AllAMomentum:
    def on_close(self, day: BacktestDay) -> dict[str, Decimal]:
        ranking = []
        for symbol in day.instruments:
            closes = day.closes(symbol, adjusted=True, limit=21)
            if len(closes) < 21 or closes[0] is None or closes[-1] is None:
                continue
            momentum = closes[-1] / closes[0] - 1
            if isfinite(momentum):
                ranking.append((-momentum, symbol))  # Higher returns sort first.

        winners = sorted(ranking)[:10]
        return {symbol: Decimal("0.10") for _, symbol in winners}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="published daily stock snapshot directory")
    parser.add_argument("--output", type=Path, required=True, help="directory for the saved run")
    args = parser.parse_args()

    snapshot = Snapshot.open(args.snapshot)
    instruments = json.loads(snapshot.market_path.read_bytes())["instruments"]
    if any(s["asset_class"] != "stock" or s["currency"] != "CNY" for s in instruments):
        parser.error("the input snapshot must contain only CNY stocks")

    result = backtest(
        snapshot, AllAMomentum(), schedule="week_end", initial_cash=1_000_000, detail=True
    )
    request = result.save(args.output)
    print(f"Input covers {len(instruments)} stocks and {len(snapshot.sessions)} trading sessions.")
    first = next((row for row in result.targets if row["weights"]), None)
    if first:
        names = ", ".join(first["weights"])
        print(f"Selected at the {first['decision_session']} close: {names}")
        print(f"First eligible open: {first['eligible_session']}")
    print(f"{result.summary['fill_count']} fills. Saved {request}")
    if result.summary["status"] != "completed":
        raise SystemExit(f"Incomplete run: {result.summary['first_issue']}")


if __name__ == "__main__":
    main()
