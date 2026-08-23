"""DuckDB-backed out-of-core full-A factor evaluation.

The evaluator deliberately leaves the instrument-by-event panel in DuckDB.  Python
receives only factor identities, coverage scalars, event aggregates, quantile
aggregates and pairwise correlations, then applies the v2 oracle's summary semantics.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from trademaster.factors import factor_output_schema
from trademaster.research.factor_evaluation import (
    FullAFactorDiagnostics,
    FullAFactorEvaluationConfig,
    FullAFactorEvaluationResult,
    FullAHorizonDiagnostics,
    _drawdown,
    _newey_west_t_stat,
    _summary_stats,
    _t_stat,
    forward_return_v2_schema,
    full_a_event_metric_schema,
    full_a_factor_correlation_schema,
    full_a_quantile_metric_schema,
    full_a_universe_schema,
)


def _quoted_path(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _require_parquet_schema(path: Path, expected: pa.Schema, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} Parquet does not exist: {resolved}")
    try:
        actual = pq.read_schema(resolved).remove_metadata()
    except Exception as error:
        raise ValueError(f"{label} Parquet is unreadable") from error
    if actual != expected:
        raise ValueError(f"{label} schema drift")
    return resolved


def _fetch_count(connection: duckdb.DuckDBPyConnection, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError("DuckDB scalar count query returned no row")
    return int(row[0])


def _finite_values(rows: list[dict[str, object]], name: str) -> list[float]:
    result: list[float] = []
    for row in rows:
        raw = row[name]
        if raw is not None:
            value = float(cast(Any, raw))
            if math.isfinite(value):
                result.append(value)
    return result


def _build_summaries(
    *,
    identities: tuple[tuple[str, str], ...],
    coverage: dict[tuple[str, str], tuple[int, dict[int, int]]],
    universe_observation_count: int,
    label_valid_by_horizon: dict[int, int],
    invalid_reasons_by_horizon: dict[int, tuple[tuple[str, int], ...]],
    event_metrics: pa.Table,
    config: FullAFactorEvaluationConfig,
) -> tuple[FullAFactorDiagnostics, ...]:
    # This is event-level data (factor x horizon x observation event), never the full
    # instrument panel.  Keeping summary math here preserves the v2 oracle semantics.
    event_rows = event_metrics.to_pylist()
    denominator = universe_observation_count * len(config.horizons)
    summaries: list[FullAFactorDiagnostics] = []
    for identity in identities:
        valid_factor_count, joint_by_horizon = coverage[identity]
        direction = config.direction_by_identity.get(identity, 0)
        horizons: list[FullAHorizonDiagnostics] = []
        for horizon in config.horizons:
            rows = [
                row
                for row in event_rows
                if row["factor_id"] == identity[0]
                and row["factor_version"] == identity[1]
                and row["horizon_sessions"] == horizon
            ]
            rows.sort(key=lambda row: cast(Any, row["event_time"]))
            ic_values = _finite_values(rows, "pearson_ic")
            rank_ic_values = _finite_values(rows, "rank_ic")
            spreads = _finite_values(rows, "long_short_spread")
            monotonicities = _finite_values(rows, "quantile_monotonicity")
            turnovers = _finite_values(rows, "top_quantile_turnover")
            size_correlations = _finite_values(rows, "factor_size_correlation")
            size_neutral_rank_ics = _finite_values(rows, "size_neutral_rank_ic")
            industry_rank_ics = _finite_values(rows, "industry_rank_ic")
            capacities = _finite_values(rows, "capacity_proxy_cny")
            top_small_cap_shares = _finite_values(rows, "top_small_market_cap_share")
            top_mid_cap_shares = _finite_values(rows, "top_mid_market_cap_share")
            top_large_cap_shares = _finite_values(rows, "top_large_market_cap_share")
            mean_ic, ic_deviation, icir = _summary_stats(ic_values)
            mean_rank_ic, rank_deviation, rank_icir = _summary_stats(rank_ic_values)
            spread_mean, spread_deviation, _ = _summary_stats(spreads)
            drawdown, recovery = _drawdown(spreads)
            effective_lag = config.effective_newey_west_lag(horizon)
            horizons.append(
                FullAHorizonDiagnostics(
                    horizon_sessions=horizon,
                    event_count=len(rows),
                    label_observation_coverage=(
                        label_valid_by_horizon[horizon] / universe_observation_count
                    ),
                    joint_observation_coverage=(
                        joint_by_horizon[horizon] / universe_observation_count
                    ),
                    label_invalid_reason_counts=invalid_reasons_by_horizon[horizon],
                    effective_newey_west_lag=effective_lag,
                    overlapping_forward_returns=(config.has_overlapping_forward_returns(horizon)),
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
                        statistics.fmean(monotonicities) if monotonicities else None
                    ),
                    mean_top_quantile_turnover=(statistics.fmean(turnovers) if turnovers else None),
                    mean_factor_size_correlation=(
                        statistics.fmean(size_correlations) if size_correlations else None
                    ),
                    mean_industry_rank_ic=(
                        statistics.fmean(industry_rank_ics) if industry_rank_ics else None
                    ),
                    capacity_proxy_cny=(statistics.median(capacities) if capacities else None),
                    mean_size_neutral_rank_ic=(
                        statistics.fmean(size_neutral_rank_ics) if size_neutral_rank_ics else None
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
                factor_direction=direction,
                universe_observation_count=universe_observation_count,
                factor_observation_coverage=(valid_factor_count / universe_observation_count),
                label_observation_coverage=(sum(label_valid_by_horizon.values()) / denominator),
                joint_observation_coverage=(sum(joint_by_horizon.values()) / denominator),
                horizons=tuple(horizons),
            )
        )
    return tuple(summaries)


class DuckDBFullAFactorEvaluator:
    """Evaluate full-A Parquet panels without materializing them in Python."""

    def __init__(
        self,
        *,
        database_path: Path | None = None,
        temp_directory: Path | None = None,
        memory_limit: str | None = None,
        threads: int | None = None,
    ) -> None:
        if threads is not None and threads < 1:
            raise ValueError("DuckDB evaluator threads must be positive")
        self.database_path = database_path
        self.temp_directory = temp_directory
        self.memory_limit = memory_limit
        self.threads = threads

    @contextmanager
    def _connection(self) -> Iterator[duckdb.DuckDBPyConnection]:
        database = str(self.database_path.resolve()) if self.database_path else ":memory:"
        connection = duckdb.connect(database)
        try:
            if self.temp_directory is not None:
                directory = self.temp_directory.expanduser().resolve()
                directory.mkdir(parents=True, exist_ok=True)
                connection.execute("SET temp_directory = ?", [str(directory)])
            if self.memory_limit is not None:
                connection.execute("SET memory_limit = ?", [self.memory_limit])
            if self.threads is not None:
                connection.execute("SET threads = ?", [self.threads])
            connection.execute("SET preserve_insertion_order = false")
            yield connection
        finally:
            connection.close()

    def evaluate(
        self,
        *,
        factor_values_path: Path,
        forward_returns_path: Path,
        universe_path: Path,
        config: FullAFactorEvaluationConfig,
    ) -> FullAFactorEvaluationResult:
        factor_path = _require_parquet_schema(
            factor_values_path, factor_output_schema(), label="factor values"
        )
        label_path = _require_parquet_schema(
            forward_returns_path, forward_return_v2_schema(), label="forward returns"
        )
        pit_path = _require_parquet_schema(
            universe_path, full_a_universe_schema(), label="evaluation universe"
        )
        with self._connection() as connection:
            connection.execute(
                "CREATE TEMP VIEW factors AS SELECT * FROM read_parquet("
                + _quoted_path(factor_path)
                + ")"
            )
            connection.execute(
                "CREATE TEMP VIEW labels AS SELECT * FROM read_parquet("
                + _quoted_path(label_path)
                + ")"
            )
            connection.execute(
                "CREATE TEMP VIEW universe AS SELECT * FROM read_parquet("
                + _quoted_path(pit_path)
                + ")"
            )
            connection.execute(
                "CREATE TEMP TABLE factor_directions("
                "factor_id VARCHAR, factor_version VARCHAR, direction INTEGER)"
            )
            if config.factor_directions:
                connection.executemany(
                    "INSERT INTO factor_directions VALUES (?, ?, ?)",
                    list(config.factor_directions),
                )
            self._validate_inputs(connection, config)
            self._create_relations(connection, config)
            identities = tuple(
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    """
                    SELECT DISTINCT factor_id, factor_version
                    FROM factors
                    ORDER BY factor_id, factor_version
                    """
                ).fetchall()
            )
            universe_count = _fetch_count(connection, "SELECT count(*) FROM eligible_universe")
            label_valid_by_horizon = {horizon: 0 for horizon in config.horizons}
            for horizon, count in connection.execute(
                "SELECT horizon_sessions, count(*) FROM labels WHERE is_valid "
                "GROUP BY horizon_sessions"
            ).fetchall():
                label_valid_by_horizon[int(horizon)] = int(count)
            invalid_reason_counts: dict[int, dict[str, int]] = {
                horizon: {} for horizon in config.horizons
            }
            for horizon, raw_reason, count in connection.execute(
                "SELECT horizon_sessions, "
                "coalesce(nullif(invalid_reason, ''), 'unspecified_invalid'), count(*) "
                "FROM labels WHERE NOT is_valid GROUP BY horizon_sessions, 2"
            ).fetchall():
                invalid_reason_counts[int(horizon)][str(raw_reason)] = int(count)
            invalid_reasons_by_horizon = {
                horizon: tuple(sorted(invalid_reason_counts[horizon].items()))
                for horizon in config.horizons
            }
            coverage = self._coverage(connection, identities)
            event_metrics = self._event_metrics(connection, config)
            quantile_metrics = self._quantile_metrics(connection)
            factor_correlations = self._factor_correlations(connection)

        return FullAFactorEvaluationResult(
            summaries=_build_summaries(
                identities=identities,
                coverage=coverage,
                universe_observation_count=universe_count,
                label_valid_by_horizon=label_valid_by_horizon,
                invalid_reasons_by_horizon=invalid_reasons_by_horizon,
                event_metrics=event_metrics,
                config=config,
            ),
            event_metrics=event_metrics,
            quantile_metrics=quantile_metrics,
            factor_correlations=factor_correlations,
        )

    @staticmethod
    def _validate_inputs(
        connection: duckdb.DuckDBPyConnection,
        config: FullAFactorEvaluationConfig,
    ) -> None:
        checks = (
            (
                (
                    "SELECT count(*) FROM (SELECT 1 FROM universe GROUP BY event_time, "
                    "instrument_id HAVING count(*) > 1)"
                ),
                "evaluation universe contains duplicate keys",
            ),
            (
                (
                    "SELECT count(*) FROM (SELECT 1 FROM factors GROUP BY factor_id, "
                    "factor_version, event_time, instrument_id HAVING count(*) > 1)"
                ),
                "factor evaluation contains duplicate observations",
            ),
            (
                (
                    "SELECT count(*) FROM (SELECT 1 FROM labels GROUP BY event_time, "
                    "instrument_id, horizon_sessions HAVING count(*) > 1)"
                ),
                "forward labels contain duplicate keys",
            ),
            (
                """
                SELECT count(*) FROM factors AS factor
                ANTI JOIN universe AS pit
                  ON pit.event_time = factor.event_time
                 AND pit.instrument_id = factor.instrument_id
                """,
                "factor observation is outside the universe key set",
            ),
        )
        for query, message in checks:
            if _fetch_count(connection, query) > 0:
                raise ValueError(message)
        eligible = _fetch_count(connection, "SELECT count(*) FROM universe WHERE eligible")
        if eligible == 0:
            raise ValueError("evaluation universe contains no eligible observations")
        factors = _fetch_count(connection, "SELECT count(*) FROM factors")
        if factors == 0:
            raise ValueError("factor evaluation contains no factor identities")
        unknown_directions = _fetch_count(
            connection,
            """
            SELECT count(*) FROM factor_directions AS direction
            ANTI JOIN (SELECT DISTINCT factor_id, factor_version FROM factors) AS factor
              USING (factor_id, factor_version)
            """,
        )
        if unknown_directions:
            raise ValueError("factor direction references an unknown factor identity")

        connection.execute("CREATE TEMP TABLE requested_horizons(horizon_sessions INTEGER)")
        connection.executemany(
            "INSERT INTO requested_horizons VALUES (?)",
            [(horizon,) for horizon in config.horizons],
        )
        connection.execute(
            """
            CREATE TEMP VIEW expected_label_keys AS
            SELECT pit.event_time, pit.instrument_id, horizon.horizon_sessions
            FROM universe AS pit
            CROSS JOIN requested_horizons AS horizon
            WHERE pit.eligible
            """
        )
        missing = _fetch_count(
            connection,
            """
            SELECT count(*) FROM expected_label_keys AS expected
            ANTI JOIN labels AS label
              USING (event_time, instrument_id, horizon_sessions)
            """,
        )
        extra = _fetch_count(
            connection,
            """
            SELECT count(*) FROM labels AS label
            ANTI JOIN expected_label_keys AS expected
              USING (event_time, instrument_id, horizon_sessions)
            """,
        )
        if missing or extra:
            raise ValueError("forward label key coverage differs from PIT universe")

    @staticmethod
    def _create_relations(
        connection: duckdb.DuckDBPyConnection,
        config: FullAFactorEvaluationConfig,
    ) -> None:
        connection.execute(
            "CREATE TEMP VIEW eligible_universe AS SELECT * FROM universe WHERE eligible"
        )
        connection.execute(
            """
            CREATE TEMP VIEW pit_market_cap_buckets AS
            WITH ranked AS (
                SELECT event_time, instrument_id,
                       row_number() OVER (
                           PARTITION BY event_time
                           ORDER BY total_market_value, instrument_id
                       ) AS size_row,
                       count(*) OVER (PARTITION BY event_time) AS size_count
                FROM eligible_universe
                WHERE total_market_value > 0 AND isfinite(total_market_value)
            )
            SELECT event_time, instrument_id,
                   CASE least(3, floor((size_row - 1) * 3 / size_count) + 1)
                       WHEN 1 THEN 'small'
                       WHEN 2 THEN 'mid'
                       ELSE 'large'
                   END AS market_cap_bucket
            FROM ranked WHERE size_count >= 3
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW valid_factors AS
            SELECT factor.*,
                   coalesce(mapping.direction, 0)::INTEGER AS factor_direction,
                   CASE WHEN coalesce(mapping.direction, 0) = -1
                        THEN -factor.value ELSE factor.value END AS oriented_value
            FROM factors AS factor
            INNER JOIN eligible_universe AS pit USING (event_time, instrument_id)
            LEFT JOIN factor_directions AS mapping USING (factor_id, factor_version)
            WHERE factor.is_valid AND isfinite(factor.value)
            """
        )
        connection.execute(
            f"""
            CREATE TEMP VIEW ex_ante_factor_pool AS
            SELECT * EXCLUDE pool_count
            FROM (
                SELECT factor.*, pit.total_market_value, pit.industry_id,
                       count(*) OVER (
                           PARTITION BY factor.factor_id, factor.factor_version,
                                        factor.event_time
                       ) AS pool_count
                FROM valid_factors AS factor
                INNER JOIN eligible_universe AS pit USING (event_time, instrument_id)
            )
            WHERE pool_count >= {config.minimum_observations}
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW size_regression_stats AS
            SELECT factor_id, factor_version, event_time,
                   count(*) AS observation_count,
                   avg(value) AS mean_factor,
                   avg(ln(total_market_value)) AS mean_log_size,
                   var_pop(ln(total_market_value)) AS log_size_variance,
                   covar_pop(value, ln(total_market_value)) AS factor_size_covariance
            FROM ex_ante_factor_pool
            WHERE total_market_value > 0 AND isfinite(total_market_value)
            GROUP BY factor_id, factor_version, event_time
            """
        )
        connection.execute(
            f"""
            CREATE TEMP VIEW ex_ante_ranked AS
            SELECT factor.*,
                   CASE WHEN size.observation_count >= {config.minimum_observations}
                                  AND size.log_size_variance > 0
                        THEN factor.value - (
                            size.mean_factor
                            + size.factor_size_covariance / size.log_size_variance
                              * (ln(factor.total_market_value) - size.mean_log_size)
                        )
                        ELSE NULL
                   END AS size_neutral_value,
                   least(
                       {config.quantiles},
                       floor(
                           (row_number() OVER deterministic_order - 1)
                           * {config.quantiles}
                           / count(*) OVER sample_group
                       ) + 1
                   )::SMALLINT AS quantile
            FROM ex_ante_factor_pool AS factor
            LEFT JOIN size_regression_stats AS size
              USING (factor_id, factor_version, event_time)
            WINDOW
                sample_group AS (
                    PARTITION BY factor_id, factor_version, event_time
                ),
                deterministic_order AS (
                    PARTITION BY factor_id, factor_version, event_time
                    ORDER BY oriented_value, instrument_id
                )
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW joined_panel AS
                   SELECT factor.factor_id,
                   factor.factor_version,
                   factor.event_time,
                   factor.instrument_id,
                   label.horizon_sessions,
                   factor.value AS factor_value,
                   factor.size_neutral_value,
                   factor.factor_direction,
                   factor.quantile,
                   label.forward_return,
                   factor.total_market_value,
                   factor.industry_id
            FROM ex_ante_ranked AS factor
            INNER JOIN labels AS label USING (event_time, instrument_id)
            WHERE label.is_valid
            """
        )
        connection.execute(
            f"""
            CREATE TEMP VIEW qualified_panel AS
            SELECT * EXCLUDE observation_count
            FROM (
                SELECT joined_panel.*,
                       count(*) OVER (
                           PARTITION BY factor_id, factor_version,
                                        event_time, horizon_sessions
                       ) AS observation_count
                FROM joined_panel
            )
            WHERE observation_count >= {config.minimum_observations}
            """
        )
        connection.execute(
            f"""
            CREATE TEMP VIEW size_neutral_qualified AS
            SELECT * EXCLUDE neutral_observation_count
            FROM (
                SELECT joined_panel.*,
                       count(*) OVER (
                           PARTITION BY factor_id, factor_version,
                                        event_time, horizon_sessions
                       ) AS neutral_observation_count
                FROM joined_panel
                WHERE size_neutral_value IS NOT NULL
                  AND isfinite(size_neutral_value)
            )
            WHERE neutral_observation_count >= {config.minimum_observations}
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW size_neutral_ranked AS
            SELECT size_neutral_qualified.*,
                   rank() OVER residual_order
                       + (count(*) OVER residual_tie - 1) / 2.0 AS residual_rank,
                   rank() OVER return_order
                       + (count(*) OVER return_tie - 1) / 2.0 AS neutral_return_rank
            FROM size_neutral_qualified
            WINDOW
                residual_order AS (
                    PARTITION BY factor_id, factor_version, event_time, horizon_sessions
                    ORDER BY size_neutral_value
                ),
                residual_tie AS (
                    PARTITION BY factor_id, factor_version, event_time,
                                 horizon_sessions, size_neutral_value
                ),
                return_order AS (
                    PARTITION BY factor_id, factor_version, event_time, horizon_sessions
                    ORDER BY forward_return
                ),
                return_tie AS (
                    PARTITION BY factor_id, factor_version, event_time,
                                 horizon_sessions, forward_return
                )
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW ranked_panel AS
            SELECT qualified_panel.*,
                   rank() OVER sample_order
                       + (count(*) OVER sample_tie - 1) / 2.0 AS factor_rank,
                   rank() OVER return_order
                       + (count(*) OVER return_tie - 1) / 2.0 AS return_rank
            FROM qualified_panel
            WINDOW
                sample_order AS (
                    PARTITION BY factor_id, factor_version, event_time, horizon_sessions
                    ORDER BY factor_value
                ),
                sample_tie AS (
                    PARTITION BY factor_id, factor_version, event_time,
                                 horizon_sessions, factor_value
                ),
                return_order AS (
                    PARTITION BY factor_id, factor_version, event_time, horizon_sessions
                    ORDER BY forward_return
                ),
                return_tie AS (
                    PARTITION BY factor_id, factor_version, event_time,
                                 horizon_sessions, forward_return
                )
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW quantile_aggregates AS
            SELECT factor_id, factor_version, event_time, horizon_sessions, quantile,
                   avg(forward_return) AS mean_forward_return,
                   count(*)::INTEGER AS observation_count
            FROM ranked_panel
            GROUP BY factor_id, factor_version, event_time, horizon_sessions, quantile
            """
        )

    @staticmethod
    def _coverage(
        connection: duckdb.DuckDBPyConnection,
        identities: tuple[tuple[str, str], ...],
    ) -> dict[tuple[str, str], tuple[int, dict[int, int]]]:
        valid_by_identity = {
            (str(row[0]), str(row[1])): int(row[2])
            for row in connection.execute(
                """
                SELECT factor_id, factor_version, count(*)
                FROM valid_factors
                GROUP BY factor_id, factor_version
                """
            ).fetchall()
        }
        joint_by_identity: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
        for row in connection.execute(
            """
            SELECT factor.factor_id, factor.factor_version,
                   label.horizon_sessions, count(*)
            FROM valid_factors AS factor
            INNER JOIN labels AS label USING (event_time, instrument_id)
            WHERE label.is_valid
            GROUP BY factor.factor_id, factor.factor_version, label.horizon_sessions
            """
        ).fetchall():
            joint_by_identity[(str(row[0]), str(row[1]))][int(row[2])] = int(row[3])
        horizons = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT horizon_sessions FROM requested_horizons ORDER BY horizon_sessions"
            ).fetchall()
        )
        return {
            identity: (
                valid_by_identity.get(identity, 0),
                {
                    horizon: joint_by_identity.get(identity, {}).get(horizon, 0)
                    for horizon in horizons
                },
            )
            for identity in identities
        }

    @staticmethod
    def _event_metrics(
        connection: duckdb.DuckDBPyConnection,
        config: FullAFactorEvaluationConfig,
    ) -> pa.Table:
        rate = config.capacity_participation_rate
        quantiles = config.quantiles
        entry_horizon = config.horizons[0]
        overlapping_horizons = tuple(
            horizon
            for horizon in config.horizons
            if config.has_overlapping_forward_returns(horizon)
        )
        raw_spread = (
            f"max(mean_forward_return) FILTER (WHERE quantile = {quantiles}) "
            "- max(mean_forward_return) FILTER (WHERE quantile = 1)"
        )
        spread_expression = (
            "CASE WHEN horizon_sessions IN ("
            + ",".join(str(horizon) for horizon in overlapping_horizons)
            + f") THEN NULL ELSE {raw_spread} END"
            if overlapping_horizons
            else raw_spread
        )
        query = f"""
            WITH event_base_raw AS (
                SELECT factor_id, factor_version, event_time, horizon_sessions,
                       count(*)::INTEGER AS observation_count,
                       corr(factor_value, forward_return) AS pearson_ic,
                       corr(factor_rank, return_rank) AS rank_ic,
                       corr(
                           factor_value,
                           CASE WHEN total_market_value > 0
                                THEN ln(total_market_value) ELSE NULL END
                       ) AS factor_size_correlation
                FROM ranked_panel
                GROUP BY factor_id, factor_version, event_time, horizon_sessions
            ),
            size_neutral_event AS (
                SELECT factor_id, factor_version, event_time, horizon_sessions,
                       CASE WHEN isfinite(corr(residual_rank, neutral_return_rank))
                            THEN corr(residual_rank, neutral_return_rank) ELSE NULL END
                            AS size_neutral_rank_ic
                FROM size_neutral_ranked
                GROUP BY factor_id, factor_version, event_time, horizon_sessions
            ),
            industry_base AS (
                SELECT *,
                       rank() OVER factor_order
                           + (count(*) OVER factor_tie - 1) / 2.0 AS industry_factor_rank,
                       rank() OVER return_order
                           + (count(*) OVER return_tie - 1) / 2.0 AS industry_return_rank
                FROM qualified_panel
                WHERE industry_id IS NOT NULL
                WINDOW
                    factor_order AS (
                        PARTITION BY factor_id, factor_version, event_time,
                                     horizon_sessions, industry_id
                        ORDER BY factor_value
                    ),
                    factor_tie AS (
                        PARTITION BY factor_id, factor_version, event_time,
                                     horizon_sessions, industry_id, factor_value
                    ),
                    return_order AS (
                        PARTITION BY factor_id, factor_version, event_time,
                                     horizon_sessions, industry_id
                        ORDER BY forward_return
                    ),
                    return_tie AS (
                        PARTITION BY factor_id, factor_version, event_time,
                                     horizon_sessions, industry_id, forward_return
                    )
            ),
            industry_values AS (
                SELECT factor_id, factor_version, event_time, horizon_sessions, industry_id,
                       CASE WHEN count(*) >= 2
                                 AND isfinite(corr(industry_factor_rank, industry_return_rank))
                            THEN corr(industry_factor_rank, industry_return_rank)
                            ELSE NULL END AS industry_rank_ic
                FROM industry_base
                GROUP BY factor_id, factor_version, event_time,
                         horizon_sessions, industry_id
            ),
            industry_event AS (
                SELECT factor_id, factor_version, event_time, horizon_sessions,
                       avg(industry_rank_ic) AS industry_rank_ic
                FROM industry_values
                GROUP BY factor_id, factor_version, event_time, horizon_sessions
            ),
            quantile_event AS (
                SELECT factor_id, factor_version, event_time, horizon_sessions,
                       CASE WHEN count(*) >= 2
                                 AND isfinite(corr(quantile::DOUBLE, mean_forward_return))
                            THEN corr(quantile::DOUBLE, mean_forward_return)
                            ELSE NULL END AS quantile_monotonicity,
                       {spread_expression} AS long_short_spread
                FROM quantile_aggregates
                GROUP BY factor_id, factor_version, event_time, horizon_sessions
            ),
            entry_evidence AS (
                SELECT event_time, instrument_id, entry_amount, entry_tradable
                FROM labels WHERE horizon_sessions = {entry_horizon}
            ),
            top_members AS (
                SELECT factor.factor_id, factor.factor_version, factor.event_time,
                       factor.instrument_id, entry.entry_amount, entry.entry_tradable,
                       bucket.market_cap_bucket
                FROM ex_ante_ranked AS factor
                LEFT JOIN entry_evidence AS entry USING (event_time, instrument_id)
                LEFT JOIN pit_market_cap_buckets AS bucket USING (event_time, instrument_id)
                WHERE factor.quantile = {quantiles}
            ),
            top_counts AS (
                SELECT factor_id, factor_version, event_time,
                       count(*) AS member_count,
                       count(*) FILTER (
                           WHERE entry_tradable AND entry_amount > 0
                             AND isfinite(entry_amount)
                       ) AS liquid_member_count,
                       count(market_cap_bucket) AS bucketed_member_count,
                       count(*) FILTER (WHERE market_cap_bucket = 'small') AS small_count,
                       count(*) FILTER (WHERE market_cap_bucket = 'mid') AS mid_count,
                       count(*) FILTER (WHERE market_cap_bucket = 'large') AS large_count,
                       min(entry_amount) FILTER (
                           WHERE entry_tradable AND entry_amount > 0
                             AND isfinite(entry_amount)
                       ) AS minimum_amount
                FROM top_members
                GROUP BY factor_id, factor_version, event_time
            ),
            event_sequence AS (
                SELECT top.*,
                       lag(top.event_time) OVER (
                           PARTITION BY factor_id, factor_version
                           ORDER BY top.event_time
                       ) AS previous_event_time
                FROM top_counts AS top
            ),
            top_overlap AS (
                SELECT sequence.factor_id, sequence.factor_version,
                       sequence.event_time, sequence.member_count,
                       sequence.liquid_member_count, sequence.bucketed_member_count,
                       sequence.small_count, sequence.mid_count, sequence.large_count,
                       sequence.minimum_amount,
                       sequence.previous_event_time,
                       previous.member_count AS previous_member_count,
                       count(previous_member.instrument_id) AS overlap_count
                FROM event_sequence AS sequence
                LEFT JOIN event_sequence AS previous
                  ON previous.factor_id = sequence.factor_id
                 AND previous.factor_version = sequence.factor_version
                 AND previous.event_time = sequence.previous_event_time
                LEFT JOIN top_members AS current_member
                  ON current_member.factor_id = sequence.factor_id
                 AND current_member.factor_version = sequence.factor_version
                 AND current_member.event_time = sequence.event_time
                LEFT JOIN top_members AS previous_member
                  ON previous_member.factor_id = sequence.factor_id
                 AND previous_member.factor_version = sequence.factor_version
                 AND previous_member.event_time = sequence.previous_event_time
                 AND previous_member.instrument_id = current_member.instrument_id
                GROUP BY sequence.factor_id, sequence.factor_version,
                         sequence.event_time, sequence.member_count,
                         sequence.liquid_member_count, sequence.bucketed_member_count,
                         sequence.small_count, sequence.mid_count, sequence.large_count,
                         sequence.minimum_amount,
                         sequence.previous_event_time, previous.member_count
            )
            SELECT base.factor_id,
                   base.factor_version,
                   base.event_time,
                   base.horizon_sessions::INTEGER AS horizon_sessions,
                   base.observation_count,
                   CASE WHEN isfinite(base.pearson_ic)
                        THEN base.pearson_ic ELSE NULL END AS pearson_ic,
                   CASE WHEN isfinite(base.rank_ic)
                        THEN base.rank_ic ELSE NULL END AS rank_ic,
                   CASE WHEN isfinite(base.factor_size_correlation)
                        THEN base.factor_size_correlation ELSE NULL END
                        AS factor_size_correlation,
                   neutral.size_neutral_rank_ic,
                   industry.industry_rank_ic,
                   quantile.long_short_spread,
                   quantile.quantile_monotonicity,
                   CASE WHEN top.previous_event_time IS NULL
                                  OR top.member_count = 0 THEN NULL
                        ELSE 1.0 - top.overlap_count::DOUBLE
                                   / greatest(
                                       coalesce(top.previous_member_count, 0),
                                       top.member_count
                                   )
                   END AS top_quantile_turnover,
                   CASE WHEN top.minimum_amount IS NULL
                                  OR top.liquid_member_count <> top.member_count THEN NULL
                        ELSE top.minimum_amount * {rate} * top.member_count
                   END AS capacity_proxy_cny,
                   CASE WHEN top.bucketed_member_count <> top.member_count
                                  OR top.member_count = 0 THEN NULL
                        ELSE top.small_count::DOUBLE / top.member_count
                   END AS top_small_market_cap_share,
                   CASE WHEN top.bucketed_member_count <> top.member_count
                                  OR top.member_count = 0 THEN NULL
                        ELSE top.mid_count::DOUBLE / top.member_count
                   END AS top_mid_market_cap_share,
                   CASE WHEN top.bucketed_member_count <> top.member_count
                                  OR top.member_count = 0 THEN NULL
                        ELSE top.large_count::DOUBLE / top.member_count
                   END AS top_large_market_cap_share
            FROM event_base_raw AS base
            LEFT JOIN size_neutral_event AS neutral
              USING (factor_id, factor_version, event_time, horizon_sessions)
            LEFT JOIN industry_event AS industry
              USING (factor_id, factor_version, event_time, horizon_sessions)
            LEFT JOIN quantile_event AS quantile
              USING (factor_id, factor_version, event_time, horizon_sessions)
            LEFT JOIN top_overlap AS top
              USING (factor_id, factor_version, event_time)
            ORDER BY base.factor_id, base.factor_version,
                     base.event_time, base.horizon_sessions
        """
        return connection.execute(query).to_arrow_table().cast(full_a_event_metric_schema())

    @staticmethod
    def _quantile_metrics(connection: duckdb.DuckDBPyConnection) -> pa.Table:
        return (
            connection.execute(
                """
                SELECT factor_id, factor_version, event_time,
                       horizon_sessions::INTEGER AS horizon_sessions, quantile,
                       mean_forward_return, observation_count
                FROM quantile_aggregates
                ORDER BY factor_id, factor_version, event_time,
                         horizon_sessions, quantile
                """
            )
            .to_arrow_table()
            .cast(full_a_quantile_metric_schema())
        )

    @staticmethod
    def _factor_correlations(connection: duckdb.DuckDBPyConnection) -> pa.Table:
        query = """
            WITH identities AS (
                SELECT DISTINCT factor_id, factor_version FROM factors
            ),
            identity_pairs AS (
                SELECT left_identity.factor_id AS left_factor_id,
                       left_identity.factor_version AS left_factor_version,
                       right_identity.factor_id AS right_factor_id,
                       right_identity.factor_version AS right_factor_version
                FROM identities AS left_identity
                CROSS JOIN identities AS right_identity
                WHERE left_identity.factor_id < right_identity.factor_id
                   OR (left_identity.factor_id = right_identity.factor_id
                       AND left_identity.factor_version < right_identity.factor_version)
            ),
            pair_samples AS (
                SELECT left_factor.factor_id AS left_factor_id,
                       left_factor.factor_version AS left_factor_version,
                       right_factor.factor_id AS right_factor_id,
                       right_factor.factor_version AS right_factor_version,
                       left_factor.event_time,
                       left_factor.instrument_id,
                       left_factor.value AS left_value,
                       right_factor.value AS right_value
                FROM valid_factors AS left_factor
                INNER JOIN valid_factors AS right_factor
                  USING (event_time, instrument_id)
                WHERE left_factor.factor_id < right_factor.factor_id
                   OR (left_factor.factor_id = right_factor.factor_id
                       AND left_factor.factor_version < right_factor.factor_version)
            ),
            ranked_pairs AS (
                SELECT *,
                       rank() OVER left_order
                           + (count(*) OVER left_tie - 1) / 2.0 AS left_rank,
                       rank() OVER right_order
                           + (count(*) OVER right_tie - 1) / 2.0 AS right_rank
                FROM pair_samples
                WINDOW
                    left_order AS (
                        PARTITION BY left_factor_id, left_factor_version,
                                     right_factor_id, right_factor_version, event_time
                        ORDER BY left_value
                    ),
                    left_tie AS (
                        PARTITION BY left_factor_id, left_factor_version,
                                     right_factor_id, right_factor_version,
                                     event_time, left_value
                    ),
                    right_order AS (
                        PARTITION BY left_factor_id, left_factor_version,
                                     right_factor_id, right_factor_version, event_time
                        ORDER BY right_value
                    ),
                    right_tie AS (
                        PARTITION BY left_factor_id, left_factor_version,
                                     right_factor_id, right_factor_version,
                                     event_time, right_value
                    )
            ),
            event_correlations AS (
                SELECT left_factor_id, left_factor_version,
                       right_factor_id, right_factor_version, event_time,
                       CASE WHEN isfinite(corr(left_value, right_value))
                            THEN corr(left_value, right_value) ELSE NULL END
                            AS pearson_correlation,
                       CASE WHEN isfinite(corr(left_rank, right_rank))
                            THEN corr(left_rank, right_rank) ELSE NULL END
                            AS rank_correlation
                FROM ranked_pairs
                GROUP BY left_factor_id, left_factor_version,
                         right_factor_id, right_factor_version, event_time
            ),
            correlations AS (
                SELECT left_factor_id, left_factor_version,
                       right_factor_id, right_factor_version,
                       avg(pearson_correlation) AS pearson_correlation,
                       avg(rank_correlation) AS rank_correlation,
                       count(rank_correlation)::BIGINT AS observation_count
                FROM event_correlations
                GROUP BY left_factor_id, left_factor_version,
                         right_factor_id, right_factor_version
            )
            SELECT identity.left_factor_id, identity.left_factor_version,
                   identity.right_factor_id, identity.right_factor_version,
                   correlation.pearson_correlation,
                   correlation.rank_correlation,
                   coalesce(correlation.observation_count, 0)::BIGINT
                       AS observation_count
            FROM identity_pairs AS identity
            LEFT JOIN correlations AS correlation
              USING (left_factor_id, left_factor_version,
                     right_factor_id, right_factor_version)
            ORDER BY left_factor_id, left_factor_version,
                     right_factor_id, right_factor_version
        """
        return connection.execute(query).to_arrow_table().cast(full_a_factor_correlation_schema())


__all__ = ["DuckDBFullAFactorEvaluator"]
