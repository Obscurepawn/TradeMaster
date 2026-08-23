"""Credential-gated real Tushare end-to-end scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Literal, cast

import pyarrow as pa

from trademaster.contracts import (
    DatasetRequest,
    FactorContext,
    SignalContext,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.data import (
    CacheFirstDataPortal,
    DataConfig,
    DuckDbCatalog,
    ParquetObjectStore,
    ProviderPage,
    TushareProvider,
    default_dataset_registry,
    normalize_request,
)
from trademaster.factors import (
    CloseLevelFactor,
    FactorExecutor,
    FactorRegistry,
    MomentumFactor,
)
from trademaster.reporting import BenchmarkSeries
from trademaster.signals import (
    ThresholdQuantitySignalConfig,
    ThresholdQuantitySignalGenerator,
    TopNSignalConfig,
    TopNTargetWeightGenerator,
)

from . import (
    E2ERunBundle,
    E2ERunnerClient,
    E2ERunnerError,
    RunnerBar,
    RunnerEvent,
    RunnerFeeSchedule,
    RunnerInstrument,
    RunnerRequest,
    RunnerSignal,
    allocate_target_weights,
)

_UNIT = 100_000_000
_CROSS_DATES = (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7))
_ETF_DATES = (
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 1, 7),
    date(2025, 1, 8),
    date(2025, 1, 9),
    date(2025, 1, 10),
    date(2025, 1, 13),
)
_CROSS_INSTRUMENTS = ("600000.SH", "600036.SH", "601318.SH")
_ETF = "510300.SH"


@dataclass(frozen=True, slots=True)
class RealTushareE2EOutcome:
    cross_sectional: E2ERunBundle
    etf_timing: E2ERunBundle
    summary_path: Path
    provider_calls_before_cache_probe: int
    provider_calls_after_cache_probe: int


@dataclass(frozen=True, slots=True)
class DailyLimitRuleEvidence:
    rule_id: str
    limit_ratio_ppm: int
    tick_size_scaled: int

    def __post_init__(self) -> None:
        if (
            not self.rule_id
            or not 0 < self.limit_ratio_ppm < 1_000_000
            or self.tick_size_scaled <= 0
        ):
            raise ValueError("daily limit rule evidence is invalid")

    def limits(self, pre_close_scaled: int) -> tuple[int, int]:
        if pre_close_scaled <= 0:
            raise E2ERunnerError("limit rule requires positive previous close")

        def rounded(value: int) -> int:
            return (
                (value + self.tick_size_scaled // 2)
                // self.tick_size_scaled
                * self.tick_size_scaled
            )

        down = rounded(pre_close_scaled * (1_000_000 - self.limit_ratio_ppm) // 1_000_000)
        up = rounded(pre_close_scaled * (1_000_000 + self.limit_ratio_ppm) // 1_000_000)
        if down <= 0 or up < down:
            raise E2ERunnerError("limit rule produced invalid price bounds")
        return down, up


class _CountingProvider:
    def __init__(self, provider: TushareProvider) -> None:
        self.provider = provider
        self.calls: list[tuple[str, tuple[str, ...], str | None]] = []

    def fetch(
        self,
        request: DatasetRequest,
        *,
        coverage_keys: tuple[str, ...],
        page_token: str | None,
    ) -> ProviderPage:
        self.calls.append((request.dataset, coverage_keys, page_token))
        return self.provider.fetch(request, coverage_keys=coverage_keys, page_token=page_token)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def _coverage(days: tuple[date, ...]) -> tuple[str, ...]:
    return tuple(f"SSE:{day.isoformat()}" for day in days)


def _dataset_request(
    dataset: str,
    days: tuple[date, ...],
    *,
    instruments: tuple[str, ...] = (),
    fields: tuple[str, ...],
) -> DatasetRequest:
    return normalize_request(
        default_dataset_registry(),
        dataset=dataset,
        start=_at(days[0], 0),
        end=_at(days[-1], 7),
        instruments=instruments,
        fields=fields,
        coverage_keys=_coverage(days),
    )


def _scaled(value: object) -> int:
    return int((Decimal(str(value)) * Decimal(_UNIT)).to_integral_value(rounding=ROUND_DOWN))


def _runner_signal(row: dict[str, object]) -> RunnerSignal:
    value = cast(Decimal, row["value"])
    return RunnerSignal(
        signal_id=str(row["signal_id"]),
        instrument_id=str(row["instrument_id"]),
        signal_time=cast(datetime, row["signal_time"]),
        eligible_execution_time=cast(datetime, row["eligible_execution_time"]),
        intent_type="quantity",
        value_scaled=str(int(value * _UNIT)),
        reason=str(row["reason"]),
        snapshot_id=str(row["snapshot_id"]),
    )


def _bars_by_key(table: pa.Table) -> dict[tuple[str, date], dict[str, object]]:
    return {
        (str(row["instrument_id"]), cast(date, row["trade_date"])): row for row in table.to_pylist()
    }


def _runner_bar(
    row: dict[str, object],
    *,
    at_open: bool,
    status_row: dict[str, object] | None,
    limit_rule: DailyLimitRuleEvidence | None,
    evidence_prefix: str,
) -> RunnerBar:
    open_scaled = _scaled(row["open"])
    if at_open:
        low_scaled = high_scaled = close_scaled = open_scaled
        volume = 1
    else:
        low_scaled = _scaled(row["low"])
        high_scaled = _scaled(row["high"])
        close_scaled = _scaled(row["close"])
        volume = max(1, int(cast(float, row["volume"])))
    if status_row is None:
        if limit_rule is None:
            raise E2ERunnerError("market bar lacks exact status or limit-rule evidence")
        down_limit, up_limit = limit_rule.limits(_scaled(row["pre_close"]))
        raw_volume = float(cast(Any, row["volume"]))
        trading_status: Literal["tradable", "suspended"] = (
            "tradable" if raw_volume > 0 else "suspended"
        )
        evidence = f"{evidence_prefix}:limit-rule:{limit_rule.rule_id}"
    else:
        down_limit = _scaled(status_row["down_limit"])
        up_limit = _scaled(status_row["up_limit"])
        trading_status = "suspended" if bool(status_row["suspended"]) else "tradable"
        evidence = f"{evidence_prefix}:{status_row['source_revision']}"
    return RunnerBar(
        instrument_id=str(row["instrument_id"]),
        open_scaled=str(open_scaled),
        high_scaled=str(high_scaled),
        low_scaled=str(low_scaled),
        close_scaled=str(close_scaled),
        volume_units=str(volume),
        up_limit_scaled=str(up_limit),
        down_limit_scaled=str(down_limit),
        trading_status=trading_status,
        status_evidence_id=evidence,
    )


def _events(
    days: tuple[date, ...],
    instruments: tuple[str, ...],
    bars: pa.Table,
    *,
    status: pa.Table | None,
    limit_rule: DailyLimitRuleEvidence | None = None,
    session_opens: dict[date, datetime],
    snapshot_id: str,
) -> tuple[RunnerEvent, ...]:
    bar_rows = _bars_by_key(bars)
    status_rows = _bars_by_key(status) if status is not None else {}
    result: list[RunnerEvent] = []
    for index, day in enumerate(days):
        if index > 0:
            result.append(RunnerEvent(event_time=session_opens[day], kind="settlement", bars=()))
            result.append(
                RunnerEvent(
                    event_time=session_opens[day],
                    kind="session_open",
                    bars=tuple(
                        _runner_bar(
                            bar_rows[(instrument, day)],
                            at_open=True,
                            status_row=status_rows.get((instrument, day)),
                            limit_rule=limit_rule,
                            evidence_prefix=snapshot_id,
                        )
                        for instrument in instruments
                    ),
                )
            )
        result.append(
            RunnerEvent(
                event_time=_at(day, 7),
                kind="bar_close",
                bars=tuple(
                    _runner_bar(
                        bar_rows[(instrument, day)],
                        at_open=False,
                        status_row=status_rows.get((instrument, day)),
                        limit_rule=limit_rule,
                        evidence_prefix=snapshot_id,
                    )
                    for instrument in instruments
                ),
            )
        )
    return tuple(result)


def _fee_schedule() -> RunnerFeeSchedule:
    return RunnerFeeSchedule(
        schedule_id="cn-a-share-v1",
        commission_ppm=300,
        minimum_commission_scaled=str(5 * _UNIT),
        sell_stamp_duty_ppm=500,
        sse_transfer_fee_ppm=10,
        rounding_unit_scaled="1000000",
    )


def run_real_tushare_e2e(
    *,
    data_root: Path,
    output_root: Path,
    as_of: datetime,
    runner_command: tuple[str, ...],
) -> RealTushareE2EOutcome:
    """Run real cross-sectional and ETF timing scenarios, then prove cache reuse."""
    config = DataConfig(
        data_root=data_root,
        log_dir=data_root / "logs",
        etf_instruments=(_ETF,),
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    provider = _CountingProvider(
        TushareProvider.from_environment(config=config, registry=registry, clock=lambda: as_of)
    )
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=provider,
        clock=lambda: as_of,
    )
    requests: list[DatasetRequest] = []
    try:
        calendar_days = tuple(sorted(set(_ETF_DATES) | {date(2025, 1, 14)}))
        calendar_request = _dataset_request(
            "trade_calendar",
            calendar_days,
            fields=("is_open", "open_at", "close_at"),
        )
        calendar = portal.query(calendar_request, as_of=as_of)
        requests.append(calendar_request)
        calendar_rows = {
            cast(date, row["session_date"]): row
            for row in calendar.to_pylist()
            if bool(row["is_open"])
        }
        session_opens = {
            day: cast(datetime, calendar_rows[day]["open_at"]) for day in calendar_days
        }

        cross_factor_days = _CROSS_DATES[:-1]
        cross_factor_request = _dataset_request(
            "daily_bars",
            cross_factor_days,
            instruments=_CROSS_INSTRUMENTS,
            fields=("close",),
        )
        cross_cutoff = _at(cross_factor_days[-1], 8)
        cross_factor_bars = portal.query(cross_factor_request, as_of=cross_cutoff)
        cross_adjustment_request = _dataset_request(
            "adj_factors",
            cross_factor_days,
            instruments=_CROSS_INSTRUMENTS,
            fields=("adj_factor",),
        )
        cross_adjustments = portal.query(cross_adjustment_request, as_of=cross_cutoff)
        cross_factor_snapshot = portal.snapshot(
            SnapshotRequest(
                datasets=(cross_adjustment_request, cross_factor_request),
                as_of=cross_cutoff,
            )
        )
        requests.extend((cross_factor_request, cross_adjustment_request))
        adjustment_rows = _bars_by_key(cross_adjustments)
        adjusted_fields: Any = [
            pa.field("instrument_id", pa.string(), nullable=False),
            pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("close", pa.float64(), nullable=False),
        ]
        adjusted_input = pa.Table.from_pylist(
            [
                {
                    "instrument_id": str(row["instrument_id"]),
                    "event_time": row["event_time"],
                    "close": float(cast(Any, row["close"]))
                    * float(
                        cast(
                            Any,
                            adjustment_rows[
                                (str(row["instrument_id"]), cast(date, row["trade_date"]))
                            ]["adj_factor"],
                        )
                    ),
                }
                for row in cross_factor_bars.to_pylist()
            ],
            schema=pa.schema(adjusted_fields),
        )
        adjusted_input = bind_snapshot_provenance(adjusted_input, cross_factor_snapshot)
        factor = MomentumFactor(lookback_sessions=1)
        cross_factors = FactorExecutor(FactorRegistry((factor,))).compute(
            factor.spec.factor_id,
            factor.spec.version,
            FactorContext(
                as_of=cross_cutoff,
                snapshot=cross_factor_snapshot,
                inputs=adjusted_input,
            ),
        )
        weights = TopNTargetWeightGenerator(
            TopNSignalConfig(
                strategy_id="real-weekly-momentum",
                factor_id=factor.spec.factor_id,
                factor_version=factor.spec.version,
                top_n=2,
                minimum_valid_instruments=3,
            )
        ).generate(
            SignalContext(
                as_of=cross_cutoff,
                eligible_execution_time=session_opens[_CROSS_DATES[-1]],
                snapshot=cross_factor_snapshot,
                factors=cross_factors,
            )
        )
        cross_bars_request = _dataset_request(
            "daily_bars",
            _CROSS_DATES,
            instruments=_CROSS_INSTRUMENTS,
            fields=("open", "high", "low", "close", "volume", "pre_close"),
        )
        cross_status_request = _dataset_request(
            "daily_limits_status",
            _CROSS_DATES,
            instruments=_CROSS_INSTRUMENTS,
            fields=("up_limit", "down_limit", "suspended", "is_st"),
        )
        index_request = _dataset_request(
            "index_bars",
            _CROSS_DATES,
            instruments=("000016.SH",),
            fields=("close",),
        )
        cross_bars = portal.query(cross_bars_request, as_of=as_of)
        cross_status = portal.query(cross_status_request, as_of=as_of)
        index_bars = portal.query(index_request, as_of=as_of)
        requests.extend((cross_bars_request, cross_status_request, index_request))
        cross_market_snapshot = portal.snapshot(
            SnapshotRequest(
                datasets=tuple(
                    sorted(
                        (calendar_request, cross_bars_request, cross_status_request, index_request),
                        key=lambda item: (
                            item.dataset,
                            item.start,
                            item.end,
                            item.instruments,
                            item.fields,
                            item.coverage_keys,
                        ),
                    )
                ),
                as_of=as_of,
            )
        )
        cross_rows = _bars_by_key(cross_bars)
        allocated = allocate_target_weights(
            weights,
            target_nav_scaled=95_000 * _UNIT,
            open_prices_scaled={
                instrument: _scaled(cross_rows[(instrument, _CROSS_DATES[-1])]["open"])
                for instrument in _CROSS_INSTRUMENTS
            },
            lot_sizes={instrument: 100 for instrument in _CROSS_INSTRUMENTS},
        )
        cross_times = tuple(_at(day, 7) for day in _CROSS_DATES)
        index_close = {
            cast(date, row["trade_date"]): Decimal(str(row["close"]))
            for row in index_bars.to_pylist()
        }
        client = E2ERunnerClient(command=runner_command)
        cross_bundle = client.execute(
            RunnerRequest(
                schema_id="trademaster.e2e-runner/v1",
                run_id="real-cross-sectional-20250107",
                strategy_id="real-weekly-momentum",
                snapshot_id=cross_market_snapshot.snapshot_id,
                initial_cash_scaled=str(100_000 * _UNIT),
                initial_time=_at(_CROSS_DATES[0], 0),
                instruments=tuple(
                    RunnerInstrument(
                        instrument_id=instrument,
                        asset_class="stock",
                        venue="sse",
                        currency="CNY",
                        buy_lot_size=100,
                        tick_size_scaled="1000000",
                        settlement="t1",
                    )
                    for instrument in _CROSS_INSTRUMENTS
                ),
                fee_schedule=_fee_schedule(),
                slippage_ppm=0,
                session_opens=tuple(
                    session_opens[day] for day in (*_CROSS_DATES, date(2025, 1, 8))
                ),
                events=_events(
                    _CROSS_DATES,
                    _CROSS_INSTRUMENTS,
                    cross_bars,
                    status=cross_status,
                    session_opens=session_opens,
                    snapshot_id=cross_market_snapshot.snapshot_id,
                ),
                signals=tuple(_runner_signal(row) for row in allocated.to_pylist()),
            ),
            output_dir=output_root / "cross-sectional",
            benchmarks=(
                BenchmarkSeries(
                    benchmark_id="000016.SH",
                    event_times=cross_times,
                    close_values=tuple(index_close[day] for day in _CROSS_DATES),
                ),
            ),
            classifications={
                "600000.SH": ("bank", "large"),
                "600036.SH": ("bank", "large"),
                "601318.SH": ("insurance", "large"),
            },
            snapshot_manifests=(cross_factor_snapshot, cross_market_snapshot),
            snapshot_data_root=config.paths.data_root,
        )

        etf_factor = CloseLevelFactor()
        etf_signals: list[RunnerSignal] = []
        etf_factor_snapshots: list[SnapshotManifest] = []
        for cutoff_day, eligible_day in (
            (date(2025, 1, 7), date(2025, 1, 8)),
            (date(2025, 1, 10), date(2025, 1, 13)),
        ):
            history_days = tuple(day for day in _ETF_DATES if day <= cutoff_day)
            etf_factor_request = _dataset_request(
                "daily_bars",
                history_days,
                instruments=(_ETF,),
                fields=("close",),
            )
            cutoff = _at(cutoff_day, 8)
            etf_factor_bars = portal.query(etf_factor_request, as_of=cutoff)
            etf_factor_snapshot = portal.snapshot(
                SnapshotRequest(datasets=(etf_factor_request,), as_of=cutoff)
            )
            requests.append(etf_factor_request)
            etf_factor_snapshots.append(etf_factor_snapshot)
            factor_rows = FactorExecutor(FactorRegistry((etf_factor,))).compute(
                etf_factor.spec.factor_id,
                etf_factor.spec.version,
                FactorContext(
                    as_of=cutoff,
                    snapshot=etf_factor_snapshot,
                    inputs=etf_factor_bars,
                ),
            )
            generated = ThresholdQuantitySignalGenerator(
                ThresholdQuantitySignalConfig(
                    strategy_id="real-etf-threshold",
                    instrument_id=_ETF,
                    factor_id=etf_factor.spec.factor_id,
                    factor_version=etf_factor.spec.version,
                    buy_above=3.87,
                    sell_below=3.85,
                    target_quantity=1_000,
                )
            ).generate(
                SignalContext(
                    as_of=cutoff,
                    eligible_execution_time=session_opens[eligible_day],
                    snapshot=etf_factor_snapshot,
                    factors=factor_rows,
                )
            )
            if generated.num_rows != 1:
                raise RuntimeError(
                    "real ETF history did not produce the expected threshold crossing"
                )
            etf_signals.append(_runner_signal(generated.to_pylist()[0]))
        etf_bars_request = _dataset_request(
            "daily_bars",
            _ETF_DATES,
            instruments=(_ETF,),
            fields=("open", "high", "low", "close", "volume", "pre_close"),
        )
        etf_bars = portal.query(etf_bars_request, as_of=as_of)
        requests.append(etf_bars_request)
        etf_market_snapshot = portal.snapshot(
            SnapshotRequest(datasets=(etf_bars_request,), as_of=as_of)
        )
        etf_times = tuple(_at(day, 7) for day in _ETF_DATES)
        etf_rows = _bars_by_key(etf_bars)
        etf_bundle = client.execute(
            RunnerRequest(
                schema_id="trademaster.e2e-runner/v1",
                run_id="real-etf-round-trip-20250109",
                strategy_id="real-etf-threshold",
                snapshot_id=etf_market_snapshot.snapshot_id,
                initial_cash_scaled=str(100_000 * _UNIT),
                initial_time=_at(_ETF_DATES[0], 0),
                instruments=(
                    RunnerInstrument(
                        instrument_id=_ETF,
                        asset_class="etf",
                        venue="sse",
                        currency="CNY",
                        buy_lot_size=100,
                        tick_size_scaled="100000",
                        settlement="t1",
                    ),
                ),
                fee_schedule=_fee_schedule(),
                slippage_ppm=0,
                session_opens=tuple(session_opens[day] for day in (*_ETF_DATES, date(2025, 1, 14))),
                events=_events(
                    _ETF_DATES,
                    (_ETF,),
                    etf_bars,
                    status=None,
                    limit_rule=DailyLimitRuleEvidence(
                        rule_id="sse-etf-10pct-v1:510300.SH",
                        limit_ratio_ppm=100_000,
                        tick_size_scaled=100_000,
                    ),
                    session_opens=session_opens,
                    snapshot_id=etf_market_snapshot.snapshot_id,
                ),
                signals=tuple(etf_signals),
            ),
            output_dir=output_root / "etf-timing",
            benchmarks=(
                BenchmarkSeries(
                    benchmark_id="510300.SH:buy_hold",
                    event_times=etf_times,
                    close_values=tuple(
                        Decimal(str(etf_rows[(_ETF, day)]["close"])) for day in _ETF_DATES
                    ),
                ),
            ),
            classifications={_ETF: ("broad_market_etf", "large_blend")},
            snapshot_manifests=(*etf_factor_snapshots, etf_market_snapshot),
            snapshot_data_root=config.paths.data_root,
        )

        before_probe = len(provider.calls)
        for request in requests:
            portal.ensure(request)
        after_probe = len(provider.calls)
        if before_probe != after_probe:
            raise RuntimeError("cache probe unexpectedly called Tushare")
        output_root.mkdir(parents=True, exist_ok=True)
        summary_path = output_root / "real-e2e-summary.json"
        summary = {
            "schema_id": "trademaster.real-e2e-summary/v1",
            "as_of": as_of.isoformat(),
            "provider_calls_before_cache_probe": before_probe,
            "provider_calls_after_cache_probe": after_probe,
            "raw_parquet_count": len(tuple(config.paths.raw.rglob("*.parquet"))),
            "staging_parquet_count": len(tuple(config.paths.staging.rglob("*.parquet"))),
            "canonical_parquet_count": len(tuple(config.paths.canonical.rglob("*.parquet"))),
            "cross_sectional": {
                "request_sha256": cross_bundle.request_sha256,
                "result_sha256": cross_bundle.result_sha256,
            },
            "etf_timing": {
                "request_sha256": etf_bundle.request_sha256,
                "result_sha256": etf_bundle.result_sha256,
            },
        }
        summary_path.write_text(
            json.dumps(summary, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return RealTushareE2EOutcome(
            cross_sectional=cross_bundle,
            etf_timing=etf_bundle,
            summary_path=summary_path,
            provider_calls_before_cache_probe=before_probe,
            provider_calls_after_cache_probe=after_probe,
        )
    finally:
        catalog.close()


__all__ = ["RealTushareE2EOutcome", "run_real_tushare_e2e"]
