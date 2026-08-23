"""Real Tushare-to-Rust E2E runner for the industry fundamental strategy."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import time as wall_time
from bisect import bisect_left
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import pyarrow as pa

from trademaster.contracts import (
    AvailabilityPolicy,
    DatasetCoverage,
    DatasetRequest,
    FactorContext,
    SnapshotManifest,
    SnapshotObject,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.e2e import (
    ClassificationObservation,
    E2ERunBundle,
    E2ERunnerClient,
    RunnerBar,
    RunnerEvent,
    RunnerFeeSchedule,
    RunnerInstrument,
    RunnerRequest,
    RunnerSignal,
    attach_e2e_bundle_artifacts,
    verify_e2e_bundle,
)
from trademaster.factors import (
    FactorArtifact,
    FactorManager,
    FactorScope,
    ManagedFactorRegistry,
)
from trademaster.factors.cross_section import assemble_grouped_factor_inputs
from trademaster.factors.fundamental import fundamental_factor_suite
from trademaster.reporting import (
    BenchmarkSeries,
    PerformanceSeries,
    TradeSummary,
    compute_benchmark_metrics,
    compute_strategy_metrics,
)

from . import (
    FundamentalRecord,
    IndustryFundamentalTop5Config,
    IndustryFundamentalTop5Strategy,
    SelectedTarget,
    scored_candidates_from_factors,
)
from .data import PublishedStrategyObject, StrategyDataCache, TushareQueryClient

_UNIT = 100_000_000
_VALUATION_FIELDS = (
    "ts_code",
    "trade_date",
    "pe_ttm",
    "pb",
    "dv_ttm",
    "total_mv",
)
_FINANCIAL_FIELDS = (
    "ts_code",
    "ann_date",
    "end_date",
    "update_flag",
    "roe",
    "grossprofit_margin",
    "ocf_to_or",
    "q_sales_yoy",
    "q_profit_yoy",
    "debt_to_assets",
)
_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
)
_ADJUSTMENT_FIELDS = ("ts_code", "trade_date", "adj_factor")


class FundamentalStrategyRunError(RuntimeError):
    """The real strategy run could not satisfy a required data or runtime contract."""


@dataclass(frozen=True, slots=True)
class RealStrategyConfig:
    start: date = date(2016, 8, 12)
    end: date = date(2026, 8, 11)
    initial_cash_cny: int = 500_000
    universe_index: str = "000300.SH"
    benchmark_id: str = "000300.SH"
    rebalance_months: tuple[int, ...] = (5, 11)
    rebalance_day_floor: int = 6
    top_per_industry: int = 5
    top_industries_by_mean_score: int | None = None
    industry_membership_policy: Literal[
        "historical_interval_required", "static_latest_experiment"
    ] = "historical_interval_required"
    commission_ppm: int = 300
    minimum_commission_cny: Decimal = Decimal(5)
    sell_stamp_duty_ppm: int = 500
    sse_transfer_fee_ppm: int = 10
    slippage_ppm: int = 500

    def __post_init__(self) -> None:
        if (
            self.end <= self.start
            or self.initial_cash_cny <= 0
            or not self.universe_index
            or not self.benchmark_id
            or self.rebalance_months != tuple(sorted(set(self.rebalance_months)))
            or any(month < 1 or month > 12 for month in self.rebalance_months)
            or not 1 <= self.rebalance_day_floor <= 28
            or self.top_per_industry < 1
            or self.industry_membership_policy
            not in {"historical_interval_required", "static_latest_experiment"}
            or (
                self.top_industries_by_mean_score is not None
                and self.top_industries_by_mean_score < 1
            )
            or not 0 < self.commission_ppm <= 1_000_000
            or self.minimum_commission_cny < 0
            or not self.minimum_commission_cny.is_finite()
            or not 0 <= self.sell_stamp_duty_ppm <= 1_000_000
            or not 0 <= self.sse_transfer_fee_ppm <= 1_000_000
            or not 0 <= self.slippage_ppm <= 1_000_000
        ):
            raise ValueError("real fundamental strategy config is invalid")


def _config_json_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_config_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _config_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return value


def _strategy_definition_payload(
    config: RealStrategyConfig,
    *,
    factor_definition_sha256s: tuple[str, ...],
) -> dict[str, object]:
    if factor_definition_sha256s != tuple(sorted(set(factor_definition_sha256s))) or any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in factor_definition_sha256s
    ):
        raise ValueError("factor definition hashes must be canonical")
    return {
        "schema_id": "trademaster.industry-fundamental-definition/v2",
        "run_config": _config_json_value(asdict(config)),
        "factor_definition_sha256s": list(factor_definition_sha256s),
    }


def _strategy_definition_sha256(
    config: RealStrategyConfig,
    *,
    factor_definition_sha256s: tuple[str, ...],
) -> str:
    return hashlib.sha256(
        json.dumps(
            _strategy_definition_payload(
                config,
                factor_definition_sha256s=factor_definition_sha256s,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class RealStrategyOutcome:
    bundle: E2ERunBundle
    summary_path: Path
    markdown_report_path: Path
    selection_paths: tuple[Path, ...]
    provider_calls_before_cache_probe: int
    provider_calls_after_cache_probe: int


class _RateLimitedClient:
    def __init__(self, client: TushareQueryClient, *, minimum_interval: float = 0.13) -> None:
        self.client = client
        self.minimum_interval = minimum_interval
        self.calls: list[tuple[str, int]] = []
        self._last_call = 0.0

    def query(
        self,
        api_name: str,
        *,
        fields: str,
        limit: int,
        offset: int,
        **params: object,
    ) -> Any:
        elapsed = wall_time.monotonic() - self._last_call
        if elapsed < self.minimum_interval:
            wall_time.sleep(self.minimum_interval - elapsed)
        self.calls.append((api_name, offset))
        for attempt in range(4):
            try:
                result = self.client.query(
                    api_name,
                    fields=fields,
                    limit=limit,
                    offset=offset,
                    **params,
                )
                self._last_call = wall_time.monotonic()
                return result
            except Exception:
                if attempt == 3:
                    raise
                wall_time.sleep(0.5 * (2**attempt))
        raise AssertionError("retry loop must return or raise")


def _client_from_environment() -> TushareQueryClient:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise FundamentalStrategyRunError("TUSHARE_TOKEN is required")
    tushare: Any = importlib.import_module("tushare")
    return cast(TushareQueryClient, tushare.pro_api(token))


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def _date(value: object) -> date:
    raw = str(value)
    if len(raw) != 8 or not raw.isdigit():
        raise FundamentalStrategyRunError(f"invalid compact date: {raw}")
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))


def _number(value: object) -> float | None:
    if value is None:
        return None
    parsed = float(cast(Any, value))
    return parsed if math.isfinite(parsed) else None


def _scaled(value: object) -> str:
    return str(int(Decimal(str(value)) * Decimal(_UNIT)))


def _venue_for_instrument(instrument_id: str) -> Literal["sse", "szse", "bse"]:
    if instrument_id.endswith(".SH"):
        return "sse"
    if instrument_id.endswith(".SZ"):
        return "szse"
    if instrument_id.endswith(".BJ"):
        return "bse"
    raise FundamentalStrategyRunError(f"unsupported A-share instrument suffix: {instrument_id}")


def _adjust_price(value: Decimal, factor: Decimal, base_factor: Decimal) -> Decimal:
    if value <= 0 or factor <= 0 or base_factor <= 0:
        raise FundamentalStrategyRunError("price adjustment inputs must be positive")
    return (value * factor / base_factor).quantize(Decimal("0.00000001"))


def _adjusted_execution_limits(
    limit: Mapping[str, object] | None,
    *,
    factor: Decimal,
    base_factor: Decimal,
) -> tuple[Decimal, Decimal]:
    if limit is None:
        raise FundamentalStrategyRunError("execution event lacks exact limit evidence")
    up = _number(limit.get("up_limit"))
    down = _number(limit.get("down_limit"))
    if up is None or down is None:
        raise FundamentalStrategyRunError("execution limit evidence is incomplete")
    return (
        _adjust_price(Decimal(str(up)), factor, base_factor),
        _adjust_price(Decimal(str(down)), factor, base_factor),
    )


def _weekly_event_days(
    open_days: tuple[date, ...],
    *,
    required: frozenset[date],
    start: date,
    end: date,
) -> tuple[date, ...]:
    eligible = tuple(day for day in open_days if start <= day <= end)
    if not eligible or not required <= frozenset(eligible):
        raise FundamentalStrategyRunError("weekly observations lack required event days")
    week_ends: dict[tuple[int, int], date] = {}
    for day in eligible:
        iso = day.isocalendar()
        week_ends[(iso.year, iso.week)] = day
    return tuple(sorted({*week_ends.values(), *required}))


def _retry_open_dates(
    open_days: tuple[date, ...], execution_date: date, *, attempts: int
) -> tuple[date, ...]:
    if attempts < 1 or execution_date not in open_days:
        raise ValueError("exit retry schedule is invalid")
    start = open_days.index(execution_date)
    return open_days[start : start + attempts]


def _rejection_counts(
    rejections: tuple[Mapping[str, object], ...],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for rejection in rejections:
        code = rejection.get("code")
        if not isinstance(code, str) or not code:
            raise FundamentalStrategyRunError("Rust rejection lacks a code")
        result[code] = result.get(code, 0) + 1
    return dict(sorted(result.items()))


def _instrument_hash(instruments: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(instruments, separators=(",", ":")).encode()).hexdigest()


def _snapshot_for_object(
    *,
    published: PublishedStrategyObject,
    dataset: str,
    as_of: datetime,
    event_start: datetime,
    event_end: datetime,
    known_at: datetime,
    instruments: tuple[str, ...],
    fields: tuple[str, ...],
    coverage_key: str,
    schema_version: str,
) -> SnapshotManifest:
    fields = tuple(sorted(fields))
    request = DatasetRequest(
        dataset=dataset,
        start=event_start,
        end=event_end,
        instruments=instruments,
        fields=fields,
        coverage_keys=(coverage_key,),
    )
    resolved_hash = _instrument_hash(instruments)
    snapshot_object = SnapshotObject(
        dataset=dataset,
        partition=coverage_key.replace(":", "="),
        uri=str(published.relative_path),
        sha256=published.sha256,
        schema_sha256=published.schema_sha256,
        schema_version=schema_version,
        fields=fields,
        coverage_keys=(coverage_key,),
        resolved_instrument_set_sha256=resolved_hash,
        resolved_instrument_count=len(instruments),
        row_count=published.row_count,
        event_time_start=event_start,
        event_time_end=event_end,
        known_at_max=known_at,
    )
    coverage = DatasetCoverage(
        request=request,
        covered_start=event_start,
        covered_end=event_end,
        fields=fields,
        resolved_instrument_set_sha256=resolved_hash,
        resolved_instrument_count=len(instruments),
        object_sha256s=(published.sha256,),
        row_count=published.row_count,
        known_at_max=known_at,
    )
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=(request,), as_of=as_of),
        availability_policy=AvailabilityPolicy(
            policy_id="strategy-pit/v1",
            known_at_field="known_at",
            publication_lag_policy_id="explicit-business-time/v1",
        ),
        objects=(snapshot_object,),
        coverages=(coverage,),
    )


def _fundamental_input_table(
    records: tuple[FundamentalRecord, ...],
    provenance: Mapping[str, Mapping[str, str]],
) -> pa.Table:
    if set(provenance) != {item.instrument_id for item in records}:
        raise FundamentalStrategyRunError(
            "fundamental input provenance must exactly cover candidate records"
        )
    metric_fields = (
        "pe_ttm",
        "pb",
        "dv_ttm",
        "roe",
        "grossprofit_margin",
        "ocf_to_or",
        "q_sales_yoy",
        "q_profit_yoy",
        "debt_to_assets",
    )
    evidence_fields = (
        "valuation_trade_date",
        "financial_ann_date",
        "financial_end_date",
        "financial_update_flag",
        "industry_mapping_in_date",
        "valuation_source_sha256",
        "financial_source_sha256",
        "industry_membership_source_sha256",
        "industry_taxonomy_source_sha256",
        "universe_source_sha256",
    )
    if any(
        len(provenance[item.instrument_id].get(field, "")) != 64
        for item in records
        for field in evidence_fields
        if field.endswith("_sha256")
    ):
        raise FundamentalStrategyRunError(
            "fundamental input source evidence must use SHA-256 identities"
        )
    schema_fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("industry_id", pa.string(), nullable=False),
        pa.field("industry_name", pa.string(), nullable=False),
        pa.field("signal_time", pa.timestamp("us", tz="UTC"), nullable=False),
        *(pa.field(name, pa.float64(), nullable=True) for name in metric_fields),
        pa.field("total_market_value", pa.float64(), nullable=False),
        *(pa.field(name, pa.string(), nullable=False) for name in evidence_fields),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("known_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
    schema = pa.schema(schema_fields)
    return pa.Table.from_pylist(
        [
            {
                "instrument_id": item.instrument_id,
                "industry_id": item.industry_id,
                "industry_name": item.industry_name,
                "signal_time": item.signal_time,
                **{name: getattr(item, name) for name in metric_fields},
                "total_market_value": item.total_market_value,
                **dict(provenance[item.instrument_id]),
                "event_time": item.signal_time,
                "known_at": item.signal_time,
            }
            for item in records
        ],
        schema=schema,
    )


def _rebalance_dates(
    open_days: tuple[date, ...], config: RealStrategyConfig
) -> tuple[tuple[date, date, date], ...]:
    result: list[tuple[date, date, date]] = []
    for year in range(config.start.year, config.end.year + 1):
        for month in config.rebalance_months:
            floor = date(year, month, config.rebalance_day_floor)
            signal_candidates = [day for day in open_days if floor <= day <= config.end]
            if not signal_candidates:
                continue
            signal = signal_candidates[0]
            if signal.month != month or signal < config.start:
                continue
            index = open_days.index(signal)
            if index == 0 or index + 1 >= len(open_days):
                continue
            result.append((open_days[index - 1], signal, open_days[index + 1]))
    if len(result) < 4:
        raise FundamentalStrategyRunError("fewer than four semiannual rebalances")
    return tuple(result)


def _active_industry(
    rows: list[dict[str, object]], instrument_id: str, at: date
) -> tuple[str, date, str | None] | None:
    eligible: list[tuple[date, str, str | None]] = []
    for row in rows:
        if str(row["ts_code"]) != instrument_id or row.get("in_date") in (None, ""):
            continue
        entered = _date(row["in_date"])
        exited = None if row.get("out_date") in (None, "") else _date(row["out_date"])
        if entered > at or (exited is not None and at > exited):
            continue
        source_sha = row.get("_source_content_sha256")
        eligible.append(
            (
                entered,
                str(row["l1_code"]),
                str(source_sha) if source_sha is not None else None,
            )
        )
    if not eligible:
        return None
    entered, industry_id, source_sha = max(eligible, key=lambda item: (item[0], item[1]))
    return industry_id, entered, source_sha


def _resolve_industry_membership(
    rows: list[dict[str, object]],
    instrument_id: str,
    at: date,
    *,
    policy: Literal["historical_interval_required", "static_latest_experiment"],
) -> tuple[str, date, str | None]:
    active = _active_industry(rows, instrument_id, at)
    if active is not None:
        return active
    if policy == "historical_interval_required":
        raise FundamentalStrategyRunError(
            f"historical industry interval is unavailable for {instrument_id} at {at}"
        )
    latest = [
        row
        for row in rows
        if str(row["ts_code"]) == instrument_id
        and row.get("in_date") not in (None, "")
        and str(row.get("is_new") or "Y") == "Y"
    ]
    if not latest:
        raise FundamentalStrategyRunError(
            f"static industry experiment lacks a current mapping for {instrument_id}"
        )
    row = max(
        latest,
        key=lambda item: (_date(item["in_date"]), str(item["l1_code"])),
    )
    source_sha = row.get("_source_content_sha256")
    return (
        str(row["l1_code"]),
        _date(row["in_date"]),
        str(source_sha) if source_sha is not None else None,
    )


def _latest_financial(rows: list[dict[str, object]], signal_date: date) -> dict[str, object] | None:
    eligible = [
        row
        for row in rows
        if row.get("ann_date") not in (None, "")
        and row.get("end_date") not in (None, "")
        and _date(row["ann_date"]) < signal_date
        and _date(row["end_date"]) <= signal_date
        and (signal_date - _date(row["end_date"])).days <= 400
        and str(row.get("update_flag") or "") == "0"
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            _date(row["end_date"]),
            _date(row["ann_date"]),
        ),
    )


def _market_cap_bucket(total_mv: float) -> str:
    if total_mv >= 10_000_000:
        return "large"
    if total_mv >= 2_000_000:
        return "mid"
    return "small"


def _signal(
    target: SelectedTarget,
    *,
    eligible_time: datetime,
    snapshot_id: str,
) -> RunnerSignal:
    identity = {
        "instrument_id": target.instrument_id,
        "signal_time": target.signal_time.isoformat(),
        "eligible_time": eligible_time.isoformat(),
        "weight": str(target.target_weight),
        "snapshot_id": snapshot_id,
    }
    return RunnerSignal(
        signal_id=hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        instrument_id=target.instrument_id,
        signal_time=target.signal_time,
        eligible_execution_time=eligible_time,
        intent_type="target_weight",
        value_scaled=str(int(target.target_weight * Decimal(_UNIT))),
        reason=f"industry_rank={target.industry_rank};score={target.score:.8f}",
        snapshot_id=snapshot_id,
    )


def _exit_signal(
    instrument_id: str,
    *,
    signal_time: datetime,
    eligible_time: datetime,
    snapshot_id: str,
) -> RunnerSignal:
    identity = {
        "instrument_id": instrument_id,
        "signal_time": signal_time.isoformat(),
        "eligible_time": eligible_time.isoformat(),
        "weight": "0",
        "snapshot_id": snapshot_id,
    }
    return RunnerSignal(
        signal_id=hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        instrument_id=instrument_id,
        signal_time=signal_time,
        eligible_execution_time=eligible_time,
        intent_type="target_weight",
        value_scaled="0",
        reason="semiannual_exit",
        snapshot_id=snapshot_id,
    )


def run_real_strategy(
    *,
    data_root: Path,
    output_root: Path,
    runner_command: tuple[str, ...],
    config: RealStrategyConfig | None = None,
    now: datetime | None = None,
    ingestion_clock: Callable[[], datetime] | None = None,
) -> RealStrategyOutcome:
    """Download/cache real Tushare data, run Rust, verify the bundle, and report."""
    config = config or RealStrategyConfig()
    now = now or _at(config.end + timedelta(days=1), 0)
    if now <= _at(config.end, 12):
        raise ValueError("run as-of must be later than the final market observation")
    ingestion_clock = ingestion_clock or (lambda: datetime.now(UTC))
    ingestion_time = ingestion_clock()
    ingestion_offset = ingestion_time.utcoffset()
    if (
        ingestion_time.tzinfo is None
        or ingestion_offset is None
        or ingestion_offset.total_seconds() != 0
    ):
        raise ValueError("ingestion clock must return UTC")
    provider = _RateLimitedClient(_client_from_environment())
    strategy = IndustryFundamentalTop5Strategy(
        IndustryFundamentalTop5Config(
            top_per_industry=config.top_per_industry,
            top_industries_by_mean_score=config.top_industries_by_mean_score,
        )
    )
    factor_suite = fundamental_factor_suite()
    factor_registry = ManagedFactorRegistry(
        factor_suite.registrations,
        external_dataset_fields=factor_suite.external_dataset_fields,
    )
    factor_definition_sha256s = tuple(
        sorted(item.definition.definition_sha256 for item in factor_suite.registrations)
    )
    strategy_definition_sha256 = _strategy_definition_sha256(
        config,
        factor_definition_sha256s=factor_definition_sha256s,
    )
    strategy_id = f"industry-fundamental-semiannual-{strategy_definition_sha256[:16]}"
    selection_paths: list[Path] = []
    factor_artifacts: list[FactorArtifact] = []
    factor_input_snapshots: list[SnapshotManifest] = []
    with (
        StrategyDataCache(root=data_root, client=provider, clock=ingestion_clock) as cache,
        FactorManager(
            root=data_root / "factors",
            registry=factor_registry,
            clock=ingestion_clock,
        ) as factor_manager,
    ):
        calendar = cache.query(
            "trade_cal",
            params={
                "exchange": "SSE",
                "start_date": (config.start - timedelta(days=10)).strftime("%Y%m%d"),
                "end_date": (config.end + timedelta(days=10)).strftime("%Y%m%d"),
                "is_open": "1",
            },
            fields=("exchange", "cal_date", "is_open", "pretrade_date"),
            page_limit=8000,
        )
        open_days = tuple(
            sorted(
                _date(row["cal_date"]) for row in calendar.to_pylist() if str(row["is_open"]) == "1"
            )
        )
        rebalances = _rebalance_dates(open_days, config)
        baseline_date = next((day for day in open_days if day >= config.start), None)
        if baseline_date is None or baseline_date >= rebalances[0][1]:
            raise FundamentalStrategyRunError("ten-year cash baseline is unavailable")

        industries_result = cache.query_with_evidence(
            "index_classify",
            params={"level": "L1", "src": "SW2021"},
            fields=("index_code", "industry_name", "level", "src"),
            page_limit=1000,
        )
        industries = industries_result.table
        industry_taxonomy_source_sha256 = industries_result.evidence.content_sha256
        industry_names = {
            str(row["index_code"]): str(row["industry_name"]) for row in industries.to_pylist()
        }
        membership_rows: list[dict[str, object]] = []
        for industry_id in sorted(industry_names):
            membership_result = cache.query_with_evidence(
                "index_member_all",
                params={"l1_code": industry_id},
                fields=("l1_code", "ts_code", "in_date", "out_date", "is_new"),
                page_limit=2000,
            )
            membership_rows.extend(
                {
                    **row,
                    "_source_content_sha256": (membership_result.evidence.content_sha256),
                }
                for row in membership_result.table.to_pylist()
            )

        candidates_by_signal: dict[date, tuple[str, ...]] = {}
        universe_source_by_signal: dict[date, str] = {}
        for _, signal_date, _ in rebalances:
            weights_result = cache.query_with_evidence(
                "index_weight",
                params={
                    "index_code": config.universe_index,
                    "start_date": (signal_date - timedelta(days=45)).strftime("%Y%m%d"),
                    "end_date": signal_date.strftime("%Y%m%d"),
                },
                fields=("index_code", "con_code", "trade_date", "weight"),
                page_limit=1000,
            )
            weights = weights_result.table
            universe_source_by_signal[signal_date] = weights_result.evidence.content_sha256
            rows = weights.to_pylist()
            if not rows:
                raise FundamentalStrategyRunError("index_weight returned no composition")
            effective = max(_date(row["trade_date"]) for row in rows)
            candidates_by_signal[signal_date] = tuple(
                sorted(
                    {str(row["con_code"]) for row in rows if _date(row["trade_date"]) == effective}
                )
            )
        candidate_union = tuple(
            sorted({item for values in candidates_by_signal.values() for item in values})
        )

        financial_by_instrument: dict[str, list[dict[str, object]]] = {}
        for instrument_id in candidate_union:
            financial_result = cache.query_with_evidence(
                "fina_indicator",
                params={
                    "ts_code": instrument_id,
                    "start_date": (config.start - timedelta(days=500)).strftime("%Y%m%d"),
                    "end_date": config.end.strftime("%Y%m%d"),
                },
                fields=_FINANCIAL_FIELDS,
                page_limit=100,
            )
            financial_by_instrument[instrument_id] = [
                {
                    **row,
                    "_source_content_sha256": (financial_result.evidence.content_sha256),
                }
                for row in financial_result.table.to_pylist()
            ]

        selections: list[tuple[date, date, date, tuple[SelectedTarget, ...], SnapshotManifest]] = []
        classification_timeline: list[ClassificationObservation] = []
        for valuation_date, signal_date, execution_date in rebalances:
            valuations_result = cache.query_with_evidence(
                "daily_basic",
                params={"trade_date": valuation_date.strftime("%Y%m%d")},
                fields=_VALUATION_FIELDS,
                page_limit=6000,
            )
            valuations = valuations_result.table
            valuation_by_instrument = {str(row["ts_code"]): row for row in valuations.to_pylist()}
            records: list[FundamentalRecord] = []
            selection_provenance: dict[str, dict[str, str]] = {}
            for instrument_id in candidates_by_signal[signal_date]:
                valuation = valuation_by_instrument.get(instrument_id)
                membership = _resolve_industry_membership(
                    membership_rows,
                    instrument_id,
                    signal_date,
                    policy=config.industry_membership_policy,
                )
                financial = _latest_financial(financial_by_instrument[instrument_id], signal_date)
                if valuation is None or financial is None:
                    continue
                industry_id, industry_mapping_in_date, membership_source_sha = membership
                financial_source_sha = financial.get("_source_content_sha256")
                if membership_source_sha is None or financial_source_sha is None:
                    raise FundamentalStrategyRunError(
                        "fundamental candidate lacks immutable source evidence"
                    )
                total_mv = _number(valuation["total_mv"])
                if total_mv is None or total_mv <= 0:
                    continue
                records.append(
                    FundamentalRecord(
                        instrument_id=instrument_id,
                        industry_id=industry_id,
                        industry_name=industry_names[industry_id],
                        signal_time=_at(signal_date, 7),
                        pe_ttm=_number(valuation["pe_ttm"]),
                        pb=_number(valuation["pb"]),
                        dv_ttm=_number(valuation["dv_ttm"]),
                        roe=_number(financial["roe"]),
                        grossprofit_margin=_number(financial["grossprofit_margin"]),
                        ocf_to_or=_number(financial["ocf_to_or"]),
                        q_sales_yoy=_number(financial["q_sales_yoy"]),
                        q_profit_yoy=_number(financial["q_profit_yoy"]),
                        debt_to_assets=_number(financial["debt_to_assets"]),
                        total_market_value=total_mv,
                    )
                )
                selection_provenance[instrument_id] = {
                    "valuation_trade_date": valuation_date.strftime("%Y%m%d"),
                    "financial_ann_date": str(financial["ann_date"]),
                    "financial_end_date": str(financial["end_date"]),
                    "financial_update_flag": str(financial["update_flag"]),
                    "industry_mapping_in_date": industry_mapping_in_date.strftime("%Y%m%d"),
                    "valuation_source_sha256": (valuations_result.evidence.content_sha256),
                    "financial_source_sha256": str(financial_source_sha),
                    "industry_membership_source_sha256": membership_source_sha,
                    "industry_taxonomy_source_sha256": (industry_taxonomy_source_sha256),
                    "universe_source_sha256": universe_source_by_signal[signal_date],
                }
            record_tuple = tuple(records)
            factor_input_table = _fundamental_input_table(record_tuple, selection_provenance)
            factor_input_published = cache.publish_canonical(
                "fundamental_factor_inputs", factor_input_table
            )
            signal_time = _at(signal_date, 7)
            factor_input_snapshot = _snapshot_for_object(
                published=factor_input_published,
                dataset="fundamental_factor_inputs",
                as_of=signal_time,
                event_start=signal_time,
                event_end=signal_time,
                known_at=signal_time,
                instruments=tuple(sorted(item.instrument_id for item in record_tuple)),
                fields=tuple(sorted(factor_input_table.column_names)),
                coverage_key=f"factor-input:{signal_date.isoformat()}",
                schema_version="fundamental-factor-input/v1",
            )
            factor_context = FactorContext(
                as_of=signal_time,
                snapshot=factor_input_snapshot,
                inputs=bind_snapshot_provenance(factor_input_table, factor_input_snapshot),
            )
            factor_input_snapshots.append(factor_input_snapshot)
            factor_scope = FactorScope(
                start=signal_time,
                end=signal_time,
                as_of=signal_time,
                instruments=tuple(sorted(item.instrument_id for item in record_tuple)),
                coverage_keys=(f"factor-input:{signal_date.isoformat()}",),
            )
            atomic_artifacts = tuple(
                factor_manager.materialize(
                    registration.definition.factor_id,
                    registration.definition.version,
                    context=factor_context,
                    scope=factor_scope,
                )
                for registration in factor_suite.atomic
            )
            grouped_inputs = assemble_grouped_factor_inputs(
                tuple(item.table for item in atomic_artifacts),
                dimensions=factor_input_table.select(
                    ("instrument_id", "event_time", "industry_id")
                ),
                group_field="industry_id",
            )
            composite_context = FactorContext(
                as_of=signal_time,
                snapshot=factor_input_snapshot,
                inputs=grouped_inputs,
            )
            industry_artifact = factor_manager.materialize(
                factor_suite.industry_composite.definition.factor_id,
                factor_suite.industry_composite.definition.version,
                context=composite_context,
                scope=factor_scope,
                parents=atomic_artifacts,
            )
            global_artifact = factor_manager.materialize(
                factor_suite.global_composite.definition.factor_id,
                factor_suite.global_composite.definition.version,
                context=composite_context,
                scope=factor_scope,
                parents=atomic_artifacts,
            )
            factor_artifacts.extend((*atomic_artifacts, industry_artifact, global_artifact))
            candidates = scored_candidates_from_factors(
                record_tuple,
                grouped_atomic_inputs=grouped_inputs,
                components=factor_suite.industry_components,
                industry_composite=industry_artifact.table,
                global_composite=global_artifact.table,
            )
            selected = strategy.select(candidates)
            selected_rows = [
                {
                    "selection_schema_id": "trademaster.strategy-selection/v1",
                    "strategy_id": strategy_id,
                    "strategy_definition_sha256": strategy_definition_sha256,
                    "instrument_id": item.instrument_id,
                    "industry_id": item.industry_id,
                    "industry_name": item.industry_name,
                    "signal_time": item.signal_time,
                    "score": item.score,
                    "valid_metric_count": item.valid_metric_count,
                    "total_market_value": item.total_market_value,
                    "industry_rank": item.industry_rank,
                    "industry_mean_score": item.industry_mean_score,
                    "industry_selection_rank": item.industry_selection_rank,
                    "target_weight": str(item.target_weight),
                    "factor_input_snapshot_id": factor_input_snapshot.snapshot_id,
                    "industry_factor_definition_sha256": (
                        industry_artifact.manifest.definition_sha256
                    ),
                    "industry_factor_materialization_id": (
                        industry_artifact.manifest.materialization_id
                    ),
                    "global_factor_definition_sha256": (global_artifact.manifest.definition_sha256),
                    "global_factor_materialization_id": (
                        global_artifact.manifest.materialization_id
                    ),
                    **selection_provenance[item.instrument_id],
                    "event_time": item.signal_time,
                    "known_at": item.signal_time,
                }
                for item in selected
            ]
            table = pa.Table.from_pylist(selected_rows)
            published = cache.publish_canonical("strategy_selections", table)
            selection_paths.append(published.path)
            snapshot = _snapshot_for_object(
                published=published,
                dataset="strategy_selections",
                as_of=signal_time,
                event_start=signal_time,
                event_end=signal_time,
                known_at=signal_time,
                instruments=tuple(sorted(item.instrument_id for item in selected)),
                fields=tuple(sorted(table.column_names)),
                coverage_key=f"selection:{signal_date.isoformat()}",
                schema_version="fundamental-selection/v2",
            )
            selections.append((valuation_date, signal_date, execution_date, selected, snapshot))
            for item in selected:
                classification_timeline.append(
                    ClassificationObservation(
                        instrument_id=item.instrument_id,
                        effective_at=item.signal_time,
                        industry_name=item.industry_name,
                        market_cap_bucket=_market_cap_bucket(item.total_market_value),
                    )
                )

        selected_union = tuple(
            sorted({item.instrument_id for _, _, _, selected, _ in selections for item in selected})
        )
        signals: list[RunnerSignal] = []
        ever_selected: set[str] = set()
        execution_dates = {execution for _, _, execution in rebalances}
        for _, signal_date, execution_date, selected, snapshot in selections:
            current = {item.instrument_id for item in selected}
            inactive = ever_selected - current
            retry_dates = _retry_open_dates(open_days, execution_date, attempts=20)
            for retry_date in retry_dates:
                signals.extend(
                    _exit_signal(
                        instrument_id,
                        signal_time=_at(signal_date, 7),
                        eligible_time=_at(retry_date, 1, 30),
                        snapshot_id=snapshot.snapshot_id,
                    )
                    for instrument_id in sorted(inactive)
                )
            signals.extend(
                _signal(
                    item,
                    eligible_time=_at(execution_date, 1, 30),
                    snapshot_id=snapshot.snapshot_id,
                )
                for item in selected
            )
            ever_selected.update(current)
        open_event_dates = {signal.eligible_execution_time.date() for signal in signals}
        price_start = rebalances[0][1] - timedelta(days=10)
        price_rows: dict[tuple[str, date], dict[str, object]] = {}
        adjustment_factors: dict[tuple[str, date], Decimal] = {}
        base_factors: dict[str, Decimal] = {}
        for instrument_id in selected_union:
            for row in cache.query(
                "daily",
                params={
                    "ts_code": instrument_id,
                    "start_date": price_start.strftime("%Y%m%d"),
                    "end_date": config.end.strftime("%Y%m%d"),
                },
                fields=_DAILY_FIELDS,
                page_limit=6000,
            ).to_pylist():
                price_rows[(instrument_id, _date(row["trade_date"]))] = row
            factors = cache.query(
                "adj_factor",
                params={
                    "ts_code": instrument_id,
                    "start_date": price_start.strftime("%Y%m%d"),
                    "end_date": config.end.strftime("%Y%m%d"),
                },
                fields=_ADJUSTMENT_FIELDS,
                page_limit=6000,
            ).to_pylist()
            if not factors:
                raise FundamentalStrategyRunError(
                    f"adj_factor returned no data for {instrument_id}"
                )
            latest_factor: tuple[date, Decimal] | None = None
            for factor_row in factors:
                factor = Decimal(str(factor_row["adj_factor"]))
                if factor <= 0:
                    raise FundamentalStrategyRunError("adj_factor must be positive")
                factor_date = _date(factor_row["trade_date"])
                adjustment_factors[(instrument_id, factor_date)] = factor
                if latest_factor is None or factor_date > latest_factor[0]:
                    latest_factor = (factor_date, factor)
            if latest_factor is None:
                raise FundamentalStrategyRunError(
                    f"adj_factor returned no usable data for {instrument_id}"
                )
            base_factors[instrument_id] = latest_factor[1]

        for key, row in tuple(price_rows.items()):
            instrument_id, day = key
            try:
                factor = adjustment_factors[key]
                base_factor = base_factors[instrument_id]
            except KeyError as error:
                raise FundamentalStrategyRunError(
                    f"daily price lacks same-day adj_factor for {instrument_id} {day}"
                ) from error
            adjusted = dict(row)
            for field in ("open", "high", "low", "close", "pre_close"):
                value = _number(row[field])
                if value is not None:
                    adjusted[field] = float(_adjust_price(Decimal(str(value)), factor, base_factor))
            adjusted["adj_factor"] = float(factor)
            adjusted["adj_base_factor"] = float(base_factor)
            adjusted["price_adjustment"] = "qfq_end_normalized"
            price_rows[key] = adjusted

        limit_rows: dict[tuple[str, date], dict[str, object]] = {}
        for execution_date in sorted(open_event_dates):
            for row in cache.query(
                "stk_limit",
                params={"trade_date": execution_date.strftime("%Y%m%d")},
                fields=("ts_code", "trade_date", "up_limit", "down_limit"),
                page_limit=5800,
            ).to_pylist():
                limit_rows[(str(row["ts_code"]), execution_date)] = row

        benchmark = cache.query(
            "index_daily",
            params={
                "ts_code": config.benchmark_id,
                "start_date": baseline_date.strftime("%Y%m%d"),
                "end_date": config.end.strftime("%Y%m%d"),
            },
            fields=("ts_code", "trade_date", "open", "high", "low", "close", "vol"),
            page_limit=8000,
        )
        benchmark_close = {
            _date(row["trade_date"]): Decimal(str(row["close"])) for row in benchmark.to_pylist()
        }
        required_event_days = frozenset({baseline_date})
        event_days = tuple(
            day
            for day in _weekly_event_days(
                open_days,
                required=required_event_days,
                start=baseline_date,
                end=config.end,
            )
            if day in benchmark_close
        )
        if not event_days or not required_event_days <= frozenset(event_days):
            raise FundamentalStrategyRunError("benchmark and calendar do not overlap")
        runtime_days = tuple(sorted({*event_days, *open_event_dates}))

        market_rows = [
            {
                **row,
                "instrument_id": instrument_id,
                "event_time": _at(day, 7),
                "known_at": _at(day, 12),
            }
            for (instrument_id, day), row in sorted(price_rows.items())
            if day in runtime_days
        ] + [
            {
                "ts_code": config.benchmark_id,
                "trade_date": day.strftime("%Y%m%d"),
                "open": None,
                "high": None,
                "low": None,
                "close": float(benchmark_close[day]),
                "pre_close": None,
                "vol": None,
                "amount": None,
                "adj_factor": None,
                "adj_base_factor": None,
                "price_adjustment": "benchmark_raw",
                "instrument_id": config.benchmark_id,
                "event_time": _at(day, 7),
                "known_at": _at(day, 12),
            }
            for day in event_days
        ]
        market_table = pa.Table.from_pylist(market_rows)
        market_published = cache.publish_canonical("strategy_market_inputs", market_table)
        market_as_of = max(now, _at(config.end, 13))
        market_snapshot = _snapshot_for_object(
            published=market_published,
            dataset="strategy_market_inputs",
            as_of=market_as_of,
            event_start=_at(runtime_days[0], 1, 30),
            event_end=_at(runtime_days[-1], 7),
            known_at=_at(event_days[-1], 12),
            instruments=tuple(sorted((*selected_union, config.benchmark_id))),
            fields=tuple(sorted(market_table.column_names)),
            coverage_key=f"market:{event_days[0]}:{event_days[-1]}",
            schema_version="strategy-market-input/v1",
        )

        grouped_price_history: dict[str, list[tuple[date, float]]] = {
            instrument_id: [] for instrument_id in selected_union
        }
        for (instrument_id, day), row in price_rows.items():
            close = _number(row["close"])
            if close is not None:
                grouped_price_history[instrument_id].append((day, close))
        price_history: dict[str, tuple[tuple[date, ...], tuple[float, ...]]] = {}
        for instrument_id, raw_observations in grouped_price_history.items():
            observations = sorted(raw_observations)
            price_history[instrument_id] = (
                tuple(day for day, _ in observations),
                tuple(close for _, close in observations),
            )
        last_close: dict[str, float] = {}
        events: list[RunnerEvent] = []
        for day in runtime_days:
            close_bars: list[RunnerBar] = []
            open_bars: list[RunnerBar] = []
            for instrument_id in selected_union:
                price_row = price_rows.get((instrument_id, day))
                if price_row is not None:
                    close_value = _number(price_row["close"])
                    open_value = _number(price_row["open"])
                    high_value = _number(price_row["high"])
                    low_value = _number(price_row["low"])
                    if None in (close_value, open_value, high_value, low_value):
                        raise FundamentalStrategyRunError("daily OHLC value is missing")
                    close = cast(float, close_value)
                    last_close[instrument_id] = close
                    limit = limit_rows.get((instrument_id, day))
                    factor = adjustment_factors[(instrument_id, day)]
                    base_factor = base_factors[instrument_id]
                    if day in open_event_dates:
                        up_value, down_value = _adjusted_execution_limits(
                            limit,
                            factor=factor,
                            base_factor=base_factor,
                        )
                        up = float(up_value)
                        down = float(down_value)
                    else:
                        up = close
                        down = close
                    raw_volume = _number(price_row["vol"])
                    tradable = raw_volume is not None and raw_volume > 0
                    volume = str(max(1, int(raw_volume or 0)))
                    close_bars.append(
                        RunnerBar(
                            instrument_id=instrument_id,
                            open_scaled=_scaled(cast(float, open_value)),
                            high_scaled=_scaled(cast(float, high_value)),
                            low_scaled=_scaled(cast(float, low_value)),
                            close_scaled=_scaled(close),
                            volume_units=volume,
                            up_limit_scaled=_scaled(up),
                            down_limit_scaled=_scaled(down),
                            trading_status="tradable" if tradable else "suspended",
                            status_evidence_id=(
                                f"tushare:daily+stk_limit:{day}:present"
                                if day in open_event_dates
                                else f"tushare:daily:{day}:valuation-only"
                            ),
                        )
                    )
                    if day in open_event_dates:
                        open_price = cast(float, open_value)
                        open_bars.append(
                            RunnerBar(
                                instrument_id=instrument_id,
                                open_scaled=_scaled(open_price),
                                high_scaled=_scaled(open_price),
                                low_scaled=_scaled(open_price),
                                close_scaled=_scaled(open_price),
                                volume_units="1",
                                up_limit_scaled=_scaled(up),
                                down_limit_scaled=_scaled(down),
                                trading_status="tradable" if tradable else "suspended",
                                status_evidence_id=f"tushare:daily+stk_limit:{day}:present",
                            )
                        )
                else:
                    if instrument_id not in last_close:
                        history_days, history_closes = price_history[instrument_id]
                        prior_index = bisect_left(history_days, day) - 1
                        if prior_index >= 0:
                            last_close[instrument_id] = history_closes[prior_index]
                    if instrument_id not in last_close:
                        continue
                    close = last_close[instrument_id]
                    suspended = RunnerBar(
                        instrument_id=instrument_id,
                        open_scaled=_scaled(close),
                        high_scaled=_scaled(close),
                        low_scaled=_scaled(close),
                        close_scaled=_scaled(close),
                        volume_units="1",
                        up_limit_scaled=_scaled(close * 2),
                        down_limit_scaled=_scaled(max(close / 2, 0.01)),
                        trading_status="suspended",
                        status_evidence_id=f"tushare:daily:{day}:absent",
                    )
                    close_bars.append(suspended)
                    if day in open_event_dates:
                        open_bars.append(suspended)
            close_bars.sort(key=lambda item: item.instrument_id)
            open_bars.sort(key=lambda item: item.instrument_id)
            if day in open_event_dates:
                opening = _at(day, 1, 30)
                events.append(RunnerEvent(event_time=opening, kind="settlement", bars=()))
                events.append(
                    RunnerEvent(event_time=opening, kind="session_open", bars=tuple(open_bars))
                )
            if day in event_days:
                events.append(
                    RunnerEvent(event_time=_at(day, 7), kind="bar_close", bars=tuple(close_bars))
                )

        request = RunnerRequest(
            schema_id="trademaster.e2e-runner/v1",
            run_id=(
                f"{strategy_id}-{event_days[0]}-{event_days[-1]}-{market_snapshot.snapshot_id[:16]}"
            ),
            strategy_id=strategy_id,
            snapshot_id=market_snapshot.snapshot_id,
            initial_cash_scaled=str(config.initial_cash_cny * _UNIT),
            initial_time=_at(event_days[0], 0),
            instruments=tuple(
                RunnerInstrument(
                    instrument_id=instrument_id,
                    asset_class="stock",
                    venue=_venue_for_instrument(instrument_id),
                    currency="CNY",
                    buy_lot_size=100,
                    tick_size_scaled="1000000",
                    settlement="t1",
                )
                for instrument_id in selected_union
            ),
            fee_schedule=RunnerFeeSchedule(
                schedule_id="cn-a-share-fundamental-v1",
                commission_ppm=config.commission_ppm,
                minimum_commission_scaled=str(int(config.minimum_commission_cny * Decimal(_UNIT))),
                sell_stamp_duty_ppm=config.sell_stamp_duty_ppm,
                sse_transfer_fee_ppm=config.sse_transfer_fee_ppm,
                rounding_unit_scaled="1000000",
            ),
            slippage_ppm=config.slippage_ppm,
            session_opens=tuple(
                _at(day, 1, 30) for day in open_days if baseline_date < day <= config.end
            ),
            events=tuple(events),
            signals=tuple(signals),
        )
        benchmark_series = BenchmarkSeries(
            benchmark_id=config.benchmark_id,
            event_times=tuple(_at(day, 7) for day in event_days),
            close_values=tuple(benchmark_close[day] for day in event_days),
        )
        bundle = E2ERunnerClient(command=runner_command).execute(
            request,
            output_dir=output_root,
            benchmarks=(benchmark_series,),
            classifications=tuple(
                sorted(
                    classification_timeline,
                    key=lambda item: (item.effective_at, item.instrument_id),
                )
            ),
            snapshot_manifests=(
                *(snapshot for _, _, _, _, snapshot in selections),
                *factor_input_snapshots,
                market_snapshot,
            ),
            factor_artifacts=tuple(factor_artifacts),
            snapshot_data_root=cache.root,
            annual_sessions=52,
        )
        before_probe = len(provider.calls)
        # Every raw request is looked up again through DuckDB and immutable Parquet.
        # Re-running the complete function in a new process is the stronger final cache proof.
        for path in cache.cached_objects():
            if not path.is_file():
                raise FundamentalStrategyRunError("cached object disappeared")
        after_probe = len(provider.calls)
        summary_path = output_root / "strategy-summary.json"
        result = json.loads(bundle.result_path.read_text(encoding="utf-8"))
        close_nav = [item for item in result["nav"] if item["event_kind"] == "bar_close"]
        performance = PerformanceSeries(
            event_times=tuple(datetime.fromisoformat(item["event_time"]) for item in close_nav),
            net_asset_values=tuple(
                Decimal(item["net_asset_value_scaled"]) / Decimal(_UNIT) for item in close_nav
            ),
            cash_values=tuple(Decimal(item["cash_scaled"]) / Decimal(_UNIT) for item in close_nav),
            market_values=tuple(
                Decimal(item["market_value_scaled"]) / Decimal(_UNIT) for item in close_nav
            ),
        )
        metrics = compute_strategy_metrics(performance, annual_sessions=52)
        benchmark_metrics = compute_benchmark_metrics(
            performance, benchmark_series, annual_sessions=52
        )
        gross_notional_scaled = sum(
            int(fill["price_scaled"]) * int(fill["quantity"]) for fill in result["fills"]
        )
        average_nav_scaled = sum(int(item["net_asset_value_scaled"]) for item in close_nav) // len(
            close_nav
        )
        trade_summary = TradeSummary.from_scaled_values(
            gross_traded_notional_scaled=gross_notional_scaled,
            commission_scaled=sum(int(fill["commission_scaled"]) for fill in result["fills"]),
            tax_scaled=sum(int(fill["tax_scaled"]) for fill in result["fills"]),
            transfer_fee_scaled=sum(int(fill["transfer_fee_scaled"]) for fill in result["fills"]),
            slippage_scaled=sum(int(fill["slippage_scaled"]) for fill in result["fills"]),
            average_nav_scaled=average_nav_scaled,
        )
        rejection_codes = _rejection_counts(tuple(result["rejections"]))
        benchmark_total_return = float(
            benchmark_series.close_values[-1] / benchmark_series.close_values[0] - 1
        )
        final_nav = performance.net_asset_values[-1]
        final_cash = performance.cash_values[-1]
        final_targets = {item.instrument_id for item in selections[-1][3]}
        residual_positions = [
            position
            for position in result["final_positions"]
            if position["instrument_id"] not in final_targets
        ]
        residual_weight = Decimal(
            sum(int(position["market_value_scaled"]) for position in residual_positions)
        ) / (final_nav * Decimal(_UNIT))
        summary = {
            "schema_id": "trademaster.industry-fundamental-summary/v3",
            "run_id": result["run_id"],
            "strategy_id": strategy_id,
            "strategy_definition_sha256": strategy_definition_sha256,
            "strategy_definition": _strategy_definition_payload(
                config,
                factor_definition_sha256s=factor_definition_sha256s,
            ),
            "universe_index": config.universe_index,
            "benchmark_id": config.benchmark_id,
            "start": event_days[0].isoformat(),
            "end": event_days[-1].isoformat(),
            "initial_cash_cny": config.initial_cash_cny,
            "valuation_frequency": "weekly",
            "annual_sessions": 52,
            "price_adjustment": "adj_factor_qfq_end_normalized",
            "execution_accounting_precision": (
                "continuous_total_return_price_approximation_not_exact_corporate_actions"
            ),
            "industry_classification": config.industry_membership_policy,
            "industry_classification_is_historical_pit": (
                config.industry_membership_policy == "historical_interval_required"
            ),
            "rebalance_count": len(selections),
            "top_per_industry": config.top_per_industry,
            "top_industries_by_mean_score": config.top_industries_by_mean_score,
            "factor_definition_sha256s": list(factor_definition_sha256s),
            "factor_materialization_ids": sorted(
                {item.manifest.materialization_id for item in factor_artifacts}
            ),
            "factor_materialization_count": len(
                {item.manifest.materialization_id for item in factor_artifacts}
            ),
            "factor_catalog": "factors/catalog.duckdb",
            "selection_counts": [len(selected) for _, _, _, selected, _ in selections],
            "selected_union_count": len(selected_union),
            "provider_calls_before_cache_probe": before_probe,
            "provider_calls_after_cache_probe": after_probe,
            "request_sha256": bundle.request_sha256,
            "result_sha256": bundle.result_sha256,
            "order_count": result["order_count"],
            "execution_count": result["execution_count"],
            "rejection_count": result["rejection_count"],
            "final_position_count": len(result["final_positions"]),
            "non_target_residual_position_count": len(residual_positions),
            "non_target_residual_instruments": sorted(
                str(position["instrument_id"]) for position in residual_positions
            ),
            "non_target_residual_weight": float(residual_weight),
            "initial_nav_cny": str(performance.net_asset_values[0]),
            "final_nav_cny": str(final_nav),
            "final_cash_cny": str(final_cash),
            "final_cash_weight": float(final_cash / final_nav),
            "total_return": metrics.total_return,
            "annualized_return": metrics.annualized_return,
            "annualized_volatility": metrics.annualized_volatility,
            "sharpe_ratio": metrics.sharpe_ratio,
            "sortino_ratio": metrics.sortino_ratio,
            "calmar_ratio": metrics.calmar_ratio,
            "maximum_drawdown": metrics.maximum_drawdown,
            "maximum_recovery_days": metrics.maximum_recovery_days,
            "longest_underwater_days": metrics.longest_underwater_days,
            "positive_week_ratio": metrics.positive_session_ratio,
            "best_week_return": metrics.best_session_return,
            "worst_week_return": metrics.worst_session_return,
            "value_at_risk_95": metrics.value_at_risk_95,
            "conditional_value_at_risk_95": metrics.conditional_value_at_risk_95,
            "benchmark_total_return": benchmark_total_return,
            "tracking_error": benchmark_metrics.tracking_error,
            "information_ratio": benchmark_metrics.information_ratio,
            "beta": benchmark_metrics.beta,
            "alpha": benchmark_metrics.alpha,
            "correlation": benchmark_metrics.correlation,
            "turnover_rate": trade_summary.turnover_rate,
            "gross_traded_notional_cny": str(trade_summary.gross_traded_notional),
            "commission_cny": str(trade_summary.commission),
            "tax_cny": str(trade_summary.tax),
            "transfer_fee_cny": str(trade_summary.transfer_fee),
            "slippage_cny": str(trade_summary.slippage),
            "total_cost_cny": str(trade_summary.total_cost),
            "rejection_codes": rejection_codes,
            "exit_retry_trading_days": 20,
            "raw_parquet_count": len(cache.cached_objects()),
            "canonical_parquet_count": len(tuple((cache.root / "canonical").rglob("*.parquet"))),
            "execution_dates": sorted(day.isoformat() for day in execution_dates),
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        markdown_report_path = output_root / "strategy-report.md"
        markdown_report_path.write_text(
            "\n".join(
                (
                    (
                        f"# 行业基本面 Top{config.top_per_industry} 研究近似报告"
                        if config.top_industries_by_mean_score is None
                        else (
                            "# 基本面最强"
                            f"Top{config.top_industries_by_mean_score}行业 × "
                            f"行业内Top{config.top_per_industry} 研究近似报告"
                        )
                    ),
                    "",
                    f"- 区间：{event_days[0]} 至 {event_days[-1]}（周频估值，52 期年化）",
                    f"- 初始/期末净值：{performance.net_asset_values[0]} / {final_nav} CNY",
                    f"- 总收益率：{metrics.total_return:.2%}",
                    f"- 年化收益率：{metrics.annualized_return:.2%}",
                    f"- 年化波动率：{metrics.annualized_volatility:.2%}",
                    f"- Sharpe / Sortino：{metrics.sharpe_ratio!s} / {metrics.sortino_ratio!s}",
                    f"- 最大回撤：{metrics.maximum_drawdown:.2%}",
                    f"- 基准 {config.benchmark_id} 同期价格收益：{benchmark_total_return:.2%}",
                    f"- 样本池指数：{config.universe_index}",
                    f"- 策略定义：{strategy_definition_sha256}",
                    f"- 换手率：{trade_summary.turnover_rate:.2f}x",
                    f"- 总交易成本：{trade_summary.total_cost} CNY",
                    f"- 调仓/成交/拒单：{len(selections)} / {result['execution_count']} / {result['rejection_count']}",
                    f"- 期末现金比例：{final_cash / final_nav:.2%}",
                    f"- 期末非目标残留：{len(residual_positions)} 只，{residual_weight:.2%} NAV",
                    "",
                    "## 方法与数据口径",
                    "",
                    (
                        f"每个调仓时点取历史 {config.universe_index} 成分，只使用 update_flag=0 的原始财务版本且要求公告日在信号日前，并结合前一交易日估值进行行业内评分；"
                        + (
                            "保留全部可用 SW2021 一级行业，"
                            if config.top_industries_by_mean_score is None
                            else (
                                "同样九项指标先在全体候选股票间 winsorize、标准化并加权，按各行业合格成分股全市场综合分的等权均值，选择最高的"
                                f"{config.top_industries_by_mean_score}个 SW2021 一级行业，"
                            )
                        )
                        + f"每个保留行业选择行业内综合分最高的{config.top_per_industry}只，全部入选标的目标等权，下一交易日开盘由 Rust 按 100 股整数手、T+1、涨跌停、费用与滑点执行。退出失败会在 20 个交易日内逐日重试，并在后续调仓继续重试。股票价格用 adj_factor 构造区间末端归一的连续序列。"
                    ),
                    "",
                    "## 重要限制",
                    "",
                    (
                        "行业分类按 index_member_all 的 in_date/out_date 区间解析；正式模式只要候选股票在信号日缺少历史区间即阻断，static_latest_experiment 则是拥有独立策略身份的非PIT实验。"
                        "update_flag=1 的修订版本因缺少修订可用时间而全部排除；复权连续价格执行只是研究近似，Rust尚未逐事件处理现金分红和拆并股，因此100股整数手、最低佣金和持仓股数不是精确公司行动会计。"
                        "市值桶在每次调仓时按当期已知估值更新，并在下一次更新前沿用。若股票连续停牌超过重试窗口，报告会保留并列出无法卖出的真实残留。完整收益、回撤、rolling risk、费用、行业与市值暴露见 report.html。"
                    ),
                    "",
                )
            ),
            encoding="utf-8",
        )
        attach_e2e_bundle_artifacts(
            bundle.manifest_path,
            artifacts={
                "strategy_report": markdown_report_path,
                "strategy_summary": summary_path,
            },
        )
        verify_e2e_bundle(bundle.manifest_path, runner_command=runner_command)
        return RealStrategyOutcome(
            bundle=bundle,
            summary_path=summary_path,
            markdown_report_path=markdown_report_path,
            selection_paths=tuple(selection_paths),
            provider_calls_before_cache_probe=before_probe,
            provider_calls_after_cache_probe=after_probe,
        )


__all__ = [
    "FundamentalStrategyRunError",
    "RealStrategyConfig",
    "RealStrategyOutcome",
    "run_real_strategy",
]
