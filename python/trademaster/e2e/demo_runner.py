"""Replay close-based demo targets with the existing daily Rust engine."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pyarrow as pa

from trademaster.contracts import SnapshotManifest
from trademaster.e2e import (
    E2ERunBundle,
    E2ERunnerClient,
    RunnerInstrument,
    RunnerRequest,
    RunnerSignal,
    _canonical_json,
    _write_atomic,
    attach_e2e_bundle_artifacts,
    verify_e2e_bundle,
)
from trademaster.e2e.demo_data import DemoMarket
from trademaster.e2e.real_tushare import _at, _bars_by_key, _events, _fee_schedule
from trademaster.reporting import BenchmarkSeries
from trademaster.research.tushare import CacheOnlyTushareClient
from trademaster.strategies.industry_fundamental_top5.data import StrategyDataCache
from trademaster.strategies.industry_fundamental_top5.real import (
    _snapshot_for_object,
    _venue_for_instrument,
)

_SCALE = 100_000_000


def _save_table(
    cache: StrategyDataCache,
    dataset: str,
    table: pa.Table,
    start: datetime,
    end: datetime,
    instruments: tuple[str, ...],
) -> SnapshotManifest:
    return _snapshot_for_object(
        published=cache.publish_canonical(dataset, table),
        dataset=dataset,
        as_of=end,
        event_start=start,
        event_end=end,
        known_at=end,
        instruments=instruments,
        fields=tuple(table.column_names),
        coverage_key=f"{dataset}:{start.date()}:{end.date()}",
        schema_version=f"{dataset}/v1",
    )


def run_demo_strategy(
    *,
    strategy_id: str,
    market: DemoMarket,
    instruments: tuple[str, ...],
    decisions: Sequence[dict[str, Decimal] | None],
    data_root: Path,
    output_dir: Path,
    strategy_config: dict[str, object],
) -> E2ERunBundle:
    """Estimate a long-only stock strategy and verify the saved bundle with Rust."""
    days = market.days
    if len(days) < 2 or len(days) != len(decisions) or tuple(sorted(set(days))) != days:
        raise ValueError("provide one decision per trading session and at least two sessions")
    if market.next_session <= days[-1]:
        raise ValueError("the T+1 settlement session must follow the last sample session")
    if not instruments or len(set(instruments)) != len(instruments):
        raise ValueError("the trading universe must contain distinct instruments")
    instruments = tuple(sorted(instruments))
    keys = ["trade_date", "instrument_id"]
    order: list[tuple[str, Literal["ascending", "descending"]]] = [
        (key, "ascending") for key in keys
    ]
    bars = market.bars.sort_by(order)
    status = market.status.sort_by(order)
    if not bars.select(keys).equals(status.select(keys)):
        raise ValueError("each daily price must have a matching status row")
    bars_by_key = _bars_by_key(bars)
    market_symbols = tuple(sorted({symbol for symbol, _ in bars_by_key}))
    if (
        len(bars_by_key) != len(bars)
        or set(bars_by_key) != {(symbol, day) for symbol in market_symbols for day in days}
        or not set(instruments) <= set(market_symbols)
    ):
        raise ValueError("the market sample must cover each candidate on every session")
    market_table = bars
    for column in ("up_limit", "down_limit", "suspended", "source_revision"):
        market_table = market_table.append_column(column, status[column])
    market_table = market_table.replace_schema_metadata(
        {"source_evidence": _canonical_json(market.source_evidence)}
    )
    open_times = {day: _at(day, 1, 30) for day in (*days, market.next_session)}
    close_times = tuple(_at(day, 7) for day in days)
    config_sha = hashlib.sha256(_canonical_json(strategy_config)).hexdigest()
    history: list[dict[str, object]] = []
    signals: list[RunnerSignal] = []
    with StrategyDataCache(
        root=data_root, client=CacheOnlyTushareClient(), clock=lambda: datetime.now(UTC)
    ) as cache:
        price_snapshot = _save_table(
            cache, "demo_market", market_table, open_times[days[0]], close_times[-1], market_symbols
        )
        snapshots = [price_snapshot]
        for i, weights in enumerate(decisions):
            close = close_times[i]
            execution_day = days[i + 1] if i + 1 < len(days) else None
            if weights is not None and (
                not weights.keys() <= set(instruments)
                or any(
                    not w.is_finite() or w < 0 or w > 1 or w * _SCALE != int(w * _SCALE)
                    for w in weights.values()
                )
                or sum(weights.values()) not in (0, 1)
            ):
                raise ValueError("v1 target weights must name candidates and sum to zero or one")
            record: dict[str, object] = {
                "decision_session": days[i].isoformat(),
                "decision_time": close.isoformat(),
                "execution_session": execution_day.isoformat()
                if weights is not None and execution_day is not None
                else None,
                "weights": None if weights is None else {s: str(w) for s, w in weights.items()},
            }
            history.append(record)
            if weights is None or execution_day is None:
                continue
            rows = [
                {
                    "instrument_id": symbol,
                    "weight": str(weights.get(symbol, Decimal(0))),
                    "decision_at": close,
                    "known_at": close,
                    "strategy_id": strategy_id,
                    "strategy_config_sha256": config_sha,
                }
                for symbol in instruments
            ]
            selection = _save_table(
                cache, "demo_targets", pa.Table.from_pylist(rows), close, close, instruments
            )
            snapshots.append(selection)
            record["target_snapshot_id"] = selection.snapshot_id
            for symbol in instruments:
                signals.append(
                    RunnerSignal(
                        signal_id=f"{selection.snapshot_id}:{symbol}",
                        instrument_id=symbol,
                        signal_time=close,
                        eligible_execution_time=open_times[execution_day],
                        intent_type="target_weight",
                        value_scaled=str(int(weights.get(symbol, 0) * _SCALE)),
                        reason="demo_close_target",
                        snapshot_id=selection.snapshot_id,
                    )
                )
    strategy_bytes = _canonical_json(
        {
            "strategy_id": strategy_id,
            "config": strategy_config,
            "instruments": instruments,
            "market_snapshot_id": price_snapshot.snapshot_id,
            "source_evidence": market.source_evidence,
            "decisions": history,
            "timing": "Retrospective daily history. Decisions use bars through the indicated close "
            "and are eligible at the next session open. Cutoffs refer to simulated "
            "session times, not the historical availability of this vendor data.",
        }
    )
    identity = hashlib.sha256(strategy_bytes).hexdigest()
    request = RunnerRequest(
        schema_id="trademaster.e2e-runner/v1",
        run_id=f"{strategy_id}-{identity}",
        strategy_id=strategy_id,
        snapshot_id=price_snapshot.snapshot_id,
        initial_cash_scaled=str(100_000 * _SCALE),
        initial_time=_at(days[0], 0),
        instruments=tuple(
            RunnerInstrument(
                instrument_id=s,
                asset_class="stock",
                venue=_venue_for_instrument(s),
                currency="CNY",
                buy_lot_size=100,
                tick_size_scaled="1000000",
                settlement="t1",
            )
            for s in instruments
        ),
        fee_schedule=_fee_schedule(),
        slippage_ppm=0,
        session_opens=tuple(open_times.values()),
        events=_events(
            days,
            instruments,
            bars,
            status=status,
            session_opens=open_times,
            snapshot_id=price_snapshot.snapshot_id,
        ),
        signals=tuple(signals),
    )
    benchmarks = tuple(
        BenchmarkSeries(
            benchmark_id=f"{s}:raw_close_buy_hold",
            event_times=close_times,
            close_values=tuple(Decimal(str(bars_by_key[s, day]["close"])) for day in days),
        )
        for s in instruments
    )
    bundle = E2ERunnerClient().execute(
        request,
        output_dir=output_dir,
        benchmarks=benchmarks,
        classifications={s: ("unclassified", "unclassified") for s in instruments},
        snapshot_manifests=tuple(snapshots),
        snapshot_data_root=data_root,
    )
    strategy_path = output_dir / "strategy.json"
    _write_atomic(strategy_path, strategy_bytes)
    attach_e2e_bundle_artifacts(bundle.manifest_path, artifacts={"strategy": strategy_path})
    return verify_e2e_bundle(bundle.manifest_path)
