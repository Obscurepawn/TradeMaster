"""Full-universe forward labels and extended factor diagnostics (v2 oracle)."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.factors import factor_output_schema


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def full_a_market_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("adj_factor", pa.float64(), nullable=True),
        pa.field("amount", pa.float64(), nullable=True),
        pa.field("bar_available", pa.bool_(), nullable=False),
        pa.field("tradable", pa.bool_(), nullable=False),
    ]
    return pa.schema(fields)


def full_a_universe_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("eligible", pa.bool_(), nullable=False),
        pa.field("exclusion_reason", pa.string(), nullable=True),
        pa.field("industry_id", pa.string(), nullable=True),
        pa.field("total_market_value", pa.float64(), nullable=True),
    ]
    return pa.schema(fields)


def forward_return_v2_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("entry_time", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("exit_time", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("horizon_sessions", pa.int32(), nullable=False),
        pa.field("forward_return", pa.float64(), nullable=False),
        pa.field("is_valid", pa.bool_(), nullable=False),
        pa.field("invalid_reason", pa.string(), nullable=True),
        pa.field("entry_amount", pa.float64(), nullable=True),
        pa.field("entry_tradable", pa.bool_(), nullable=False),
    ]
    return pa.schema(fields)


def full_a_event_metric_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("factor_id", pa.string(), nullable=False),
        pa.field("factor_version", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("horizon_sessions", pa.int32(), nullable=False),
        pa.field("observation_count", pa.int32(), nullable=False),
        pa.field("pearson_ic", pa.float64(), nullable=True),
        pa.field("rank_ic", pa.float64(), nullable=True),
        pa.field("factor_size_correlation", pa.float64(), nullable=True),
        pa.field("size_neutral_rank_ic", pa.float64(), nullable=True),
        pa.field("industry_rank_ic", pa.float64(), nullable=True),
        pa.field("long_short_spread", pa.float64(), nullable=True),
        pa.field("quantile_monotonicity", pa.float64(), nullable=True),
        pa.field("top_quantile_turnover", pa.float64(), nullable=True),
        pa.field("capacity_proxy_cny", pa.float64(), nullable=True),
        pa.field("top_small_market_cap_share", pa.float64(), nullable=True),
        pa.field("top_mid_market_cap_share", pa.float64(), nullable=True),
        pa.field("top_large_market_cap_share", pa.float64(), nullable=True),
    ]
    return pa.schema(fields)


def full_a_quantile_metric_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("factor_id", pa.string(), nullable=False),
        pa.field("factor_version", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("horizon_sessions", pa.int32(), nullable=False),
        pa.field("quantile", pa.int16(), nullable=False),
        pa.field("mean_forward_return", pa.float64(), nullable=False),
        pa.field("observation_count", pa.int32(), nullable=False),
    ]
    return pa.schema(fields)


def full_a_factor_correlation_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("left_factor_id", pa.string(), nullable=False),
        pa.field("left_factor_version", pa.string(), nullable=False),
        pa.field("right_factor_id", pa.string(), nullable=False),
        pa.field("right_factor_version", pa.string(), nullable=False),
        pa.field("pearson_correlation", pa.float64(), nullable=True),
        pa.field("rank_correlation", pa.float64(), nullable=True),
        pa.field("observation_count", pa.int64(), nullable=False),
    ]
    return pa.schema(fields)


class ForwardReturnPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.forward-return-policy/v2"] = (
        "trademaster.forward-return-policy/v2"
    )
    horizons: tuple[int, ...]
    alignment: Literal["next_session_close"]
    price_adjustment: Literal["hfq_raw_times_adj_factor"]
    require_tradable_entry: bool

    @model_validator(mode="after")
    def validate_policy(self) -> ForwardReturnPolicy:
        if (
            not self.horizons
            or self.horizons != tuple(sorted(set(self.horizons)))
            or any(value < 1 for value in self.horizons)
        ):
            raise ValueError("forward-return horizons must be positive, unique and sorted")
        return self

    @property
    def policy_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.model_dump(mode="json"))).hexdigest()


class FullAForwardReturnBuilder:
    """Small-table correctness oracle over a dense active-universe session panel."""

    def build(
        self,
        market: pa.Table,
        universe: pa.Table,
        *,
        policy: ForwardReturnPolicy,
    ) -> pa.Table:
        if market.schema.remove_metadata() != full_a_market_schema():
            raise ValueError("full-A market schema drift")
        if universe.schema.remove_metadata() != full_a_universe_schema():
            raise ValueError("full-A universe schema drift")
        market_rows = market.to_pylist()
        universe_rows = universe.to_pylist()
        market_by_key: dict[tuple[str, datetime], dict[str, object]] = {}
        universe_by_key: dict[tuple[str, datetime], dict[str, object]] = {}
        for row in market_rows:
            key = (str(row["instrument_id"]), cast(datetime, row["event_time"]))
            if key in market_by_key:
                raise ValueError("full-A market contains duplicate keys")
            market_by_key[key] = row
        for row in universe_rows:
            key = (str(row["instrument_id"]), cast(datetime, row["event_time"]))
            if key in universe_by_key:
                raise ValueError("full-A universe contains duplicate keys")
            universe_by_key[key] = row
        if set(market_by_key) != set(universe_by_key):
            raise ValueError("market and universe dense key coverage differ")

        keys_by_instrument: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
        for key in universe_by_key:
            keys_by_instrument[key[0]].append(key)
        output: list[dict[str, object]] = []
        for instrument_id in sorted(keys_by_instrument):
            keys = sorted(keys_by_instrument[instrument_id], key=lambda item: item[1])
            for index, key in enumerate(keys):
                if not bool(universe_by_key[key]["eligible"]):
                    continue
                for horizon in policy.horizons:
                    entry_index = index + 1
                    exit_index = entry_index + horizon
                    if exit_index >= len(keys):
                        output.append(
                            {
                                "instrument_id": instrument_id,
                                "event_time": key[1],
                                "entry_time": None,
                                "exit_time": None,
                                "horizon_sessions": horizon,
                                "forward_return": 0.0,
                                "is_valid": False,
                                "invalid_reason": "insufficient_future_sessions",
                                "entry_amount": None,
                                "entry_tradable": False,
                            }
                        )
                        continue
                    entry_key = keys[entry_index]
                    exit_key = keys[exit_index]
                    entry = market_by_key[entry_key]
                    exit_row = market_by_key[exit_key]
                    entry_tradable = bool(entry["tradable"])
                    entry_price = _adjusted_close(entry)
                    exit_price = _adjusted_close(exit_row)
                    reason: str | None = None
                    if policy.require_tradable_entry and not entry_tradable:
                        reason = "entry_not_tradable"
                    elif entry_price is None:
                        reason = "entry_price_unavailable"
                    elif exit_price is None:
                        reason = "exit_price_unavailable"
                    value = (
                        exit_price / entry_price - 1.0
                        if reason is None and entry_price is not None and exit_price is not None
                        else 0.0
                    )
                    output.append(
                        {
                            "instrument_id": instrument_id,
                            "event_time": key[1],
                            "entry_time": entry_key[1],
                            "exit_time": exit_key[1],
                            "horizon_sessions": horizon,
                            "forward_return": value,
                            "is_valid": reason is None,
                            "invalid_reason": reason,
                            "entry_amount": entry["amount"],
                            "entry_tradable": entry_tradable,
                        }
                    )
        table = pa.Table.from_pylist(
            sorted(
                output,
                key=lambda row: (
                    row["event_time"],
                    row["instrument_id"],
                    row["horizon_sessions"],
                ),
            ),
            schema=forward_return_v2_schema(),
        )
        return table.replace_schema_metadata(
            {b"trademaster.forward_return_policy_sha256": policy.policy_sha256.encode()}
        )


def _adjusted_close(row: dict[str, object]) -> float | None:
    raw_close = row["close"]
    raw_factor = row["adj_factor"]
    if raw_close is None or raw_factor is None or not bool(row["bar_available"]):
        return None
    close = float(cast(Any, raw_close))
    factor = float(cast(Any, raw_factor))
    if not math.isfinite(close) or not math.isfinite(factor) or close <= 0 or factor <= 0:
        return None
    return close * factor


class FullAFactorEvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-evaluation-config/v2"] = (
        "trademaster.factor-evaluation-config/v2"
    )
    horizons: tuple[int, ...]
    quantiles: int = Field(ge=2, le=20)
    minimum_observations: int = Field(ge=2)
    newey_west_lag: int = Field(ge=0)
    annual_observations: int = Field(gt=0)
    annual_sessions: int = Field(default=252, gt=0)
    minimum_observation_spacing_sessions: int | None = Field(default=None, gt=0)
    capacity_participation_rate: float = Field(gt=0, le=1)
    factor_directions: tuple[tuple[str, str, Literal[-1, 0, 1]], ...] = ()

    @model_validator(mode="after")
    def validate_config(self) -> FullAFactorEvaluationConfig:
        if (
            not self.horizons
            or self.horizons != tuple(sorted(set(self.horizons)))
            or any(value < 1 for value in self.horizons)
        ):
            raise ValueError("factor evaluation horizons must be canonical")
        direction_keys = tuple((item[0], item[1]) for item in self.factor_directions)
        if any(
            not factor_id or not version for factor_id, version, _ in self.factor_directions
        ) or direction_keys != tuple(sorted(set(direction_keys))):
            raise ValueError("factor evaluation directions must be canonical")
        return self

    @property
    def direction_by_identity(self) -> dict[tuple[str, str], Literal[-1, 0, 1]]:
        return {
            (factor_id, version): direction
            for factor_id, version, direction in self.factor_directions
        }

    @property
    def observation_spacing_sessions(self) -> float:
        if self.minimum_observation_spacing_sessions is not None:
            return float(self.minimum_observation_spacing_sessions)
        return self.annual_sessions / self.annual_observations

    def effective_newey_west_lag(self, horizon_sessions: int) -> int:
        sessions_per_observation = self.observation_spacing_sessions
        overlap_lag = max(0, math.ceil(horizon_sessions / sessions_per_observation) - 1)
        return max(self.newey_west_lag, overlap_lag)

    def has_overlapping_forward_returns(self, horizon_sessions: int) -> bool:
        return horizon_sessions > self.observation_spacing_sessions


class FullAHorizonDiagnostics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    horizon_sessions: int = Field(gt=0)
    event_count: int = Field(ge=0)
    label_observation_coverage: float = Field(default=0.0, ge=0, le=1)
    joint_observation_coverage: float = Field(default=0.0, ge=0, le=1)
    label_invalid_reason_counts: tuple[tuple[str, int], ...] = ()
    effective_newey_west_lag: int = Field(default=0, ge=0)
    overlapping_forward_returns: bool = False
    mean_ic: float | None
    ic_standard_deviation: float | None
    icir: float | None
    ic_t_stat: float | None
    newey_west_ic_t_stat: float | None
    mean_rank_ic: float | None
    rank_ic_standard_deviation: float | None
    rank_icir: float | None
    positive_ic_ratio: float | None
    mean_long_short_spread: float | None
    spread_standard_deviation: float | None
    spread_sharpe: float | None
    spread_hit_ratio: float | None
    spread_max_drawdown: float | None
    spread_max_recovery_events: int | None
    quantile_monotonicity: float | None
    mean_top_quantile_turnover: float | None
    mean_factor_size_correlation: float | None
    mean_industry_rank_ic: float | None
    capacity_proxy_cny: float | None
    mean_size_neutral_rank_ic: float | None = None
    mean_top_small_market_cap_share: float | None = None
    mean_top_mid_market_cap_share: float | None = None
    mean_top_large_market_cap_share: float | None = None


class FullAFactorDiagnostics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-diagnostics/v2"] = "trademaster.factor-diagnostics/v2"
    factor_id: str
    factor_version: str
    factor_direction: Literal[-1, 0, 1] = 0
    universe_observation_count: int = Field(ge=0)
    factor_observation_coverage: float = Field(ge=0, le=1)
    label_observation_coverage: float = Field(ge=0, le=1)
    joint_observation_coverage: float = Field(ge=0, le=1)
    horizons: tuple[FullAHorizonDiagnostics, ...]


@dataclass(frozen=True, slots=True)
class FullAFactorEvaluationResult:
    summaries: tuple[FullAFactorDiagnostics, ...]
    event_metrics: pa.Table
    quantile_metrics: pa.Table
    factor_correlations: pa.Table


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = math.fsum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(math.fsum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(math.fsum((value - right_mean) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for index in range(start, end):
            result[ordered[index][0]] = rank
        start = end
    return result


def _summary_stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return (None, None, None)
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else None
    if deviation is not None and deviation <= 1e-15:
        deviation = 0.0
    ratio = mean / deviation if deviation is not None and deviation > 0 else None
    return (mean, deviation, ratio)


def _t_stat(values: list[float]) -> float | None:
    mean, deviation, _ = _summary_stats(values)
    if mean is None or deviation is None or deviation <= 0:
        return None
    return mean / (deviation / math.sqrt(len(values)))


def _newey_west_t_stat(values: list[float], lag: int) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    residuals = [value - mean for value in values]
    count = len(values)
    long_run_variance = math.fsum(value * value for value in residuals) / count
    for offset in range(1, min(lag, count - 1) + 1):
        covariance = (
            math.fsum(
                residuals[index] * residuals[index - offset] for index in range(offset, count)
            )
            / count
        )
        long_run_variance += 2.0 * (1.0 - offset / (lag + 1.0)) * covariance
    variance_of_mean = long_run_variance / count
    scale = max(1.0, *(abs(value) for value in values))
    if variance_of_mean <= (1e-15 * scale) ** 2:
        return None
    return mean / math.sqrt(variance_of_mean)


def _drawdown(values: list[float]) -> tuple[float | None, int | None]:
    if not values:
        return (None, None)
    equity = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    underwater = 0
    maximum_underwater = 0
    for value in values:
        equity *= 1.0 + value
        if equity >= peak:
            peak = equity
            underwater = 0
        else:
            underwater += 1
            maximum_underwater = max(maximum_underwater, underwater)
            maximum_drawdown = min(maximum_drawdown, equity / peak - 1.0)
    return (maximum_drawdown, maximum_underwater)


class FullAFactorEvaluator:
    """Correctness oracle whose denominators come from the PIT universe, not factor rows."""

    def evaluate(
        self,
        factors: pa.Table,
        labels: pa.Table,
        universe: pa.Table,
        *,
        config: FullAFactorEvaluationConfig,
    ) -> FullAFactorEvaluationResult:
        if factors.schema.remove_metadata() != factor_output_schema():
            raise ValueError("factor evaluation v2 input schema drift")
        if labels.schema.remove_metadata() != forward_return_v2_schema():
            raise ValueError("forward return v2 schema drift")
        if universe.schema.remove_metadata() != full_a_universe_schema():
            raise ValueError("evaluation universe schema drift")
        universe_rows: dict[tuple[datetime, str], dict[str, object]] = {}
        for row in universe.to_pylist():
            key = (cast(datetime, row["event_time"]), str(row["instrument_id"]))
            if key in universe_rows:
                raise ValueError("evaluation universe contains duplicate keys")
            universe_rows[key] = row
        expected_keys = {key for key, row in universe_rows.items() if bool(row["eligible"])}
        if not expected_keys:
            raise ValueError("evaluation universe contains no eligible observations")

        factor_values: dict[tuple[str, str], dict[tuple[datetime, str], tuple[float, bool]]] = (
            defaultdict(dict)
        )
        for row in factors.to_pylist():
            identity = (str(row["factor_id"]), str(row["factor_version"]))
            factor_key = (
                cast(datetime, row["event_time"]),
                str(row["instrument_id"]),
            )
            if factor_key not in universe_rows:
                raise ValueError("factor observation is outside the universe key set")
            if factor_key in factor_values[identity]:
                raise ValueError("factor evaluation contains duplicate observations")
            factor_values[identity][factor_key] = (
                float(cast(Any, row["value"])),
                bool(row["is_valid"]),
            )
        if not factor_values:
            raise ValueError("factor evaluation contains no factor identities")

        label_values: dict[tuple[datetime, str, int], dict[str, object]] = {}
        for row in labels.to_pylist():
            label_key = (
                cast(datetime, row["event_time"]),
                str(row["instrument_id"]),
                int(row["horizon_sessions"]),
            )
            if label_key in label_values:
                raise ValueError("forward labels contain duplicate keys")
            label_values[label_key] = row
        expected_label_keys = {
            (event_time, instrument_id, horizon)
            for event_time, instrument_id in expected_keys
            for horizon in config.horizons
        }
        if set(label_values) != expected_label_keys:
            raise ValueError("forward label key coverage differs from PIT universe")

        correlation_rows: list[dict[str, object]] = []
        identities = sorted(factor_values)
        for left_index, left_identity in enumerate(identities):
            for right_identity in identities[left_index + 1 :]:
                shared_by_event: dict[datetime, list[tuple[float, float]]] = defaultdict(list)
                for key in sorted(expected_keys):
                    if (
                        key not in factor_values[left_identity]
                        or key not in factor_values[right_identity]
                        or not factor_values[left_identity][key][1]
                        or not factor_values[right_identity][key][1]
                        or not math.isfinite(factor_values[left_identity][key][0])
                        or not math.isfinite(factor_values[right_identity][key][0])
                    ):
                        continue
                    shared_by_event[key[0]].append(
                        (
                            factor_values[left_identity][key][0],
                            factor_values[right_identity][key][0],
                        )
                    )
                event_pearsons: list[float] = []
                event_rank_correlations: list[float] = []
                for event_time in sorted(shared_by_event):
                    pairs = shared_by_event[event_time]
                    left_sample = [item[0] for item in pairs]
                    right_sample = [item[1] for item in pairs]
                    pearson = _pearson(left_sample, right_sample)
                    rank_correlation = _pearson(_ranks(left_sample), _ranks(right_sample))
                    if pearson is not None:
                        event_pearsons.append(pearson)
                    if rank_correlation is not None:
                        event_rank_correlations.append(rank_correlation)
                correlation_rows.append(
                    {
                        "left_factor_id": left_identity[0],
                        "left_factor_version": left_identity[1],
                        "right_factor_id": right_identity[0],
                        "right_factor_version": right_identity[1],
                        "pearson_correlation": (
                            statistics.fmean(event_pearsons) if event_pearsons else None
                        ),
                        "rank_correlation": (
                            statistics.fmean(event_rank_correlations)
                            if event_rank_correlations
                            else None
                        ),
                        "observation_count": len(event_rank_correlations),
                    }
                )

        event_rows: list[dict[str, object]] = []
        quantile_rows: list[dict[str, object]] = []
        summaries: list[FullAFactorDiagnostics] = []
        total = len(expected_keys)
        direction_by_identity = config.direction_by_identity
        unknown_directions = set(direction_by_identity) - set(identities)
        if unknown_directions:
            raise ValueError("factor direction references an unknown factor identity")
        label_valid_by_horizon = {
            horizon: sum(
                bool(label_values[(key[0], key[1], horizon)]["is_valid"]) for key in expected_keys
            )
            for horizon in config.horizons
        }
        invalid_reasons_by_horizon: dict[int, tuple[tuple[str, int], ...]] = {}
        for horizon in config.horizons:
            reasons: dict[str, int] = defaultdict(int)
            for key in expected_keys:
                label = label_values[(key[0], key[1], horizon)]
                if bool(label["is_valid"]):
                    continue
                raw_reason = label["invalid_reason"]
                reason = (
                    str(raw_reason)
                    if isinstance(raw_reason, str) and raw_reason
                    else "unspecified_invalid"
                )
                reasons[reason] += 1
            invalid_reasons_by_horizon[horizon] = tuple(sorted(reasons.items()))
        label_valid_total = sum(label_valid_by_horizon.values())
        market_caps_by_event: dict[datetime, list[tuple[str, float]]] = defaultdict(list)
        for key in sorted(expected_keys):
            raw_market_value = universe_rows[key]["total_market_value"]
            if raw_market_value is None:
                continue
            market_value = float(cast(Any, raw_market_value))
            if math.isfinite(market_value) and market_value > 0:
                market_caps_by_event[key[0]].append((key[1], market_value))
        market_cap_bucket_by_key: dict[tuple[datetime, str], str] = {}
        for event_time, values in market_caps_by_event.items():
            if len(values) < 3:
                continue
            ordered = sorted(values, key=lambda item: (item[1], item[0]))
            for index, (instrument_id, _) in enumerate(ordered):
                bucket_index = min(3, index * 3 // len(ordered) + 1)
                market_cap_bucket_by_key[(event_time, instrument_id)] = (
                    "small" if bucket_index == 1 else "mid" if bucket_index == 2 else "large"
                )
        for identity in sorted(factor_values):
            values_by_key = factor_values[identity]
            direction = direction_by_identity.get(identity, 0)
            orientation = -1.0 if direction == -1 else 1.0
            valid_factor_keys = {
                key
                for key, (value, valid) in values_by_key.items()
                if key in expected_keys and valid and math.isfinite(value)
            }
            joint_total = sum(
                key in valid_factor_keys
                and bool(label_values[(key[0], key[1], horizon)]["is_valid"])
                for key in expected_keys
                for horizon in config.horizons
            )
            factor_pool_by_event: dict[datetime, list[tuple[str, float]]] = defaultdict(list)
            for key in sorted(valid_factor_keys):
                factor_pool_by_event[key[0]].append((key[1], values_by_key[key][0]))
            ex_ante_events: dict[
                datetime,
                tuple[
                    dict[str, int],
                    float | None,
                    float | None,
                    tuple[float | None, float | None, float | None],
                ],
            ] = {}
            size_neutral_values_by_event: dict[datetime, dict[str, float]] = {}
            previous_top: set[str] | None = None
            entry_horizon = config.horizons[0]
            for event_time in sorted(factor_pool_by_event):
                pool = factor_pool_by_event[event_time]
                if len(pool) < config.minimum_observations:
                    continue
                ranked_pool = sorted(
                    pool,
                    key=lambda item: (orientation * item[1], item[0]),
                )
                quantile_by_instrument: dict[str, int] = {}
                for index, (instrument_id, _) in enumerate(ranked_pool):
                    quantile_by_instrument[instrument_id] = min(
                        config.quantiles,
                        index * config.quantiles // len(ranked_pool) + 1,
                    )
                top_members = {
                    instrument_id
                    for instrument_id, quantile in quantile_by_instrument.items()
                    if quantile == config.quantiles
                }
                turnover = None
                if previous_top is not None and top_members:
                    turnover = 1.0 - len(previous_top & top_members) / max(
                        len(previous_top), len(top_members)
                    )
                previous_top = top_members
                top_amounts: list[float] = []
                for instrument_id in sorted(top_members):
                    label = label_values[(event_time, instrument_id, entry_horizon)]
                    raw_amount = label["entry_amount"]
                    if (
                        not bool(label["entry_tradable"])
                        or raw_amount is None
                        or not math.isfinite(float(cast(Any, raw_amount)))
                        or float(cast(Any, raw_amount)) <= 0
                    ):
                        continue
                    top_amounts.append(float(cast(Any, raw_amount)))
                capacity = (
                    min(top_amounts) * config.capacity_participation_rate * len(top_members)
                    if top_members and len(top_amounts) == len(top_members)
                    else None
                )
                size_samples: list[tuple[str, float, float]] = []
                for instrument_id, raw_factor_value in pool:
                    raw_market_value = universe_rows[(event_time, instrument_id)][
                        "total_market_value"
                    ]
                    if raw_market_value is None:
                        continue
                    market_value = float(cast(Any, raw_market_value))
                    if math.isfinite(market_value) and market_value > 0:
                        size_samples.append(
                            (instrument_id, raw_factor_value, math.log(market_value))
                        )
                if len(size_samples) >= config.minimum_observations:
                    mean_factor = statistics.fmean(item[1] for item in size_samples)
                    mean_size = statistics.fmean(item[2] for item in size_samples)
                    size_sum_squares = math.fsum(
                        (item[2] - mean_size) ** 2 for item in size_samples
                    )
                    if size_sum_squares > 0:
                        slope = (
                            math.fsum(
                                (item[2] - mean_size) * (item[1] - mean_factor)
                                for item in size_samples
                            )
                            / size_sum_squares
                        )
                        size_neutral_values_by_event[event_time] = {
                            instrument_id: factor_value
                            - (mean_factor + slope * (log_size - mean_size))
                            for instrument_id, factor_value, log_size in size_samples
                        }
                top_buckets = [
                    market_cap_bucket_by_key.get((event_time, instrument_id))
                    for instrument_id in sorted(top_members)
                ]
                cap_shares: tuple[float | None, float | None, float | None] = (
                    (
                        (top_buckets.count("small") / len(top_buckets)),
                        (top_buckets.count("mid") / len(top_buckets)),
                        (top_buckets.count("large") / len(top_buckets)),
                    )
                    if top_buckets and all(item is not None for item in top_buckets)
                    else (
                        None,
                        None,
                        None,
                    )
                )
                ex_ante_events[event_time] = (
                    quantile_by_instrument,
                    turnover,
                    capacity,
                    cap_shares,
                )
            horizon_summaries: list[FullAHorizonDiagnostics] = []
            for horizon in config.horizons:
                overlapping_forward_returns = config.has_overlapping_forward_returns(horizon)
                grouped: dict[
                    datetime,
                    list[tuple[str, float, float, float | None, str | None]],
                ] = defaultdict(list)
                for key in sorted(expected_keys):
                    factor_observation = values_by_key.get(key)
                    label = label_values[(key[0], key[1], horizon)]
                    if (
                        factor_observation is None
                        or key not in valid_factor_keys
                        or not bool(label["is_valid"])
                    ):
                        continue
                    raw_market_value = universe_rows[key]["total_market_value"]
                    industry_id = universe_rows[key]["industry_id"]
                    grouped[key[0]].append(
                        (
                            key[1],
                            factor_observation[0],
                            float(cast(Any, label["forward_return"])),
                            (
                                float(cast(Any, raw_market_value))
                                if raw_market_value is not None
                                and math.isfinite(float(cast(Any, raw_market_value)))
                                else None
                            ),
                            None if industry_id is None else str(industry_id),
                        )
                    )
                ic_values: list[float] = []
                rank_ic_values: list[float] = []
                spreads: list[float] = []
                monotonicity_values: list[float] = []
                turnover_values: list[float] = []
                size_correlations: list[float] = []
                industry_rank_ics: list[float] = []
                capacities: list[float] = []
                size_neutral_rank_ics: list[float] = []
                top_small_cap_shares: list[float] = []
                top_mid_cap_shares: list[float] = []
                top_large_cap_shares: list[float] = []
                evaluated_event_count = 0
                for event_time in sorted(grouped):
                    joined = sorted(grouped[event_time], key=lambda item: item[0])
                    ex_ante = ex_ante_events.get(event_time)
                    if len(joined) < config.minimum_observations or ex_ante is None:
                        continue
                    quantile_by_instrument, turnover, capacity, cap_shares = ex_ante
                    evaluated_event_count += 1
                    factor_sample = [item[1] for item in joined]
                    return_sample = [item[2] for item in joined]
                    pearson = _pearson(factor_sample, return_sample)
                    rank_ic = _pearson(_ranks(factor_sample), _ranks(return_sample))
                    if pearson is not None:
                        ic_values.append(pearson)
                    if rank_ic is not None:
                        rank_ic_values.append(rank_ic)
                    neutral_values = size_neutral_values_by_event.get(event_time, {})
                    neutral_pairs = [
                        (neutral_values[item[0]], item[2])
                        for item in joined
                        if item[0] in neutral_values
                    ]
                    size_neutral_rank_ic = (
                        _pearson(
                            _ranks([item[0] for item in neutral_pairs]),
                            _ranks([item[1] for item in neutral_pairs]),
                        )
                        if len(neutral_pairs) >= config.minimum_observations
                        else None
                    )
                    if size_neutral_rank_ic is not None:
                        size_neutral_rank_ics.append(size_neutral_rank_ic)

                    size_pairs = [
                        (item[1], math.log(item[3]))
                        for item in joined
                        if item[3] is not None and item[3] > 0
                    ]
                    size_correlation = (
                        _pearson(
                            [item[0] for item in size_pairs],
                            [item[1] for item in size_pairs],
                        )
                        if len(size_pairs) >= 2
                        else None
                    )
                    if size_correlation is not None:
                        size_correlations.append(size_correlation)

                    by_industry: dict[str, list[tuple[float, float]]] = defaultdict(list)
                    for item in joined:
                        if item[4] is not None:
                            by_industry[item[4]].append((item[1], item[2]))
                    event_industry_ics = [
                        value
                        for pairs in by_industry.values()
                        if len(pairs) >= 2
                        for value in (
                            _pearson(
                                _ranks([item[0] for item in pairs]),
                                _ranks([item[1] for item in pairs]),
                            ),
                        )
                        if value is not None
                    ]
                    industry_rank_ic = (
                        statistics.fmean(event_industry_ics) if event_industry_ics else None
                    )
                    if industry_rank_ic is not None:
                        industry_rank_ics.append(industry_rank_ic)

                    by_quantile: dict[int, list[tuple[str, float]]] = {
                        value: [] for value in range(1, config.quantiles + 1)
                    }
                    for item in joined:
                        by_quantile[quantile_by_instrument[item[0]]].append((item[0], item[2]))
                    quantile_means: dict[int, float] = {}
                    for quantile, members in by_quantile.items():
                        if not members:
                            continue
                        mean_return = statistics.fmean(item[1] for item in members)
                        quantile_means[quantile] = mean_return
                        quantile_rows.append(
                            {
                                "factor_id": identity[0],
                                "factor_version": identity[1],
                                "event_time": event_time,
                                "horizon_sessions": horizon,
                                "quantile": quantile,
                                "mean_forward_return": mean_return,
                                "observation_count": len(members),
                            }
                        )
                    monotonicity = (
                        _pearson(
                            [float(value) for value in sorted(quantile_means)],
                            [quantile_means[value] for value in sorted(quantile_means)],
                        )
                        if len(quantile_means) >= 2
                        else None
                    )
                    if monotonicity is not None:
                        monotonicity_values.append(monotonicity)
                    spread = None
                    if (
                        not overlapping_forward_returns
                        and 1 in quantile_means
                        and config.quantiles in quantile_means
                    ):
                        spread = quantile_means[config.quantiles] - quantile_means[1]
                        spreads.append(spread)
                    if turnover is not None:
                        turnover_values.append(turnover)
                    if capacity is not None:
                        capacities.append(capacity)
                    small_share, mid_share, large_share = cap_shares
                    if small_share is not None:
                        top_small_cap_shares.append(small_share)
                    if mid_share is not None:
                        top_mid_cap_shares.append(mid_share)
                    if large_share is not None:
                        top_large_cap_shares.append(large_share)
                    event_rows.append(
                        {
                            "factor_id": identity[0],
                            "factor_version": identity[1],
                            "event_time": event_time,
                            "horizon_sessions": horizon,
                            "observation_count": len(joined),
                            "pearson_ic": pearson,
                            "rank_ic": rank_ic,
                            "factor_size_correlation": size_correlation,
                            "size_neutral_rank_ic": size_neutral_rank_ic,
                            "industry_rank_ic": industry_rank_ic,
                            "long_short_spread": spread,
                            "quantile_monotonicity": monotonicity,
                            "top_quantile_turnover": turnover,
                            "capacity_proxy_cny": capacity,
                            "top_small_market_cap_share": small_share,
                            "top_mid_market_cap_share": mid_share,
                            "top_large_market_cap_share": large_share,
                        }
                    )
                mean_ic, ic_deviation, icir = _summary_stats(ic_values)
                mean_rank_ic, rank_deviation, rank_icir = _summary_stats(rank_ic_values)
                spread_mean, spread_deviation, _ = _summary_stats(spreads)
                drawdown, recovery = _drawdown(spreads)
                horizon_joint_total = sum(
                    key in valid_factor_keys
                    and bool(label_values[(key[0], key[1], horizon)]["is_valid"])
                    for key in expected_keys
                )
                effective_lag = config.effective_newey_west_lag(horizon)
                horizon_summaries.append(
                    FullAHorizonDiagnostics(
                        horizon_sessions=horizon,
                        event_count=evaluated_event_count,
                        label_observation_coverage=label_valid_by_horizon[horizon] / total,
                        joint_observation_coverage=horizon_joint_total / total,
                        label_invalid_reason_counts=invalid_reasons_by_horizon[horizon],
                        effective_newey_west_lag=effective_lag,
                        overlapping_forward_returns=(overlapping_forward_returns),
                        mean_ic=mean_ic,
                        ic_standard_deviation=ic_deviation,
                        icir=icir,
                        ic_t_stat=_t_stat(ic_values),
                        newey_west_ic_t_stat=_newey_west_t_stat(ic_values, effective_lag),
                        mean_rank_ic=mean_rank_ic,
                        rank_ic_standard_deviation=rank_deviation,
                        rank_icir=rank_icir,
                        positive_ic_ratio=(
                            sum(value > 0 for value in ic_values) / len(ic_values)
                            if ic_values
                            else None
                        ),
                        mean_long_short_spread=spread_mean,
                        spread_standard_deviation=spread_deviation,
                        spread_sharpe=(
                            spread_mean / spread_deviation * math.sqrt(config.annual_observations)
                            if spread_mean is not None
                            and spread_deviation is not None
                            and spread_deviation > 0
                            else None
                        ),
                        spread_hit_ratio=(
                            sum(value > 0 for value in spreads) / len(spreads) if spreads else None
                        ),
                        spread_max_drawdown=drawdown,
                        spread_max_recovery_events=recovery,
                        quantile_monotonicity=(
                            statistics.fmean(monotonicity_values) if monotonicity_values else None
                        ),
                        mean_top_quantile_turnover=(
                            statistics.fmean(turnover_values) if turnover_values else None
                        ),
                        mean_factor_size_correlation=(
                            statistics.fmean(size_correlations) if size_correlations else None
                        ),
                        mean_industry_rank_ic=(
                            statistics.fmean(industry_rank_ics) if industry_rank_ics else None
                        ),
                        capacity_proxy_cny=(statistics.median(capacities) if capacities else None),
                        mean_size_neutral_rank_ic=(
                            statistics.fmean(size_neutral_rank_ics)
                            if size_neutral_rank_ics
                            else None
                        ),
                        mean_top_small_market_cap_share=(
                            statistics.fmean(top_small_cap_shares) if top_small_cap_shares else None
                        ),
                        mean_top_mid_market_cap_share=(
                            statistics.fmean(top_mid_cap_shares) if top_mid_cap_shares else None
                        ),
                        mean_top_large_market_cap_share=(
                            statistics.fmean(top_large_cap_shares) if top_large_cap_shares else None
                        ),
                    )
                )
            summaries.append(
                FullAFactorDiagnostics(
                    factor_id=identity[0],
                    factor_version=identity[1],
                    factor_direction=direction,  # raw IC stays raw; portfolio legs are oriented.
                    universe_observation_count=total,
                    factor_observation_coverage=len(valid_factor_keys) / total,
                    label_observation_coverage=(label_valid_total / (total * len(config.horizons))),
                    joint_observation_coverage=(joint_total / (total * len(config.horizons))),
                    horizons=tuple(horizon_summaries),
                )
            )
        return FullAFactorEvaluationResult(
            summaries=tuple(summaries),
            event_metrics=pa.Table.from_pylist(event_rows, schema=full_a_event_metric_schema()),
            quantile_metrics=pa.Table.from_pylist(
                quantile_rows, schema=full_a_quantile_metric_schema()
            ),
            factor_correlations=pa.Table.from_pylist(
                correlation_rows, schema=full_a_factor_correlation_schema()
            ),
        )


__all__ = [
    "ForwardReturnPolicy",
    "FullAFactorDiagnostics",
    "FullAFactorEvaluationConfig",
    "FullAFactorEvaluationResult",
    "FullAFactorEvaluator",
    "FullAForwardReturnBuilder",
    "FullAHorizonDiagnostics",
    "forward_return_v2_schema",
    "full_a_event_metric_schema",
    "full_a_factor_correlation_schema",
    "full_a_market_schema",
    "full_a_quantile_metric_schema",
    "full_a_universe_schema",
]
