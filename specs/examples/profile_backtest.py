"""Measure cold and repeated snapshot backtests with the installed TradeMaster SDK."""

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from time import perf_counter_ns

from trademaster.backtest import Snapshot, backtest
from trademaster.e2e.demo_strategies import NAMES, DemoStrategy


class TimedStrategy(DemoStrategy):
    def __init__(self, name):
        super().__init__(name)
        self.calls = 0
        self.elapsed_ns = 0

    def on_close(self, day):
        start = perf_counter_ns()
        target = super().on_close(day)
        self.elapsed_ns += perf_counter_ns() - start
        self.calls += 1
        return target


def measure(snapshot, name, detail):
    strategy = TimedStrategy(name)
    start = perf_counter_ns()
    result = backtest(
        snapshot,
        strategy,
        initial_cash=100_000,
        detail=detail,
        schedule="week_end" if name == "cross-sectional-momentum" else "daily_close",
    )
    elapsed_ms = (perf_counter_ns() - start) / 1e6
    payload = json.dumps([result.targets, result.raw], sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return (
        elapsed_ms,
        strategy.elapsed_ns / 1e6,
        strategy.calls,
        digest,
        str(result.equity[-1]["equity"]),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    records = {}
    modes = [(name, detail) for name in NAMES for detail in (False, True)]
    for run in range(args.repeats + 1):
        for name, detail in modes[run % len(modes) :] + modes[: run % len(modes)]:
            start = perf_counter_ns()
            market = Snapshot.open(args.snapshot)
            open_ms = (perf_counter_ns() - start) / 1e6
            first = measure(market, name, detail)
            repeat = measure(market, name, detail)
            assert first[2:] == repeat[2:]
            if run == 0:
                continue
            key = f"{name}/{'detail' if detail else 'summary'}"
            row = records.setdefault(
                key, {"digest": first[3], "calls": first[2], "equity": first[4], "samples_ms": []}
            )
            assert row["digest"] == first[3]
            row["samples_ms"].append(
                {
                    "open": open_ms,
                    "first_including_open": open_ms + first[0],
                    "repeat": repeat[0],
                    "strategy_first": first[1],
                    "strategy_repeat": repeat[1],
                }
            )
    for row in records.values():
        samples = row["samples_ms"]
        row["median_ms"] = {
            key: round(statistics.median(s[key] for s in samples), 4) for key in samples[0]
        }
    args.output.write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()
