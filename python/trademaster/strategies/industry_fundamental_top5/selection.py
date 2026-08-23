"""Factor-driven industry selection and exact target-weight allocation."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

import pyarrow as pa

from trademaster.contracts import (
    AvailabilityPolicy,
    FactorContext,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.factors import FactorExecutor, FactorRegistry, factor_output_schema
from trademaster.factors.cross_section import (
    WeightedFactorComponent,
    assemble_grouped_factor_inputs,
    composite_valid_counts,
)
from trademaster.factors.fundamental import fundamental_factor_suite
from trademaster.factors.management import FactorRegistration

_WEIGHT_UNITS = 100_000_000


@dataclass(frozen=True, slots=True)
class FundamentalRecord:
    """One PIT-eligible wide input row used by managed fundamental factors."""

    instrument_id: str
    industry_id: str
    industry_name: str
    signal_time: datetime
    pe_ttm: float | None
    pb: float | None
    dv_ttm: float | None
    roe: float | None
    grossprofit_margin: float | None
    ocf_to_or: float | None
    q_sales_yoy: float | None
    q_profit_yoy: float | None
    debt_to_assets: float | None
    total_market_value: float

    def __post_init__(self) -> None:
        offset = self.signal_time.utcoffset()
        if (
            not self.instrument_id
            or not self.industry_id
            or not self.industry_name
            or self.signal_time.tzinfo is None
            or offset is None
            or offset.total_seconds() != 0
        ):
            raise ValueError("fundamental record identity and UTC signal time are required")


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    instrument_id: str
    industry_id: str
    industry_name: str
    signal_time: datetime
    score: float
    global_score: float
    valid_metric_count: int
    total_market_value: float


@dataclass(frozen=True, slots=True)
class IndustryMeanScore:
    industry_id: str
    industry_name: str
    signal_time: datetime
    score: float
    eligible_member_count: int


@dataclass(frozen=True, slots=True)
class SelectedTarget:
    instrument_id: str
    industry_id: str
    industry_name: str
    signal_time: datetime
    score: float
    valid_metric_count: int
    total_market_value: float
    industry_rank: int
    industry_mean_score: float
    industry_selection_rank: int
    target_weight: Decimal


@dataclass(frozen=True, slots=True)
class IndustryFundamentalConfig:
    top_per_industry: int = 5
    top_industries_by_mean_score: int | None = None

    def __post_init__(self) -> None:
        if self.top_per_industry < 1 or (
            self.top_industries_by_mean_score is not None and self.top_industries_by_mean_score < 1
        ):
            raise ValueError("fundamental selection config is invalid")


def _factor_map(
    table: pa.Table,
    *,
    factor_id: str,
    factor_version: str,
) -> dict[tuple[datetime, str], tuple[float, bool]]:
    if table.schema.remove_metadata() != factor_output_schema():
        raise ValueError("fundamental composite factor schema drift")
    result: dict[tuple[datetime, str], tuple[float, bool]] = {}
    for row in table.to_pylist():
        if row["factor_id"] != factor_id or row["factor_version"] != factor_version:
            raise ValueError("fundamental composite factor identity drift")
        key = (row["event_time"], str(row["instrument_id"]))
        if key in result:
            raise ValueError("fundamental composite contains duplicate observations")
        value = float(row["value"])
        valid = bool(row["is_valid"])
        if valid and not math.isfinite(value):
            raise ValueError("valid fundamental score must be finite")
        result[key] = (value, valid)
    return result


def scored_candidates_from_factors(
    records: tuple[FundamentalRecord, ...],
    *,
    grouped_atomic_inputs: pa.Table,
    components: tuple[WeightedFactorComponent, ...],
    industry_composite: pa.Table,
    global_composite: pa.Table,
    industry_factor_id: str = "fundamental.composite.industry_relative",
    global_factor_id: str = "fundamental.composite.global",
    factor_version: str = "1",
) -> tuple[ScoredCandidate, ...]:
    """Join managed composite artifacts to immutable strategy dimensions."""

    keys = [(record.signal_time, record.instrument_id) for record in records]
    if len(keys) != len(set(keys)):
        raise ValueError("fundamental records must be unique")
    signal_times = {record.signal_time for record in records}
    if len(signal_times) > 1:
        raise ValueError("fundamental selection requires a single signal_time")
    industry = _factor_map(
        industry_composite,
        factor_id=industry_factor_id,
        factor_version=factor_version,
    )
    global_values = _factor_map(
        global_composite,
        factor_id=global_factor_id,
        factor_version=factor_version,
    )
    valid_counts = composite_valid_counts(grouped_atomic_inputs, components)
    expected = set(keys)
    if set(industry) != expected or set(global_values) != expected:
        raise ValueError("fundamental factor coverage differs from strategy records")
    result: list[ScoredCandidate] = []
    for record in records:
        key = (record.signal_time, record.instrument_id)
        industry_score, industry_valid = industry[key]
        global_score, global_valid = global_values[key]
        if not industry_valid or not global_valid or record.total_market_value <= 0:
            continue
        result.append(
            ScoredCandidate(
                instrument_id=record.instrument_id,
                industry_id=record.industry_id,
                industry_name=record.industry_name,
                signal_time=record.signal_time,
                score=industry_score,
                global_score=global_score,
                valid_metric_count=valid_counts.get(key, 0),
                total_market_value=record.total_market_value,
            )
        )
    return tuple(
        sorted(result, key=lambda item: (item.industry_id, -item.score, item.instrument_id))
    )


class IndustryFundamentalStrategy:
    """Select from managed factor scores; this class never computes factors."""

    def __init__(self, config: IndustryFundamentalConfig | None = None) -> None:
        self.config = config or IndustryFundamentalConfig()

    @staticmethod
    def score(records: tuple[FundamentalRecord, ...]) -> tuple[ScoredCandidate, ...]:
        """Compatibility computation routed through the managed factor definitions."""

        records = tuple(
            item
            for item in records
            if math.isfinite(item.total_market_value) and item.total_market_value > 0
        )
        if not records:
            return ()
        signal_times = {item.signal_time for item in records}
        if len(signal_times) != 1:
            raise ValueError("fundamental selection requires a single signal_time")
        signal_time = next(iter(signal_times))
        snapshot = SnapshotManifest.build(
            request=SnapshotRequest(datasets=(), as_of=signal_time),
            availability_policy=AvailabilityPolicy(
                policy_id="strategy-factor-compat/v1",
                known_at_field="known_at",
                publication_lag_policy_id="explicit-business-time/v1",
            ),
            objects=(),
            coverages=(),
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
        schema_fields: list[Any] = [
            pa.field("instrument_id", pa.string(), nullable=False),
            pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("industry_id", pa.string(), nullable=False),
            *(pa.field(name, pa.float64(), nullable=True) for name in metric_fields),
        ]
        wide = pa.Table.from_pylist(
            [
                {
                    "instrument_id": item.instrument_id,
                    "event_time": item.signal_time,
                    "industry_id": item.industry_id,
                    **{name: getattr(item, name) for name in metric_fields},
                }
                for item in records
            ],
            schema=pa.schema(schema_fields),
        )
        suite = fundamental_factor_suite()
        source_context = FactorContext(
            as_of=signal_time,
            snapshot=snapshot,
            inputs=bind_snapshot_provenance(wide, snapshot),
        )
        atomic = tuple(
            FactorExecutor(
                FactorRegistry(
                    (registration.factor,),
                    external_dataset_fields=suite.external_dataset_fields,
                )
            ).compute(
                registration.definition.factor_id,
                registration.definition.version,
                source_context,
            )
            for registration in suite.atomic
        )
        grouped = assemble_grouped_factor_inputs(
            atomic,
            dimensions=wide.select(("instrument_id", "event_time", "industry_id")),
            group_field="industry_id",
        )
        external_identities = tuple(sorted(item.definition.identity for item in suite.atomic))

        def composite(registration: FactorRegistration) -> pa.Table:
            return FactorExecutor(
                FactorRegistry(
                    (registration.factor,),
                    external_factor_identities=external_identities,
                )
            ).compute(
                registration.definition.factor_id,
                registration.definition.version,
                FactorContext(as_of=signal_time, snapshot=snapshot, inputs=grouped),
            )

        return scored_candidates_from_factors(
            records,
            grouped_atomic_inputs=grouped,
            components=suite.industry_components,
            industry_composite=composite(suite.industry_composite),
            global_composite=composite(suite.global_composite),
        )

    @staticmethod
    def industry_scores(
        candidates: tuple[ScoredCandidate, ...],
    ) -> tuple[IndustryMeanScore, ...]:
        signal_times = {item.signal_time for item in candidates}
        if len(signal_times) > 1:
            raise ValueError("fundamental selection requires a single signal_time")
        result: list[IndustryMeanScore] = []
        for industry_id in sorted({item.industry_id for item in candidates}):
            members = [item for item in candidates if item.industry_id == industry_id]
            first = members[0]
            result.append(
                IndustryMeanScore(
                    industry_id=industry_id,
                    industry_name=first.industry_name,
                    signal_time=first.signal_time,
                    score=statistics.fmean(item.global_score for item in members),
                    eligible_member_count=len(members),
                )
            )
        return tuple(sorted(result, key=lambda item: (-item.score, item.industry_id)))

    def select(
        self,
        candidates: tuple[ScoredCandidate, ...] | tuple[FundamentalRecord, ...],
    ) -> tuple[SelectedTarget, ...]:
        if candidates and isinstance(candidates[0], FundamentalRecord):
            candidates = self.score(candidates)
        candidates = cast(tuple[ScoredCandidate, ...], candidates)
        industry_scores = self.industry_scores(candidates)
        industry_score_by_id = {item.industry_id: item for item in industry_scores}
        industry_rank_by_id = {
            item.industry_id: rank for rank, item in enumerate(industry_scores, start=1)
        }
        selected_industries = {
            item.industry_id
            for item in (
                industry_scores
                if self.config.top_industries_by_mean_score is None
                else industry_scores[: self.config.top_industries_by_mean_score]
            )
        }
        if (
            self.config.top_industries_by_mean_score is not None
            and len(selected_industries) < self.config.top_industries_by_mean_score
        ):
            raise ValueError("insufficient eligible industries for exact Top-N selection")
        ranked: list[tuple[ScoredCandidate, int]] = []
        for industry_id in sorted(selected_industries):
            members = [item for item in candidates if item.industry_id == industry_id]
            ranked.extend(
                (item, rank)
                for rank, item in enumerate(members[: self.config.top_per_industry], start=1)
            )
        if not ranked:
            raise ValueError("fundamental selection is empty")
        base, remainder = divmod(_WEIGHT_UNITS, len(ranked))
        return tuple(
            SelectedTarget(
                instrument_id=item.instrument_id,
                industry_id=item.industry_id,
                industry_name=item.industry_name,
                signal_time=item.signal_time,
                score=item.score,
                valid_metric_count=item.valid_metric_count,
                total_market_value=item.total_market_value,
                industry_rank=rank,
                industry_mean_score=industry_score_by_id[item.industry_id].score,
                industry_selection_rank=industry_rank_by_id[item.industry_id],
                target_weight=Decimal(base + (index < remainder)) / Decimal(_WEIGHT_UNITS),
            )
            for index, (item, rank) in enumerate(ranked)
        )


IndustryFundamentalTop5Config = IndustryFundamentalConfig
IndustryFundamentalTop5Strategy = IndustryFundamentalStrategy


__all__ = [
    "FundamentalRecord",
    "IndustryFundamentalConfig",
    "IndustryFundamentalStrategy",
    "IndustryFundamentalTop5Config",
    "IndustryFundamentalTop5Strategy",
    "IndustryMeanScore",
    "ScoredCandidate",
    "SelectedTarget",
    "scored_candidates_from_factors",
]
