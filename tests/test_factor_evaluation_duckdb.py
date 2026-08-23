from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.factors import factor_output_schema
from trademaster.research.factor_evaluation import (
    FullAFactorEvaluationConfig,
    FullAFactorEvaluationResult,
    FullAFactorEvaluator,
    forward_return_v2_schema,
    full_a_universe_schema,
)
from trademaster.research.factor_evaluation_duckdb import DuckDBFullAFactorEvaluator


def _time(day: int) -> datetime:
    return datetime(2020, 1, 1, 7, tzinfo=UTC) + timedelta(days=day)


def _inputs() -> tuple[pa.Table, pa.Table, pa.Table]:
    instruments = ("000001.SZ", "000002.SZ", "600001.SH", "600002.SH")
    return_shapes = (
        (0.01, 0.03, 0.02, 0.04),
        (0.04, 0.01, 0.03, 0.02),
        (0.02, 0.04, 0.01, 0.03),
        (0.03, 0.02, 0.04, 0.01),
    )
    universe_rows: list[dict[str, object]] = []
    factor_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for day in range(4):
        for rank, instrument in enumerate(instruments, start=1):
            universe_rows.append(
                {
                    "instrument_id": instrument,
                    "event_time": _time(day),
                    "eligible": True,
                    "exclusion_reason": None,
                    "industry_id": "A" if rank <= 2 else "B",
                    "total_market_value": float(rank * 100 + day),
                }
            )
            if not (day == 0 and instrument == "600002.SH"):
                for factor_id, value in (
                    ("test.factor", float(rank + day)),
                    ("test.inverse", float(-(rank + day))),
                ):
                    factor_rows.append(
                        {
                            "instrument_id": instrument,
                            "event_time": _time(day),
                            "factor_id": factor_id,
                            "factor_version": "1",
                            "value": value,
                            "is_valid": not (day == 2 and instrument == "000001.SZ"),
                        }
                    )
            for horizon in (1, 5):
                label_rows.append(
                    {
                        "instrument_id": instrument,
                        "event_time": _time(day),
                        "entry_time": _time(day + 1),
                        "exit_time": _time(day + 1 + horizon),
                        "horizon_sessions": horizon,
                        "forward_return": return_shapes[day][rank - 1] * (1 + horizon / 10),
                        "is_valid": not (day == 3 and horizon == 5),
                        "invalid_reason": (
                            "insufficient_future_sessions" if day == 3 and horizon == 5 else None
                        ),
                        "entry_amount": 1000.0 * rank + day,
                        "entry_tradable": True,
                    }
                )
    return (
        pa.Table.from_pylist(factor_rows, schema=factor_output_schema()),
        pa.Table.from_pylist(label_rows, schema=forward_return_v2_schema()),
        pa.Table.from_pylist(universe_rows, schema=full_a_universe_schema()),
    )


def _config() -> FullAFactorEvaluationConfig:
    return FullAFactorEvaluationConfig(
        horizons=(1, 5),
        quantiles=2,
        minimum_observations=3,
        newey_west_lag=1,
        annual_observations=12,
        capacity_participation_rate=0.01,
    )


def _write_inputs(
    root: Path,
    inputs: tuple[pa.Table, pa.Table, pa.Table],
) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = (
        root / "factor_values.parquet",
        root / "forward_returns.parquet",
        root / "universe.parquet",
    )
    for path, table in zip(paths, inputs, strict=True):
        pq.write_table(table, path)
    return paths


def _assert_nested_approximately_equal(actual: Any, expected: Any) -> None:
    if isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-12)
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            _assert_nested_approximately_equal(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            _assert_nested_approximately_equal(actual_item, expected_item)
    else:
        assert actual == expected


def _sorted_rows(table: pa.Table, keys: tuple[str, ...]) -> list[dict[str, object]]:
    return sorted(table.to_pylist(), key=lambda row: tuple(row[key] for key in keys))


def _assert_result_parity(
    actual: FullAFactorEvaluationResult,
    expected: FullAFactorEvaluationResult,
) -> None:
    _assert_nested_approximately_equal(
        [item.model_dump(mode="python") for item in actual.summaries],
        [item.model_dump(mode="python") for item in expected.summaries],
    )
    for actual_table, expected_table, keys in (
        (
            actual.event_metrics,
            expected.event_metrics,
            ("factor_id", "factor_version", "event_time", "horizon_sessions"),
        ),
        (
            actual.quantile_metrics,
            expected.quantile_metrics,
            ("factor_id", "factor_version", "event_time", "horizon_sessions", "quantile"),
        ),
        (
            actual.factor_correlations,
            expected.factor_correlations,
            ("left_factor_id", "left_factor_version", "right_factor_id", "right_factor_version"),
        ),
    ):
        assert actual_table.schema == expected_table.schema
        _assert_nested_approximately_equal(
            _sorted_rows(actual_table, keys),
            _sorted_rows(expected_table, keys),
        )


def test_duckdb_evaluator_matches_small_table_oracle(tmp_path: Path) -> None:
    inputs = _inputs()
    factor_path, label_path, universe_path = _write_inputs(tmp_path, inputs)
    expected = FullAFactorEvaluator().evaluate(*inputs, config=_config())

    actual = DuckDBFullAFactorEvaluator().evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=_config(),
    )

    _assert_result_parity(actual, expected)


def test_duckdb_evaluator_matches_overlap_and_direction_semantics(tmp_path: Path) -> None:
    factors, labels, universe = _inputs()
    labels = pa.Table.from_pylist(
        [
            {
                **row,
                "exit_time": row["entry_time"] + timedelta(days=60),
                "horizon_sessions": 60,
            }
            for row in labels.to_pylist()
            if row["horizon_sessions"] == 1
        ],
        schema=labels.schema,
    )
    config = FullAFactorEvaluationConfig(
        horizons=(60,),
        quantiles=2,
        minimum_observations=3,
        newey_west_lag=1,
        annual_observations=12,
        annual_sessions=252,
        capacity_participation_rate=0.01,
        factor_directions=(("test.factor", "1", -1),),
    )
    factor_path, label_path, universe_path = _write_inputs(tmp_path, (factors, labels, universe))
    expected = FullAFactorEvaluator().evaluate(factors, labels, universe, config=config)

    actual = DuckDBFullAFactorEvaluator().evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=config,
    )

    _assert_result_parity(actual, expected)
    assert actual.summaries[0].horizons[0].effective_newey_west_lag == 2
    assert all(row["long_short_spread"] is None for row in actual.event_metrics.to_pylist())


def test_duckdb_evaluator_uses_only_eligible_pit_universe_denominator(tmp_path: Path) -> None:
    factors, labels, universe = _inputs()
    ineligible = pa.Table.from_pylist(
        [
            {
                "instrument_id": "900001.BJ",
                "event_time": _time(0),
                "eligible": False,
                "exclusion_reason": "not_listed",
                "industry_id": None,
                "total_market_value": None,
            }
        ],
        schema=full_a_universe_schema(),
    )
    universe = pa.concat_tables((universe, ineligible))
    factor_path, label_path, universe_path = _write_inputs(tmp_path, (factors, labels, universe))

    result = DuckDBFullAFactorEvaluator().evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=_config(),
    )

    assert all(item.universe_observation_count == 16 for item in result.summaries)
    assert all(
        item.factor_observation_coverage == pytest.approx(14 / 16) for item in result.summaries
    )


def test_duckdb_evaluator_rejects_incomplete_label_key_coverage(tmp_path: Path) -> None:
    factors, labels, universe = _inputs()
    factor_path, label_path, universe_path = _write_inputs(
        tmp_path, (factors, labels.slice(0, labels.num_rows - 1), universe)
    )

    with pytest.raises(ValueError, match="label key coverage"):
        DuckDBFullAFactorEvaluator().evaluate(
            factor_values_path=factor_path,
            forward_returns_path=label_path,
            universe_path=universe_path,
            config=_config(),
        )


def test_duckdb_evaluator_rejects_duplicate_and_outside_keys(tmp_path: Path) -> None:
    factors, labels, universe = _inputs()
    duplicate = pa.concat_tables((factors, factors.slice(0, 1)))
    factor_path, label_path, universe_path = _write_inputs(
        tmp_path / "duplicate", (duplicate, labels, universe)
    )
    with pytest.raises(ValueError, match="duplicate observations"):
        DuckDBFullAFactorEvaluator().evaluate(
            factor_values_path=factor_path,
            forward_returns_path=label_path,
            universe_path=universe_path,
            config=_config(),
        )

    outside_row = {
        **factors.slice(0, 1).to_pylist()[0],
        "instrument_id": "999999.SH",
    }
    outside = pa.concat_tables(
        (factors, pa.Table.from_pylist([outside_row], schema=factor_output_schema()))
    )
    factor_path, label_path, universe_path = _write_inputs(
        tmp_path / "outside", (outside, labels, universe)
    )
    with pytest.raises(ValueError, match="outside the universe key set"):
        DuckDBFullAFactorEvaluator().evaluate(
            factor_values_path=factor_path,
            forward_returns_path=label_path,
            universe_path=universe_path,
            config=_config(),
        )


def test_duckdb_evaluator_keeps_factor_pairs_with_no_shared_valid_rows(
    tmp_path: Path,
) -> None:
    factors, labels, universe = _inputs()
    invalid_rows = [
        {
            **row,
            "factor_id": "test.empty",
            "value": 0.0,
            "is_valid": False,
        }
        for row in factors.to_pylist()
        if row["factor_id"] == "test.factor"
    ]
    factors = pa.concat_tables(
        (factors, pa.Table.from_pylist(invalid_rows, schema=factor_output_schema()))
    )
    factor_path, label_path, universe_path = _write_inputs(tmp_path, (factors, labels, universe))
    expected = FullAFactorEvaluator().evaluate(factors, labels, universe, config=_config())

    actual = DuckDBFullAFactorEvaluator().evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=_config(),
    )

    _assert_result_parity(actual, expected)
    assert actual.factor_correlations.num_rows == 3


def test_duckdb_evaluator_averages_pair_correlations_per_event(tmp_path: Path) -> None:
    factors, labels, universe = _inputs()
    factor_rows = factors.to_pylist()
    for row in factor_rows:
        if row["factor_id"] == "test.inverse" and row["event_time"] < _time(2):
            row["value"] = -float(row["value"])
    factors = pa.Table.from_pylist(factor_rows, schema=factor_output_schema())
    factor_path, label_path, universe_path = _write_inputs(tmp_path, (factors, labels, universe))
    expected = FullAFactorEvaluator().evaluate(factors, labels, universe, config=_config())

    actual = DuckDBFullAFactorEvaluator().evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=_config(),
    )

    _assert_result_parity(actual, expected)
    row = actual.factor_correlations.to_pylist()[0]
    assert row["pearson_correlation"] == pytest.approx(0.0)
    assert row["rank_correlation"] == pytest.approx(0.0)
    assert row["observation_count"] == 4


def test_duckdb_evaluator_matches_size_neutral_and_ex_ante_bucket_oracle(
    tmp_path: Path,
) -> None:
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
    inputs = (
        pa.Table.from_pylist(factor_rows, schema=factor_output_schema()),
        pa.Table.from_pylist(label_rows, schema=forward_return_v2_schema()),
        pa.Table.from_pylist(universe_rows, schema=full_a_universe_schema()),
    )
    config = FullAFactorEvaluationConfig(
        horizons=(1, 5),
        quantiles=3,
        minimum_observations=4,
        newey_west_lag=1,
        annual_observations=12,
        capacity_participation_rate=0.01,
        factor_directions=(("test.size.loaded", "1", -1),),
    )
    factor_path, label_path, universe_path = _write_inputs(tmp_path, inputs)
    expected = FullAFactorEvaluator().evaluate(*inputs, config=config)

    actual = DuckDBFullAFactorEvaluator().evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=config,
    )

    _assert_result_parity(actual, expected)
    events = {
        (row["event_time"], row["horizon_sessions"]): row
        for row in actual.event_metrics.to_pylist()
    }
    assert events[(_time(0), 5)]["size_neutral_rank_ic"] == pytest.approx(1.0)
    assert events[(_time(1), 5)]["size_neutral_rank_ic"] is None
    assert events[(_time(0), 1)]["top_small_market_cap_share"] == 1.0
    assert events[(_time(0), 5)]["top_small_market_cap_share"] == 1.0


def test_duckdb_evaluator_matches_minimum_observed_spacing_overlap(
    tmp_path: Path,
) -> None:
    factors, labels, universe = _inputs()
    labels = pa.Table.from_pylist(
        [
            {
                **row,
                "exit_time": row["entry_time"] + timedelta(days=horizon),
                "horizon_sessions": horizon,
            }
            for row in labels.to_pylist()
            if row["horizon_sessions"] == 1
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
    inputs = (factors, labels, universe)
    factor_path, label_path, universe_path = _write_inputs(tmp_path, inputs)
    expected = FullAFactorEvaluator().evaluate(*inputs, config=config)

    actual = DuckDBFullAFactorEvaluator().evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=config,
    )

    _assert_result_parity(actual, expected)
    by_horizon = {item.horizon_sessions: item for item in actual.summaries[0].horizons}
    assert by_horizon[5].overlapping_forward_returns is False
    assert by_horizon[5].effective_newey_west_lag == 0
    assert by_horizon[20].overlapping_forward_returns is True
    assert by_horizon[20].effective_newey_west_lag == 1
    assert by_horizon[20].mean_long_short_spread is None


def test_duckdb_evaluator_runs_with_bounded_memory_and_spill_directory(tmp_path: Path) -> None:
    event_count = 32
    instrument_count = 2048
    instruments = tuple(f"{index:06d}.SZ" for index in range(instrument_count))
    event_times = [_time(day) for day in range(event_count) for _ in range(instrument_count)]
    instrument_ids = list(instruments) * event_count
    values = [
        float((index % instrument_count) + day)
        for day in range(event_count)
        for index in range(instrument_count)
    ]
    base_factors = pa.table(
        {
            "instrument_id": instrument_ids,
            "event_time": event_times,
            "factor_id": ["test.large"] * len(values),
            "factor_version": ["1"] * len(values),
            "value": values,
            "is_valid": [True] * len(values),
        }
    ).cast(factor_output_schema())
    inverse_factors = pa.table(
        {
            "instrument_id": instrument_ids,
            "event_time": event_times,
            "factor_id": ["test.large.inverse"] * len(values),
            "factor_version": ["1"] * len(values),
            "value": [-value for value in values],
            "is_valid": [True] * len(values),
        }
    ).cast(factor_output_schema())
    factors = pa.concat_tables((base_factors, inverse_factors))
    labels = pa.table(
        {
            "instrument_id": instrument_ids,
            "event_time": event_times,
            "entry_time": [
                _time(day + 1) for day in range(event_count) for _ in range(instrument_count)
            ],
            "exit_time": [
                _time(day + 2) for day in range(event_count) for _ in range(instrument_count)
            ],
            "horizon_sessions": [1] * len(values),
            "forward_return": [value / 1_000_000.0 for value in values],
            "is_valid": [True] * len(values),
            "invalid_reason": pa.array([None] * len(values), type=pa.string()),
            "entry_amount": [100_000.0 + value for value in values],
            "entry_tradable": [True] * len(values),
        }
    ).cast(forward_return_v2_schema())
    universe = pa.table(
        {
            "instrument_id": instrument_ids,
            "event_time": event_times,
            "eligible": [True] * len(values),
            "exclusion_reason": pa.array([None] * len(values), type=pa.string()),
            "industry_id": [
                f"I{index % 20:02d}"
                for _ in range(event_count)
                for index in range(instrument_count)
            ],
            "total_market_value": [1_000_000.0 + value for value in values],
        }
    ).cast(full_a_universe_schema())
    factor_path, label_path, universe_path = _write_inputs(
        tmp_path / "inputs", (factors, labels, universe)
    )
    spill = tmp_path / "spill"

    result = DuckDBFullAFactorEvaluator(
        temp_directory=spill,
        memory_limit="128MB",
        threads=1,
    ).evaluate(
        factor_values_path=factor_path,
        forward_returns_path=label_path,
        universe_path=universe_path,
        config=FullAFactorEvaluationConfig(
            horizons=(1,),
            quantiles=5,
            minimum_observations=100,
            newey_west_lag=1,
            annual_observations=12,
            capacity_participation_rate=0.01,
        ),
    )

    assert spill.is_dir()
    assert len(result.summaries) == 2
    assert result.event_metrics.num_rows == event_count * 2
    assert result.factor_correlations.to_pylist()[0]["observation_count"] == event_count
