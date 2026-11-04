"""Run three strategies on the same verified Tushare history and compare their accounts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from trademaster.backtest import BacktestResult, DecisionDay, Snapshot, backtest
from trademaster.e2e import E2ERunBundle, _performance_and_trade, _RunnerResult, _write_atomic
from trademaster.e2e.demo_data import load_demo_market
from trademaster.e2e.demo_runner import run_demo_strategy
from trademaster.reporting import compute_strategy_metrics
from trademaster.strategies import demo

DEFAULT_PLAN = "6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633"
START = date(2024, 1, 2)
END = date(2024, 5, 31)
UNIVERSE = ("000001.SZ", "000725.SZ", "000977.SZ", "600036.SH", "600050.SH")
TIMING_STOCK = "600036.SH"
NAMES = ("cross-sectional-momentum", "moving-average", "macd")


def _summary(bundle: E2ERunBundle) -> dict[str, object]:
    result = _RunnerResult.model_validate_json(bundle.result_path.read_bytes(), strict=True)
    series, trades = _performance_and_trade(result)
    metrics = compute_strategy_metrics(series)
    return {
        "ending_equity": str(series.net_asset_values[-1]),
        "net_return": metrics.total_return,
        "maximum_drawdown": metrics.maximum_drawdown,
        "fees_tax_slippage": str(trades.total_cost),
        "buys": sum(fill.side == "buy" for fill in result.fills),
        "sells": sum(fill.side == "sell" for fill in result.fills),
        "rejected_orders": result.rejection_count,
        "snapshot_id": result.snapshot_id,
        "request_sha256": bundle.request_sha256,
        "result_sha256": bundle.result_sha256,
        "report": f"{result.strategy_id}/{bundle.report_path.name}",
    }


def run_demos(*, data_root: Path, plan: str, output: Path) -> Path:
    market = load_demo_market(data_root, plan, START, END, UNIVERSE)
    prices = {
        (row["instrument_id"], row["trade_date"]): float(row["close"]) * float(row["adj_factor"])
        for row in market.bars.select(
            ["instrument_id", "trade_date", "close", "adj_factor"]
        ).to_pylist()
    }
    closes = {
        instrument: [prices[instrument, day] for day in market.days] for instrument in UNIVERSE
    }
    momentum_params = {"lookback": 20, "top_n": 2}
    ma_params = {"fast": 5, "slow": 20}
    macd_params = {"fast": 12, "slow": 26, "signal": 9}
    ranked = demo.cross_sectional_momentum(closes, **momentum_params)
    next_days = (*market.days[1:], market.next_session)
    weekly = [
        target if day.isocalendar()[:2] != following.isocalendar()[:2] else None
        for day, following, target in zip(market.days, next_days, ranked, strict=True)
    ]
    examples = (
        (
            "cross-sectional-momentum",
            UNIVERSE,
            weekly,
            "cross_sectional_momentum",
            momentum_params,
            "week_end",
        ),
        (
            "moving-average",
            (TIMING_STOCK,),
            demo.moving_average_cross(TIMING_STOCK, closes[TIMING_STOCK], **ma_params),
            "moving_average_cross",
            ma_params,
            "daily_close",
        ),
        (
            "macd",
            (TIMING_STOCK,),
            demo.macd_cross(TIMING_STOCK, closes[TIMING_STOCK], **macd_params),
            "macd_cross",
            macd_params,
            "daily_close",
        ),
    )
    source = Path(demo.__file__).read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    summaries = {}
    for name, instruments, decisions, function, parameters, schedule in examples:
        bundle = run_demo_strategy(
            strategy_id=name,
            market=market,
            instruments=instruments,
            decisions=decisions,
            data_root=output / "derived-data",
            output_dir=output / name,
            strategy_config={
                "function": f"trademaster.strategies.demo.{function}",
                "source_sha256": source_hash,
                "source_python": source.decode("utf-8"),
                "parameters": parameters,
                "schedule": schedule,
                "signal_price": "raw_close_times_daily_adj_factor",
                "candidates": list(instruments),
                "plan_sha256": plan,
                "start": START.isoformat(),
                "end": END.isoformat(),
            },
        )
        summaries[name] = _summary(bundle)
    path = output / "summary.json"
    _write_atomic(
        path,
        json.dumps(
            {
                "start": START.isoformat(),
                "end": END.isoformat(),
                "sessions": len(market.days),
                "source": "Tushare Pro",
                "plan_sha256": plan,
                "initial_cash": "100000",
                "strategies": summaries,
            },
            sort_keys=True,
            indent=2,
        ).encode()
        + b"\n",
    )
    return path


class DemoStrategy:
    """Run the original indicator functions on the prices visible at each decision."""

    def __init__(self, name: str) -> None:
        self.name = name

    def on_decision(self, day: DecisionDay) -> dict[str, Decimal] | None:
        if self.name == "cross-sectional-momentum":
            lookback = 20
            prices = {s: day.closes(s, adjusted=True, limit=lookback + 1) for s in UNIVERSE}
            return demo.cross_sectional_momentum(prices, lookback=lookback, top_n=2)[-1]
        if self.name == "moving-average":
            slow = 20
            closes = day.closes(TIMING_STOCK, adjusted=True, limit=slow + 1)
            return demo.moving_average_cross(TIMING_STOCK, closes, fast=5, slow=slow)[-1]
        closes = day.closes(TIMING_STOCK, adjusted=True)
        return demo.macd_cross(TIMING_STOCK, closes, fast=12, slow=26, signal=9)[-1]


def run_snapshot_demo(snapshot: Snapshot, name: str) -> BacktestResult:
    if name not in NAMES:
        raise ValueError(f"unknown demo: {name}")
    return backtest(
        snapshot,
        demo.MacdTiming(TIMING_STOCK) if name == "macd" else DemoStrategy(name),
        schedule="week_end" if name == "cross-sectional-momentum" else "daily_close",
        initial_cash=100_000,
        detail=True,
    )


def run_snapshot_demos(*, data_root: Path, plan: str, output: Path) -> Path:
    market = load_demo_market(data_root, plan, START, END, UNIVERSE)
    snapshot = Snapshot.from_demo_market(market, output / "snapshot")
    summaries = {}
    for name in NAMES:
        result = run_snapshot_demo(snapshot, name)
        result.save(output / name)
        summaries[name] = result.summary
    path = output / "summary.json"
    _write_atomic(
        path,
        json.dumps(
            {
                "snapshot_id": snapshot.snapshot_id,
                "plan_sha256": plan,
                "start": START.isoformat(),
                "end": END.isoformat(),
                "sessions": len(market.days),
                "initial_cash": "100000",
                "strategies": summaries,
            },
            default=str,
            indent=2,
            sort_keys=True,
        ).encode()
        + b"\n",
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("events", "snapshot"), default="events")
    parser.add_argument("--data-root", type=Path, default=Path("data/research/full-a-20y"))
    parser.add_argument(
        "--plan", default=DEFAULT_PLAN, help="SHA of a completed Tushare research plan"
    )
    parser.add_argument(
        "--output", type=Path, help="Result directory (separate defaults per engine)"
    )
    args = parser.parse_args()
    if args.engine == "snapshot":
        run = run_snapshot_demos
        default_output = Path("artifacts/demo-snapshot")
    else:
        run = run_demos
        default_output = Path("artifacts/demo-strategies")
    print(
        run(
            data_root=args.data_root,
            plan=args.plan,
            output=args.output or default_output,
        )
    )


if __name__ == "__main__":
    main()
