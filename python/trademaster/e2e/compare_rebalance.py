"""Replay the same real-market decisions with both Rust portfolio accounting paths."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

from trademaster import _native
from trademaster.backtest import Snapshot
from trademaster.e2e import _write_atomic, verify_e2e_bundle
from trademaster.e2e.demo_data import load_demo_market
from trademaster.e2e.demo_strategies import (
    DEFAULT_PLAN,
    END,
    NAMES,
    START,
    UNIVERSE,
    run_snapshot_demo,
)

UNIT = 100_000_000


def _money(scaled: int) -> str:
    return f"{Decimal(scaled) / UNIT:.2f}"


def _csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    _write_atomic(path, stream.getvalue().encode())


def _json(path: Path, value: object) -> None:
    _write_atomic(
        path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    )


def _metrics(
    equity: dict[str, tuple[int, int]], fills: list[dict[str, Any]], costs: int
) -> dict[str, Any]:
    values = [value for _, value in equity.values()]
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(value, peak)
        drawdown = min(drawdown, value / peak - 1)
    return {
        "ending_equity": _money(values[-1]),
        "net_return": values[-1] / values[0] - 1,
        "max_drawdown": drawdown,
        "fills": len(fills),
        "buys": sum(f["side"] == "buy" for f in fills),
        "costs": _money(costs),
    }


@dataclass
class _TradeTotals:
    shares: int = 0
    notional: int = 0
    costs: int = 0

    def fill_price(self) -> str:
        if not self.shares:
            return ""
        average = Decimal(self.notional) / (self.shares * UNIT)
        return format(average.quantize(Decimal("0.00000001")).normalize(), "f")


def _compare_paths(legacy: dict[str, Any], current: dict[str, Any], folder: Path) -> dict[str, Any]:
    before = {
        row["event_time"][:10]: (int(row["cash_scaled"]), int(row["net_asset_value_scaled"]))
        for row in legacy["nav"]
        if row["event_kind"] == "bar_close"
    }
    after = {row["session"]: (row["cash"], row["equity"]) for row in current["daily"]}
    if list(after) != list(before) or current["status"] != "completed":
        raise ValueError(f"incomplete daily result: {current['status']} {current['first_issue']}")
    daily: list[list[object]] = []
    for day, (cash, value) in before.items():
        new_cash, new_value = after[day]
        daily.append(
            [
                day,
                _money(cash),
                _money(new_cash),
                _money(value),
                _money(new_value),
                _money(new_value - value),
            ]
        )
    _csv(
        folder / "equity.csv",
        ["session", "before_cash", "after_cash", "before_equity", "after_equity", "change"],
        daily,
    )

    def fill_rows(
        rows: list[dict[str, Any]], old: bool
    ) -> defaultdict[tuple[str, str, str], _TradeTotals]:
        groups: defaultdict[tuple[str, str, str], _TradeTotals] = defaultdict(_TradeTotals)
        suffix = "_scaled" if old else ""
        for fill in rows:
            day = fill["event_time"][:10] if old else fill["session"]
            group = groups[day, fill["instrument_id"], fill["side"]]
            shares = int(fill["quantity"])
            group.shares += shares
            group.notional += shares * int(fill["price" + suffix])
            group.costs += sum(
                int(fill[component + suffix])
                for component in ("commission", "tax", "transfer_fee", "slippage")
            )
        return groups

    previous = fill_rows(legacy["fills"], True)
    actual = fill_rows(current["fills"], False)
    old_cost = sum(group.costs for group in previous.values())
    new_cost = sum(group.costs for group in actual.values())
    keys = sorted(previous.keys() | actual.keys())
    trades: list[list[object]] = []
    for key in keys:
        old, new = previous[key], actual[key]
        trades.append(
            [
                *key,
                old.shares,
                new.shares,
                new.shares - old.shares,
                old.fill_price(),
                new.fill_price(),
                format(Decimal(old.costs) / UNIT, "f"),
                format(Decimal(new.costs) / UNIT, "f"),
            ]
        )
    _csv(
        folder / "trades.csv",
        [
            "session",
            "instrument",
            "side",
            "before_shares",
            "after_shares",
            "change",
            "before_fill_price",
            "after_fill_price",
            "before_costs",
            "after_costs",
        ],
        trades,
    )
    if new_cost != current["total_costs"]:
        raise ValueError("new daily ledger costs differ from the fill records")
    return {
        "before": _metrics(before, legacy["fills"], old_cost),
        "after": _metrics(after, current["fills"], new_cost),
        "net_equity_change": _money(
            next(reversed(after.values()))[1] - next(reversed(before.values()))[1]
        ),
        "changed_equity_sessions": sum(before[d][1] != after[d][1] for d in before),
        "changed_trade_rows": sum(previous[k].shares != actual[k].shares for k in keys),
        "batch_outcomes": dict(Counter(b["status"] for b in current["batches"])),
    }


def _bench(replays: list[Callable[[], bytes]], repeats: int) -> dict[str, object]:
    """Measure native event and snapshot replays, including decoding and result serialization."""
    names = ("before", "after", "summary")
    samples: list[list[float]] = [[], [], []]
    expected = [run() for run in replays]
    detailed, summary = (json.loads(payload) for payload in expected[1:])
    for field in (
        "daily",
        "batches",
        "status",
        "order_count",
        "fill_count",
        "rejected_order_count",
        "total_costs",
        "turnover_notional",
        "first_issue",
    ):
        if detailed[field] != summary[field]:
            raise ValueError(f"summary and detailed results disagree on {field}")
    for trial in range(repeats):
        for offset in range(len(names)):
            i = (trial + offset) % len(names)
            start = time.perf_counter_ns()
            output = replays[i]()
            samples[i].append((time.perf_counter_ns() - start) / 1e6)
            if output != expected[i]:
                raise ValueError("replay output changed between timing trials")
    result: dict[str, object] = {}
    for name, times in zip(names, samples, strict=True):
        result[name] = statistics.median(times)
        result[f"samples_{name}"] = times
    return result


def _verify_original_targets(request: dict[str, Any], strategy: dict[str, Any]) -> None:
    if request["snapshot_id"] != strategy["market_snapshot_id"]:
        raise ValueError("original request and decisions cite different market snapshots")
    expected = sorted(
        (
            d["decision_session"],
            d["execution_session"],
            symbol,
            int(Decimal(d["weights"].get(symbol, 0)) * UNIT),
            d["target_snapshot_id"],
        )
        for d in strategy["decisions"]
        if d["execution_session"] is not None
        for symbol in strategy["instruments"]
    )
    sent = sorted(
        (
            row["signal_time"][:10],
            row["eligible_execution_time"][:10],
            row["instrument_id"],
            int(row["value_scaled"]),
            row["snapshot_id"],
        )
        for row in request["signals"]
    )
    if sent != expected or any(s["intent_type"] != "target_weight" for s in request["signals"]):
        raise ValueError("original runner signals differ from the recorded strategy decisions")


def compare(
    *,
    data_root: Path,
    plan: str,
    legacy_root: Path,
    output: Path,
    repeats: int = 7,
) -> Path:
    if repeats < 1:
        raise ValueError("at least one timed run is required")
    market = load_demo_market(data_root, plan, START, END, UNIVERSE)
    output.mkdir(parents=True, exist_ok=True)
    snap = Snapshot.from_demo_market(market, output / "snapshot")
    strategies = {}
    for name in NAMES:
        archive = legacy_root / name
        verified = verify_e2e_bundle(archive / "bundle-manifest.json")
        saved = json.loads((archive / "strategy.json").read_bytes())
        if (
            saved["source_evidence"] != market.source_evidence
            or saved["config"]["plan_sha256"] != plan
        ):
            raise ValueError("the original run used different Tushare observations")
        legacy_request = verified.request_path.read_bytes()
        _verify_original_targets(json.loads(legacy_request), saved)
        legacy = json.loads(verified.result_path.read_bytes())
        result = run_snapshot_demo(snap, name)
        before_targets = {
            r["decision_session"]: {
                s: int(Decimal(w) * UNIT) for s, w in r["weights"].items() if Decimal(w)
            }
            for r in saved["decisions"]
            if r["weights"] is not None
        }
        new_targets = {
            r["decision_session"]: {s: w for s, w in r["weights"].items() if w}
            for r in result.request["run"]["targets"]
        }
        if before_targets != new_targets:
            raise ValueError(f"{name}: the two strategies produced different dated targets")

        folder = output / name
        folder.mkdir(exist_ok=True)
        request_bytes = json.dumps(result.request, separators=(",", ":"), allow_nan=False).encode()
        result.save(folder)
        info = _compare_paths(legacy, result.raw, folder)
        info["same_decisions"] = True
        info["target_count"] = len(new_targets)
        summary_request = {**result.request, "run": {**result.request["run"], "detail": False}}
        summary_bytes = json.dumps(summary_request, separators=(",", ":")).encode()
        info["timing_ms"] = _bench(
            [
                partial(_native.run_event, legacy_request),
                partial(_native.replay_request, request_bytes, str(folder)),
                partial(_native.replay_request, summary_bytes, str(folder)),
            ],
            repeats,
        )
        strategies[name] = info

    file = output / "comparison.json"
    _json(
        file,
        {
            "plan_sha256": plan,
            "snapshot_id": snap.snapshot_id,
            "sessions": len(market.days),
            "start": START.isoformat(),
            "end": END.isoformat(),
            "timed_runs": repeats,
            "environment": platform.platform(),
            "python": platform.python_version(),
            "native_extension_sha256": hashlib.sha256(
                Path(_native.__file__).read_bytes()
            ).hexdigest(),
            "timing_scope": "In-process archived event and snapshot requests, including decoding, snapshot file verification and result encoding.",
            "strategies": strategies,
        },
    )
    return file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data/research/full-a-20y"))
    parser.add_argument("--plan", default=DEFAULT_PLAN)
    parser.add_argument("--legacy-root", type=Path, default=Path("artifacts/demo-strategies"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/rebalance-comparison"))
    parser.add_argument("--repeats", type=int, default=7)
    print(compare(**vars(parser.parse_args())))


if __name__ == "__main__":
    main()
