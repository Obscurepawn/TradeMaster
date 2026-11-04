"""Run a 200-quote moving average and buy-and-hold on the same data snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import Any, Literal

from trademaster import _native
from trademaster.backtest import BacktestDay, BacktestResult, Policy, Snapshot, backtest
from trademaster.backtest.snapshot import canonical
from trademaster.replay import replay

from . import broad_etf_data, long_history_data

WINDOW = 200
INITIAL_CASH = 100_000


@dataclass(frozen=True)
class Workload:
    symbol: str
    signal_instrument: str
    signal_feature: str | None
    policy: Policy
    output: Path


BROAD_ETF = Workload(
    broad_etf_data.SYMBOL,
    broad_etf_data.INDEX,
    broad_etf_data.INDEX_FEATURE,
    Policy(sell_stamp_duty_ppm=0, sse_transfer_fee_ppm=0),
    Path("artifacts/broad-etf"),
)
STOCK = Workload(
    long_history_data.SYMBOL,
    long_history_data.SYMBOL,
    None,
    Policy(),
    Path("artifacts/long-history"),
)
WORKLOADS = {"broad-etf": BROAD_ETF, "stock": STOCK}

type StrategyName = Literal["sma200", "buy_and_hold"]
NAMES: tuple[StrategyName, ...] = ("sma200", "buy_and_hold")


class LongHistoryStrategy:
    """At a quoted close, send a target only when the desired position changes."""

    def __init__(self, name: StrategyName, workload: Workload = BROAD_ETF) -> None:
        self.name = name
        self.workload = workload
        self.callbacks = 0
        self._prices: deque[Decimal] = deque()
        self._total = Decimal(0)
        self._long = False

    def on_decision(self, day: BacktestDay) -> dict[str, int] | None:
        self.callbacks += 1
        if self.name == "buy_and_hold" and self._long:
            return None
        symbol = self.workload.symbol
        feature = self.workload.signal_feature
        if feature is None:
            (latest,) = day.closes(symbol, limit=1)
            if latest is None:  # A suspended stock has no observation for the window.
                return None
        else:
            latest = day.scores(feature)[symbol]
            if latest is None:
                raise ValueError(f"Missing {feature} on {day.session}")
        # Compare on the decimal quote grid so equality stays flat.
        price = Decimal(str(latest))
        if len(self._prices) == WINDOW:
            self._total -= self._prices.popleft()
        self._prices.append(price)
        self._total += price
        if len(self._prices) < WINDOW:
            return None
        long = self.name == "buy_and_hold" or price * WINDOW > self._total
        if long == self._long:
            return None
        self._long = long
        return {symbol: 1} if long else {}


def _run(
    market: Snapshot, name: StrategyName, workload: Workload
) -> tuple[BacktestResult, float, int]:
    strategy = LongHistoryStrategy(name, workload)
    started = perf_counter_ns()
    result = backtest(
        market,
        strategy,
        schedule="daily_close",
        initial_cash=INITIAL_CASH,
        policy=workload.policy,
        detail=True,
    )
    elapsed_ms = (perf_counter_ns() - started) / 1e6
    if result.summary["status"] != "completed":
        raise ValueError(f"Incomplete {name} backtest: {result.raw['first_issue']}")
    if len(result.equity) != strategy.callbacks or strategy.callbacks != len(market.sessions):
        raise ValueError(f"Incomplete {name} daily callbacks or valuations")
    return result, elapsed_ms, strategy.callbacks


def _digest(result: BacktestResult) -> str:
    return hashlib.sha256(canonical([result.targets, result.raw])).hexdigest()


def _check_index_closes(
    market: Snapshot, symbol: str, feature: str, close_times: dict[str, str]
) -> None:
    # As-of scores can carry forward. The window requires a fresh observation at each close.
    timestamps, values = market._features.get(feature, {}).get(symbol, ([], []))
    closes = [datetime.fromisoformat(close_times[d.isoformat()]) for d in market.sessions]
    if timestamps != closes or any(v is None or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError(f"Expected a positive {feature} at every session close")


def run_benchmark(
    snapshot: Path,
    output: Path | None = None,
    *,
    repeats: int = 9,
    workload: Workload | None = None,
) -> dict[str, Any]:
    """Time full detailed backtests. Serialization, digest checks, and replay run off the clock."""
    if repeats < 1:
        raise ValueError("repeats must be positive")
    records: dict[str, Any] = {}
    results: dict[StrategyName, BacktestResult] = {}
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    snapshot_id = manifest.pop("snapshot_id")
    if hashlib.sha256(canonical(manifest)).hexdigest() != snapshot_id:
        raise ValueError("Snapshot manifest hash mismatch")
    for trial in range(repeats + 1):
        for name in NAMES if trial % 2 == 0 else reversed(NAMES):
            started = perf_counter_ns()
            market = Snapshot.open(snapshot)
            open_ms = (perf_counter_ns() - started) / 1e6
            if snapshot_id != market.snapshot_id:
                raise ValueError("Snapshot identity changed between trials")
            if workload is None:
                workload = next(
                    (w for w in WORKLOADS.values() if market.instruments == (w.symbol,)), None
                )
            if workload is None or market.instruments != (workload.symbol,):
                raise ValueError(f"Unexpected instruments for benchmark: {market.instruments}")
            if not records and workload.signal_feature is not None:
                _check_index_closes(
                    market, workload.symbol, workload.signal_feature, manifest["close_times"]
                )
            first, first_ms, callbacks = _run(market, name, workload)
            repeat, repeat_ms, _ = _run(market, name, workload)
            digest = _digest(first)
            if digest != _digest(repeat) or (trial and records[name]["digest"] != digest):
                raise ValueError(f"Different targets or ledger in {name} trial {trial}")
            if trial == 0:
                results[name] = first
                paid = sum(d["amount"] for d in first.raw.get("dividends", ()) if d["paid"])
                receivable = first.raw["daily"][-1].get("dividend_receivable", 0)
                records[name] = {
                    "digest": digest,
                    "callbacks": callbacks,
                    "target_count": len(first.targets),
                    "ending_equity_cny": str(first.equity[-1]["equity"]),
                    "net_return": str(first.summary["net_return"]),
                    "maximum_drawdown": str(first.summary["maximum_drawdown"]),
                    "total_costs_cny": str(first.summary["total_costs"]),
                    "cash_dividends_paid_cny": str(Decimal(paid) / 100_000_000),
                    "ending_dividends_receivable_cny": str(Decimal(receivable) / 100_000_000),
                    "valued_sessions": first.summary["valued_sessions"],
                    "fill_count": first.summary["fill_count"],
                    "batch_counts": first.summary["batch_counts"],
                    "samples_ms": [],
                }
            else:
                records[name]["samples_ms"].append(
                    {
                        "snapshot_open": open_ms,
                        "first_including_open": open_ms + first_ms,
                        "repeat": repeat_ms,
                    }
                )
            del market, first, repeat

    assert workload is not None
    output = output or workload.output
    for name, result in results.items():
        samples = records[name]["samples_ms"]
        records[name]["median_ms"] = {key: median(s[key] for s in samples) for key in samples[0]}
        request = result.save(output / name)
        if json.loads(replay(request)) != result.raw:
            raise ValueError(f"Saved {name} request produced a different ledger on replay")
    with (output / "equity.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("session", *NAMES))
        for sma, hold in zip(results["sma200"].equity, results["buy_and_hold"].equity, strict=True):
            if sma["session"] != hold["session"]:
                raise ValueError("Equity series have different dates")
            writer.writerow([sma["session"], sma["equity"], hold["equity"]])
    sample = results["sma200"]
    policy = workload.policy
    report = {
        "instrument": workload.symbol,
        "signal_instrument": workload.signal_instrument,
        "signal_feature": workload.signal_feature,
        "features_sha256": manifest["features"]["sha256"],
        "snapshot_id": snapshot_id,
        "market_sha256": sample.request["snapshot_sha256"],
        "first_session": sample.equity[0]["session"],
        "last_session": sample.equity[-1]["session"],
        "quoted_close_window": WINDOW,
        "initial_cash_cny": INITIAL_CASH,
        "policy": {**asdict(policy), "minimum_commission": str(policy.minimum_commission)},
        "detail": True,
        "warmup_pairs_per_strategy": 1,
        "timed_pairs_per_strategy": repeats,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "package_version": version("trademaster"),
            "native_sha256": hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest(),
        },
        "strategies": records,
    }
    (output / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def check_reference(report: dict[str, Any], reference: Path) -> None:
    """Compare immutable market and ledger identities; runtimes vary by machine."""
    expected = json.loads(reference.read_bytes())
    keys = ["snapshot_id", "market_sha256"]
    if "features_sha256" in expected or report.get("instrument") == BROAD_ETF.symbol:
        keys.append("features_sha256")
    for key in keys:
        if report[key] != expected[key]:
            raise ValueError(f"Reference mismatch for {key}: {report[key]} != {expected[key]}")
    for name in NAMES:
        digest = report["strategies"][name]["digest"]
        reference_digest = expected["strategies"][name]["digest"]
        if digest != reference_digest:
            raise ValueError(f"Reference mismatch for {name}: {digest} != {reference_digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="Build the market snapshot from TuShare or a verified local panel"
    )
    prepare.add_argument("--snapshot", type=Path)
    prepare.add_argument("--panel-manifest", type=Path)
    run = commands.add_parser("run", help="Time strategy and control on an existing snapshot")
    run.add_argument("--snapshot", type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--repeats", type=int, default=9)
    run.add_argument(
        "--check", type=Path, help="Compare market and ledger hashes with this JSON reference"
    )
    for command in (prepare, run):
        command.add_argument("--workload", choices=tuple(WORKLOADS), help="Default: broad-etf")
    args = parser.parse_args()
    workload = WORKLOADS.get(args.workload)
    if args.command == "prepare":
        if args.panel_manifest is not None:
            if workload == BROAD_ETF:
                parser.error("--panel-manifest applies only to the stock workload")
            workload = STOCK
        workload = workload or BROAD_ETF
        snapshot = args.snapshot or workload.output / "snapshot"
        if workload == STOCK:
            long_history_data.prepare_snapshot(snapshot, panel_manifest=args.panel_manifest)
        else:
            broad_etf_data.prepare_snapshot(snapshot)
        print(f"Snapshot: {snapshot}")
    else:
        if args.repeats < 1:
            parser.error("--repeats must be positive")
        snapshot = args.snapshot or (workload or BROAD_ETF).output / "snapshot"
        report = run_benchmark(snapshot, args.output, repeats=args.repeats, workload=workload)
        output = (
            args.output or (STOCK if report["instrument"] == STOCK.symbol else BROAD_ETF).output
        )
        if args.check is not None:
            try:
                check_reference(report, args.check)
            except (KeyError, OSError, TypeError, ValueError) as error:
                parser.exit(1, f"Reference check failed: {error}\n")
            print(f"Matched reference: {args.check}")
        for name, record in report["strategies"].items():
            print(
                f"{name}: CNY {record['ending_equity_cny']}, {record['fill_count']} fills, "
                f"repeat {record['median_ms']['repeat']:.2f} ms"
            )
        print(f"Results: {output / 'benchmark.json'}")


if __name__ == "__main__":
    main()
