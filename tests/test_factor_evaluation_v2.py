from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest
from trademaster.factors import factor_output_schema
from trademaster.research.factor_evaluation import (
    ForwardReturnPolicy,
    FullAFactorEvaluationConfig,
    FullAFactorEvaluator,
    FullAForwardReturnBuilder,
    full_a_market_schema,
    full_a_universe_schema,
)

START = datetime(2025, 1, 2, 7, tzinfo=UTC)


def _time(index: int) -> datetime:
    return START + timedelta(days=index)


def test_forward_return_builder_uses_next_session_close_and_exact_horizon() -> None:
    market = pa.Table.from_pylist(
        [
            {
                "instrument_id": "000001.SZ",
                "event_time": _time(index),
                "close": close,
                "adj_factor": 1.0,
                "amount": 1000.0,
                "bar_available": True,
                "tradable": tradable,
            }
            for index, (close, tradable) in enumerate(
                ((10.0, True), (11.0, True), (12.0, True), (13.0, True))
            )
        ],
        schema=full_a_market_schema(),
    )
    universe = pa.Table.from_pylist(
        [
            {
                "instrument_id": "000001.SZ",
                "event_time": _time(index),
                "eligible": True,
                "exclusion_reason": None,
                "industry_id": "bank",
                "total_market_value": 100.0,
            }
            for index in range(4)
        ],
        schema=full_a_universe_schema(),
    )
    policy = ForwardReturnPolicy(
        horizons=(1, 2),
        alignment="next_session_close",
        price_adjustment="hfq_raw_times_adj_factor",
        require_tradable_entry=True,
    )

    labels = FullAForwardReturnBuilder().build(market, universe, policy=policy)

    rows = {(row["event_time"], row["horizon_sessions"]): row for row in labels.to_pylist()}
    assert rows[(_time(0), 1)]["entry_time"] == _time(1)
    assert rows[(_time(0), 1)]["exit_time"] == _time(2)
    assert rows[(_time(0), 1)]["forward_return"] == pytest.approx(12.0 / 11.0 - 1.0)
    assert rows[(_time(0), 2)]["exit_time"] == _time(3)
    assert rows[(_time(2), 1)]["is_valid"] is False
    assert rows[(_time(2), 1)]["invalid_reason"] == "insufficient_future_sessions"
    assert len(policy.policy_sha256) == 64


def test_forward_return_builder_keeps_untradable_entry_as_explicit_invalid() -> None:
    market = pa.Table.from_pylist(
        [
            {
                "instrument_id": "000001.SZ",
                "event_time": _time(index),
                "close": 10.0 + index,
                "adj_factor": 1.0,
                "amount": 1000.0,
                "bar_available": True,
                "tradable": index != 1,
            }
            for index in range(3)
        ],
        schema=full_a_market_schema(),
    )
    universe = pa.Table.from_pylist(
        [
            {
                "instrument_id": "000001.SZ",
                "event_time": _time(index),
                "eligible": True,
                "exclusion_reason": None,
                "industry_id": None,
                "total_market_value": None,
            }
            for index in range(3)
        ],
        schema=full_a_universe_schema(),
    )

    labels = FullAForwardReturnBuilder().build(
        market,
        universe,
        policy=ForwardReturnPolicy(
            horizons=(1,),
            alignment="next_session_close",
            price_adjustment="hfq_raw_times_adj_factor",
            require_tradable_entry=True,
        ),
    )

    assert labels.to_pylist()[0]["invalid_reason"] == "entry_not_tradable"


def _evaluation_inputs() -> tuple[pa.Table, pa.Table, pa.Table]:
    instruments = ("000001.SZ", "000002.SZ", "600001.SH", "600002.SH")
    universe_rows: list[dict[str, object]] = []
    factor_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for day in range(3):
        for rank, instrument in enumerate(instruments, start=1):
            universe_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "eligible": True,
                    "exclusion_reason": None,
                    "industry_id": "A" if rank <= 2 else "B",
                    "total_market_value": float(rank * 100),
                }
            )
            if not (day == 0 and instrument == "600002.SH"):
                factor_rows.append(
                    {
                        "instrument_id": instrument,
                        "event_time": _time(day),
                        "factor_id": "test.factor",
                        "factor_version": "1",
                        "value": float(rank),
                        "is_valid": True,
                    }
                )
            label_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "entry_time": _time(day + 1),
                    "exit_time": _time(day + 2),
                    "horizon_sessions": 1,
                    "forward_return": 0.01 * rank + day * 0.001,
                    "is_valid": True,
                    "invalid_reason": None,
                    "entry_amount": 1000.0 * rank,
                    "entry_tradable": True,
                }
            )
    from trademaster.research.factor_evaluation import forward_return_v2_schema

    return (
        pa.Table.from_pylist(factor_rows, schema=factor_output_schema()),
        pa.Table.from_pylist(label_rows, schema=forward_return_v2_schema()),
        pa.Table.from_pylist(universe_rows, schema=full_a_universe_schema()),
    )


def test_full_a_evaluator_uses_universe_denominator_and_extended_diagnostics() -> None:
    factors, labels, universe = _evaluation_inputs()

    result = FullAFactorEvaluator().evaluate(
        factors,
        labels,
        universe,
        config=FullAFactorEvaluationConfig(
            horizons=(1,),
            quantiles=2,
            minimum_observations=3,
            newey_west_lag=1,
            annual_observations=12,
            capacity_participation_rate=0.01,
        ),
    )

    summary = result.summaries[0]
    assert summary.factor_id == "test.factor"
    assert summary.universe_observation_count == 12
    assert summary.factor_observation_coverage == pytest.approx(11 / 12)
    assert summary.label_observation_coverage == 1.0
    assert summary.joint_observation_coverage == pytest.approx(11 / 12)
    horizon = summary.horizons[0]
    assert horizon.event_count == 3
    assert horizon.mean_ic == pytest.approx(1.0)
    assert horizon.mean_rank_ic == pytest.approx(1.0)
    assert horizon.positive_ic_ratio == 1.0
    assert horizon.mean_long_short_spread is not None
    assert horizon.mean_long_short_spread > 0
    assert horizon.spread_hit_ratio == 1.0
    assert horizon.quantile_monotonicity == pytest.approx(1.0)
    assert horizon.mean_factor_size_correlation is not None
    assert horizon.mean_factor_size_correlation > 0.98
    assert horizon.capacity_proxy_cny is not None
    assert horizon.capacity_proxy_cny > 0
    assert result.event_metrics.num_rows == 3
    assert result.quantile_metrics.num_rows == 6


def test_full_a_evaluator_rejects_missing_label_key() -> None:
    factors, labels, universe = _evaluation_inputs()
    labels = labels.slice(0, labels.num_rows - 1)

    with pytest.raises(ValueError, match="label key coverage"):
        FullAFactorEvaluator().evaluate(
            factors,
            labels,
            universe,
            config=FullAFactorEvaluationConfig(
                horizons=(1,),
                quantiles=2,
                minimum_observations=3,
                newey_west_lag=1,
                annual_observations=12,
                capacity_participation_rate=0.01,
            ),
        )


def test_full_a_evaluator_reports_pairwise_factor_redundancy() -> None:
    factors, labels, universe = _evaluation_inputs()
    inverse_rows = [
        {
            **row,
            "factor_id": "test.inverse",
            "value": -float(row["value"]),
        }
        for row in factors.to_pylist()
    ]
    factors = pa.concat_tables(
        (
            factors,
            pa.Table.from_pylist(inverse_rows, schema=factor_output_schema()),
        )
    )

    result = FullAFactorEvaluator().evaluate(
        factors,
        labels,
        universe,
        config=FullAFactorEvaluationConfig(
            horizons=(1,),
            quantiles=2,
            minimum_observations=3,
            newey_west_lag=1,
            annual_observations=12,
            capacity_participation_rate=0.01,
        ),
    )

    assert result.factor_correlations.num_rows == 1
    row = result.factor_correlations.to_pylist()[0]
    assert row["pearson_correlation"] == pytest.approx(-1.0)
    assert row["rank_correlation"] == pytest.approx(-1.0)
    assert row["observation_count"] == 3  # event-level correlations, not pooled rows


def test_full_a_evaluator_fixes_ex_ante_quantiles_and_reports_horizon_coverage() -> None:
    instruments = ("000001.SZ", "000002.SZ", "600001.SH", "600002.SH")
    factor_rows: list[dict[str, object]] = []
    universe_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for day in range(2):
        for rank, instrument in enumerate(instruments, start=1):
            factor_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "factor_id": "test.factor",
                    "factor_version": "1",
                    "value": float(rank),
                    "is_valid": True,
                }
            )
            universe_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "eligible": True,
                    "exclusion_reason": None,
                    "industry_id": "A",
                    "total_market_value": float(rank * 100),
                }
            )
            for horizon in (1, 5):
                invalid = day == 0 and horizon == 5 and instrument == "600002.SH"
                label_rows.append(
                    {
                        "instrument_id": instrument,
                        "event_time": _time(day),
                        "entry_time": _time(day + 1),
                        "exit_time": _time(day + 1 + horizon),
                        "horizon_sessions": horizon,
                        "forward_return": float(10 - rank) / 100.0,
                        "is_valid": not invalid,
                        "invalid_reason": "exit_not_active" if invalid else None,
                        "entry_amount": float(rank * 100),
                        "entry_tradable": True,
                    }
                )
    from trademaster.research.factor_evaluation import forward_return_v2_schema

    result = FullAFactorEvaluator().evaluate(
        pa.Table.from_pylist(factor_rows, schema=factor_output_schema()),
        pa.Table.from_pylist(label_rows, schema=forward_return_v2_schema()),
        pa.Table.from_pylist(universe_rows, schema=full_a_universe_schema()),
        config=FullAFactorEvaluationConfig(
            horizons=(1, 5),
            quantiles=2,
            minimum_observations=3,
            newey_west_lag=1,
            annual_observations=12,
            annual_sessions=252,
            capacity_participation_rate=0.01,
            factor_directions=(("test.factor", "1", -1),),
        ),
    )

    summary = result.summaries[0]
    assert summary.factor_direction == -1
    by_horizon = {item.horizon_sessions: item for item in summary.horizons}
    assert by_horizon[1].label_observation_coverage == 1.0
    assert by_horizon[1].joint_observation_coverage == 1.0
    assert by_horizon[1].label_invalid_reason_counts == ()
    assert by_horizon[5].label_observation_coverage == pytest.approx(7 / 8)
    assert by_horizon[5].joint_observation_coverage == pytest.approx(7 / 8)
    assert by_horizon[5].label_invalid_reason_counts == (("exit_not_active", 1),)

    quantiles = {
        (row["event_time"], row["horizon_sessions"], row["quantile"]): row
        for row in result.quantile_metrics.to_pylist()
    }
    assert quantiles[(_time(0), 5, 1)]["observation_count"] == 1
    assert quantiles[(_time(0), 5, 2)]["observation_count"] == 2
    events = {
        (row["event_time"], row["horizon_sessions"]): row
        for row in result.event_metrics.to_pylist()
    }
    assert events[(_time(0), 1)]["capacity_proxy_cny"] == pytest.approx(2.0)
    assert events[(_time(0), 5)]["capacity_proxy_cny"] == pytest.approx(2.0)
    assert events[(_time(1), 1)]["top_quantile_turnover"] == pytest.approx(0.0)
    assert events[(_time(1), 5)]["top_quantile_turnover"] == pytest.approx(0.0)


def test_full_a_evaluator_suppresses_compounded_spread_metrics_for_overlapping_labels() -> None:
    factors, labels, universe = _evaluation_inputs()
    overlapping_labels = pa.Table.from_pylist(
        [
            {
                **row,
                "exit_time": row["entry_time"] + timedelta(days=60),
                "horizon_sessions": 60,
            }
            for row in labels.to_pylist()
        ],
        schema=labels.schema,
    )

    result = FullAFactorEvaluator().evaluate(
        factors,
        overlapping_labels,
        universe,
        config=FullAFactorEvaluationConfig(
            horizons=(60,),
            quantiles=2,
            minimum_observations=3,
            newey_west_lag=1,
            annual_observations=12,
            annual_sessions=252,
            capacity_participation_rate=0.01,
        ),
    )

    horizon = result.summaries[0].horizons[0]
    assert horizon.overlapping_forward_returns is True
    assert horizon.effective_newey_west_lag == 2
    assert horizon.mean_long_short_spread is None
    assert horizon.spread_standard_deviation is None
    assert horizon.spread_sharpe is None
    assert horizon.spread_hit_ratio is None
    assert horizon.spread_max_drawdown is None
    assert horizon.spread_max_recovery_events is None
    assert all(row["long_short_spread"] is None for row in result.event_metrics.to_pylist())
    assert result.quantile_metrics.num_rows > 0


def test_full_a_evaluator_averages_pair_correlations_per_event() -> None:
    instruments = ("000001.SZ", "000002.SZ", "600001.SH")
    factor_rows: list[dict[str, object]] = []
    universe_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for day in range(2):
        for rank, instrument in enumerate(instruments, start=1):
            universe_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "eligible": True,
                    "exclusion_reason": None,
                    "industry_id": "A",
                    "total_market_value": float(rank * 100),
                }
            )
            left = float(rank if day == 0 else rank * 10)
            right = float(rank if day == 0 else (4 - rank) * 10)
            for factor_id, value in (("test.left", left), ("test.right", right)):
                factor_rows.append(
                    {
                        "instrument_id": instrument,
                        "event_time": _time(day),
                        "factor_id": factor_id,
                        "factor_version": "1",
                        "value": value,
                        "is_valid": True,
                    }
                )
            label_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "entry_time": _time(day + 1),
                    "exit_time": _time(day + 2),
                    "horizon_sessions": 1,
                    "forward_return": float(rank) / 100.0,
                    "is_valid": True,
                    "invalid_reason": None,
                    "entry_amount": float(rank * 100),
                    "entry_tradable": True,
                }
            )
    from trademaster.research.factor_evaluation import (
        forward_return_v2_schema,
    )

    result = FullAFactorEvaluator().evaluate(
        pa.Table.from_pylist(factor_rows, schema=factor_output_schema()),
        pa.Table.from_pylist(label_rows, schema=forward_return_v2_schema()),
        pa.Table.from_pylist(universe_rows, schema=full_a_universe_schema()),
        config=FullAFactorEvaluationConfig(
            horizons=(1,),
            quantiles=2,
            minimum_observations=3,
            newey_west_lag=1,
            annual_observations=12,
            capacity_participation_rate=0.01,
        ),
    )

    row = result.factor_correlations.to_pylist()[0]
    assert row["pearson_correlation"] == pytest.approx(0.0)
    assert row["rank_correlation"] == pytest.approx(0.0)
    assert row["observation_count"] == 2


def test_full_a_evaluator_reports_size_neutral_rank_ic_and_ex_ante_cap_exposure() -> None:
    instruments = tuple(f"00000{index}.SZ" for index in range(1, 7))
    residuals = (-5.0, 1.0, 4.0, 5.0, -1.0, -4.0)
    factor_rows: list[dict[str, object]] = []
    universe_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for day in range(2):
        for index, (instrument, residual) in enumerate(zip(instruments, residuals, strict=True)):
            factor_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "factor_id": "test.size.loaded",
                    "factor_version": "1",
                    "value": 10.0 * index + residual,
                    "is_valid": True,
                }
            )
            universe_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "eligible": True,
                    "exclusion_reason": None,
                    "industry_id": "A",
                    "total_market_value": math.exp(index) if day == 0 else 100.0,
                }
            )
            for horizon in (1, 5):
                invalid = day == 0 and horizon == 5 and index == 0
                label_rows.append(
                    {
                        "instrument_id": instrument,
                        "event_time": _time(day),
                        "entry_time": _time(day + 1),
                        "exit_time": _time(day + 1 + horizon),
                        "horizon_sessions": horizon,
                        "forward_return": residual / 100.0,
                        "is_valid": not invalid,
                        "invalid_reason": "exit_not_active" if invalid else None,
                        "entry_amount": 1000.0,
                        "entry_tradable": True,
                    }
                )
    from trademaster.research.factor_evaluation import forward_return_v2_schema

    result = FullAFactorEvaluator().evaluate(
        pa.Table.from_pylist(factor_rows, schema=factor_output_schema()),
        pa.Table.from_pylist(label_rows, schema=forward_return_v2_schema()),
        pa.Table.from_pylist(universe_rows, schema=full_a_universe_schema()),
        config=FullAFactorEvaluationConfig(
            horizons=(1, 5),
            quantiles=3,
            minimum_observations=4,
            newey_west_lag=1,
            annual_observations=12,
            capacity_participation_rate=0.01,
            factor_directions=(("test.size.loaded", "1", -1),),
        ),
    )

    events = {
        (row["event_time"], row["horizon_sessions"]): row
        for row in result.event_metrics.to_pylist()
    }
    assert events[(_time(0), 1)]["size_neutral_rank_ic"] == pytest.approx(1.0)
    assert events[(_time(0), 5)]["size_neutral_rank_ic"] == pytest.approx(1.0)
    assert events[(_time(1), 1)]["size_neutral_rank_ic"] is None
    for horizon in (1, 5):
        row = events[(_time(0), horizon)]
        assert row["top_small_market_cap_share"] == 1.0
        assert row["top_mid_market_cap_share"] == 0.0
        assert row["top_large_market_cap_share"] == 0.0

    by_horizon = {item.horizon_sessions: item for item in result.summaries[0].horizons}
    assert by_horizon[1].mean_size_neutral_rank_ic == pytest.approx(1.0)
    assert by_horizon[5].mean_size_neutral_rank_ic == pytest.approx(1.0)
    assert by_horizon[1].mean_top_small_market_cap_share == 1.0
    assert by_horizon[5].mean_top_small_market_cap_share == 1.0


def test_full_a_evaluator_uses_minimum_observed_spacing_for_overlap() -> None:
    factors, labels, universe = _evaluation_inputs()
    spaced_labels = pa.Table.from_pylist(
        [
            {
                **row,
                "exit_time": row["entry_time"] + timedelta(days=horizon),
                "horizon_sessions": horizon,
            }
            for row in labels.to_pylist()
            for horizon in (5, 20)
        ],
        schema=labels.schema,
    )
    config = FullAFactorEvaluationConfig(
        horizons=(5, 20),
        quantiles=2,
        minimum_observations=3,
        newey_west_lag=0,
        annual_observations=12,
        annual_sessions=252,
        minimum_observation_spacing_sessions=18,
        capacity_participation_rate=0.01,
    )

    result = FullAFactorEvaluator().evaluate(
        factors,
        spaced_labels,
        universe,
        config=config,
    )

    by_horizon = {item.horizon_sessions: item for item in result.summaries[0].horizons}
    assert config.model_dump(mode="json")["minimum_observation_spacing_sessions"] == 18
    assert by_horizon[5].overlapping_forward_returns is False
    assert by_horizon[5].effective_newey_west_lag == 0
    assert by_horizon[5].mean_long_short_spread is not None
    assert by_horizon[20].overlapping_forward_returns is True
    assert by_horizon[20].effective_newey_west_lag == 1
    assert by_horizon[20].mean_long_short_spread is None
    assert all(
        row["long_short_spread"] is None
        for row in result.event_metrics.to_pylist()
        if row["horizon_sessions"] == 20
    )
