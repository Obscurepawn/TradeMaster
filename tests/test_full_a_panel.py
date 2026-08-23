from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.factors import factor_output_schema, public_executable_factor_suite
from trademaster.research.factor_evaluation import (
    forward_return_v2_schema,
    full_a_market_schema,
    full_a_universe_schema,
)
from trademaster.research.full_a import (
    DownloadPlanManifest,
    DownloadTask,
    FullAResearchConfig,
    FullAResearchStore,
)
from trademaster.research.full_a_panel import (
    FactorResearchPanelBuilder,
    FullAPanelConfig,
    full_a_dense_panel_schema,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _schema(fields: list[object]) -> pa.Schema:
    return pa.schema(cast("list[pa.Field[Any]]", fields))


_SCHEMAS = {
    "stock_basic": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("exchange", pa.string()),
            pa.field("curr_type", pa.string()),
            pa.field("list_status", pa.string()),
            pa.field("list_date", pa.string()),
            pa.field("delist_date", pa.string()),
        ]
    ),
    "daily": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("trade_date", pa.string()),
            pa.field("open", pa.float64()),
            pa.field("high", pa.float64()),
            pa.field("low", pa.float64()),
            pa.field("close", pa.float64()),
            pa.field("pre_close", pa.float64()),
            pa.field("vol", pa.float64()),
            pa.field("amount", pa.float64()),
        ]
    ),
    "adj_factor": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("trade_date", pa.string()),
            pa.field("adj_factor", pa.float64()),
        ]
    ),
    "daily_basic": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("trade_date", pa.string()),
            pa.field("turnover_rate", pa.float64()),
            pa.field("total_mv", pa.float64()),
            pa.field("circ_mv", pa.float64()),
            pa.field("pe_ttm", pa.float64()),
            pa.field("pb", pa.float64()),
            pa.field("ps_ttm", pa.float64()),
            pa.field("dv_ttm", pa.float64()),
        ]
    ),
    "stk_limit": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("trade_date", pa.string()),
            pa.field("up_limit", pa.float64()),
            pa.field("down_limit", pa.float64()),
        ]
    ),
    "suspend_d": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("trade_date", pa.string()),
            pa.field("suspend_type", pa.string()),
            pa.field("suspend_timing", pa.string()),
        ]
    ),
    "stock_st": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("trade_date", pa.string()),
            pa.field("type", pa.string()),
            pa.field("type_name", pa.string()),
            pa.field("name", pa.string()),
        ]
    ),
}


def _request_metadata(task: DownloadTask) -> dict[bytes, bytes]:
    def encode(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()

    return {
        b"trademaster.strategy.endpoint": task.endpoint.encode(),
        b"trademaster.strategy.params": encode(dict(task.params)),
        b"trademaster.strategy.fields": encode(task.fields),
        b"trademaster.strategy.request_sha256": task.request_sha256.encode(),
    }


def _task(endpoint: str, key: str, *, trade_date: date | None = None) -> DownloadTask:
    phase: Literal["bootstrap", "market", "status"] = (
        "bootstrap"
        if endpoint == "stock_basic"
        else ("market" if endpoint in {"daily", "adj_factor", "daily_basic"} else "status")
    )
    params = {"trade_date": trade_date.strftime("%Y%m%d")} if trade_date else {"list_status": key}
    return DownloadTask.build(
        task_key=(
            f"bootstrap.stock_basic.{key}"
            if endpoint == "stock_basic"
            else f"{phase}.{endpoint}.{key}"
        ),
        phase=phase,
        endpoint=endpoint,
        params=params,
        fields=tuple(_SCHEMAS[endpoint].names),
        page_limit=6000,
        allow_empty=endpoint in {"suspend_d", "stock_st", "stock_basic"},
    )


def _synthetic_plan() -> DownloadPlanManifest:
    sessions = tuple(date(2021, 11, 10) + timedelta(days=offset) for offset in range(31))
    tasks = [_task("stock_basic", status) for status in ("L", "D", "P", "G")]
    for session in sessions:
        key = session.strftime("%Y%m%d")
        tasks.extend(
            _task(endpoint, key, trade_date=session)
            for endpoint in (
                "daily",
                "adj_factor",
                "daily_basic",
                "stk_limit",
                "suspend_d",
                "stock_st",
            )
        )
    config = FullAResearchConfig(
        start=sessions[0],
        requested_end=sessions[-1],
        statement_start=date(2020, 1, 1),
        benchmarks=("000300.SH",),
    )
    return DownloadPlanManifest.build(
        config=config,
        resolved_end=sessions[-1],
        instruments=("000001.SZ", "000999.SZ", "430001.BJ", "600001.SH"),
        excluded_instruments=(),
        sessions=sessions,
        tasks=tuple(tasks),
    )


def _endpoint_rows(
    endpoint: str,
    task: DownloadTask,
    plan: DownloadPlanManifest,
    *,
    non_finite_adjustment_at: tuple[str, date] | None = None,
) -> list[dict[str, object]]:
    if endpoint == "stock_basic":
        status = dict(task.params)["list_status"]
        if status == "L":
            return [
                {
                    "ts_code": "000001.SZ",
                    "exchange": "SZSE",
                    "curr_type": "CNY",
                    "list_status": "L",
                    "list_date": "20211101",
                    "delist_date": None,
                },
                {
                    # A pre-BSE NEEQ date must not make this active before 2021-11-15.
                    "ts_code": "430001.BJ",
                    "exchange": "BSE",
                    "curr_type": "CNY",
                    "list_status": "L",
                    "list_date": "20200101",
                    "delist_date": None,
                },
            ]
        if status == "D":
            return [
                {
                    "ts_code": "600001.SH",
                    "exchange": "SSE",
                    "curr_type": "CNY",
                    "list_status": "D",
                    "list_date": "20211101",
                    "delist_date": "20211120",
                }
            ]
        return []

    session = date.fromisoformat(
        f"{dict(task.params)['trade_date'][:4]}-{dict(task.params)['trade_date'][4:6]}-{dict(task.params)['trade_date'][6:]}"
    )
    # 000999.SZ deliberately has market data but no current stock_basic row.
    active = ["000001.SZ", "000999.SZ"]
    if session <= date(2021, 11, 20):
        active.append("600001.SH")
    # Raw evidence deliberately contains BJ bars before its effective BSE start.
    active.append("430001.BJ")
    missing_bar = ("000001.SZ", date(2021, 11, 25))
    inferred_missing = ("000999.SZ", date(2021, 11, 24))
    unexplained_missing = ("430001.BJ", date(2021, 11, 28))
    bar_instruments = [
        item
        for item in active
        if (item, session) not in {missing_bar, inferred_missing, unexplained_missing}
    ]
    day_index = plan.sessions.index(session)
    if endpoint == "daily":
        return [
            {
                "ts_code": item,
                "trade_date": session.strftime("%Y%m%d"),
                "open": 10.0 + day_index,
                "high": 11.0 + day_index,
                "low": 9.0 + day_index,
                "close": 10.5 + day_index,
                "pre_close": 9.5 + day_index,
                "vol": 1000.0,
                "amount": 10_000.0 + day_index,
            }
            for item in bar_instruments
        ]
    if endpoint == "adj_factor":
        return [
            {
                "ts_code": item,
                "trade_date": session.strftime("%Y%m%d"),
                "adj_factor": (
                    float("nan") if non_finite_adjustment_at == (item, session) else 2.0
                ),
            }
            for item in active
            if (item, session) != unexplained_missing
        ]
    if endpoint == "daily_basic":
        return [
            {
                "ts_code": item,
                "trade_date": session.strftime("%Y%m%d"),
                "turnover_rate": 1.5,
                "total_mv": 100_000.0 + day_index,
                "circ_mv": 80_000.0,
                "pe_ttm": 10.0,
                "pb": 1.0,
                "ps_ttm": 2.0,
                "dv_ttm": 0.5,
            }
            for item in bar_instruments
        ]
    if endpoint == "stk_limit":
        return [
            {
                "ts_code": item,
                "trade_date": session.strftime("%Y%m%d"),
                "up_limit": 100.0,
                "down_limit": 1.0,
            }
            for item in bar_instruments
        ]
    if endpoint == "suspend_d" and session == missing_bar[1]:
        return [
            {
                "ts_code": missing_bar[0],
                "trade_date": session.strftime("%Y%m%d"),
                "suspend_type": "S",
                "suspend_timing": "全天",
            }
        ]
    if endpoint == "stock_st" and session == date(2021, 11, 26):
        return [
            {
                "ts_code": "000001.SZ",
                "trade_date": session.strftime("%Y%m%d"),
                "type": "S",
                "type_name": "ST",
                "name": "ST样本",
            }
        ]
    return []


def _complete_synthetic_store(
    root: Path,
    plan: DownloadPlanManifest,
    *,
    non_finite_adjustment_at: tuple[str, date] | None = None,
) -> FullAResearchStore:
    store = FullAResearchStore(root, clock=lambda: NOW)
    store.register(plan)
    objects = root / "objects"
    objects.mkdir()
    for task in plan.tasks:
        table = pa.Table.from_pylist(
            _endpoint_rows(
                task.endpoint,
                task,
                plan,
                non_finite_adjustment_at=non_finite_adjustment_at,
            ),
            schema=_SCHEMAS[task.endpoint],
        ).replace_schema_metadata(_request_metadata(task))
        path = objects / f"{task.task_id}.parquet"
        pq.write_table(table, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        store.mark_running(plan.plan_sha256, task)
        store.mark_completed(
            plan.plan_sha256,
            task,
            request_sha256=task.request_sha256,
            content_sha256=digest,
            row_count=table.num_rows,
            evidence_path=path,
        )
    return store


def test_panel_builder_keeps_dense_active_rows_and_observes_after_full_window(
    tmp_path: Path,
) -> None:
    plan = _synthetic_plan()
    store = _complete_synthetic_store(tmp_path / "source", plan)
    output = tmp_path / "derived"
    config = FullAPanelConfig(observation_frequency="month_end", horizons=(1, 5))
    try:
        builder = FactorResearchPanelBuilder(store=store, output_root=output)
        manifest = builder.build(plan, config=config)

        assert manifest.plan_sha256 == plan.plan_sha256
        assert len(manifest.manifest_sha256) == 64
        assert len(manifest.builder_code_sha256) == 64
        assert len(manifest.factor_definition_bindings) == 11
        assert all(len(item[2]) == 64 for item in manifest.factor_definition_bindings)
        assert manifest.coverage.session_count == 31
        # Two SZ x31 + SH 11 + BJ 26 (effective BSE start is 2021-11-15).
        assert manifest.coverage.active_panel_rows == 99
        assert manifest.coverage.inferred_lifecycle_instrument_count == 1
        assert manifest.coverage.missing_bar_rows == 3
        assert manifest.coverage.inferred_suspend_rows == 1
        assert manifest.coverage.unexplained_missing_bar_rows == 1
        assert manifest.coverage.bar_without_adj_factor_rows == 0
        assert manifest.coverage.bar_without_daily_basic_rows == 0
        assert manifest.coverage.observation_rows == 6
        assert manifest.coverage.factor_rows == 66
        assert manifest.coverage.label_rows == 12
        assert {item.name for item in manifest.artifacts} == {
            "dense_panel",
            "factor_values",
            "forward_returns",
            "market",
            "source_objects",
            "universe",
        }

        dense_path = builder.artifact_path(manifest, "dense_panel")
        assert pq.read_schema(dense_path).remove_metadata() == full_a_dense_panel_schema()
        assert (
            pq.read_schema(builder.artifact_path(manifest, "market")).remove_metadata()
            == full_a_market_schema()
        )
        universe_path = builder.artifact_path(manifest, "universe")
        assert pq.read_schema(universe_path).remove_metadata() == full_a_universe_schema()
        assert pq.read_metadata(universe_path).num_rows == manifest.coverage.observation_rows
        assert (
            pq.read_schema(builder.artifact_path(manifest, "factor_values")).remove_metadata()
            == factor_output_schema()
        )
        assert (
            pq.read_schema(builder.artifact_path(manifest, "forward_returns")).remove_metadata()
            == forward_return_v2_schema()
        )
        with builder.query() as connection:
            assert connection.execute(
                """
                SELECT count(*) FROM read_parquet(?)
                WHERE instrument_id = '430001.BJ' AND session_date < DATE '2021-11-15'
                """,
                [str(dense_path)],
            ).fetchone() == (0,)
            suspended = connection.execute(
                """
                SELECT bar_available, explicitly_suspended, tradable
                FROM read_parquet(?)
                WHERE instrument_id = '000001.SZ' AND session_date = DATE '2021-11-25'
                """,
                [str(dense_path)],
            ).fetchone()
            assert suspended == (False, True, False)
            missing = connection.execute(
                """
                SELECT bar_available, explicitly_suspended, tradable
                FROM read_parquet(?)
                WHERE instrument_id = '430001.BJ' AND session_date = DATE '2021-11-28'
                """,
                [str(dense_path)],
            ).fetchone()
            assert missing == (False, False, False)

            factor_path = builder.artifact_path(manifest, "factor_values")
            identities = connection.execute(
                "SELECT DISTINCT factor_id FROM read_parquet(?) ORDER BY factor_id",
                [str(factor_path)],
            ).fetchall()
            assert len(identities) == 11
            assert ("huatai53.size.log_total_market_value",) in identities
            # Missing 2021-11-25 is the exact five-session lag at the Nov observation.
            assert connection.execute(
                """
                SELECT is_valid FROM read_parquet(?)
                WHERE instrument_id = '000001.SZ'
                  AND CAST(event_time AS DATE) = DATE '2021-11-30'
                  AND factor_id = 'gtja191.alpha014'
                """,
                [str(factor_path)],
            ).fetchone() == (False,)

            label_path = builder.artifact_path(manifest, "forward_returns")
            label = connection.execute(
                """
                SELECT CAST(entry_time AS DATE), CAST(exit_time AS DATE), forward_return,
                       is_valid, entry_amount
                FROM read_parquet(?)
                WHERE instrument_id = '000001.SZ'
                  AND CAST(event_time AS DATE) = DATE '2021-11-30'
                  AND horizon_sessions = 1
                """,
                [str(label_path)],
            ).fetchone()
            assert label is not None
            assert label[:2] == (date(2021, 12, 1), date(2021, 12, 2))
            assert label[2] == pytest.approx((10.5 + 22) / (10.5 + 21) - 1.0)
            assert label[3] is True
            assert label[4] == pytest.approx((10_000.0 + 21) * 1000.0)

        assert builder.build(plan, config=config) == manifest
        assert builder.load_manifest(manifest.manifest_sha256) == manifest
    finally:
        store.close()


def test_panel_dense_session_factors_match_managed_suite_and_reject_non_finite_prices(
    tmp_path: Path,
) -> None:
    plan = _synthetic_plan()
    poisoned_session = ("000001.SZ", date(2021, 12, 1))
    store = _complete_synthetic_store(
        tmp_path / "source",
        plan,
        non_finite_adjustment_at=poisoned_session,
    )
    try:
        builder = FactorResearchPanelBuilder(store=store, output_root=tmp_path / "derived")
        manifest = builder.build(
            plan,
            config=FullAPanelConfig(observation_frequency="month_end", horizons=(1,)),
        )
        assert manifest.coverage.bar_without_adj_factor_rows == 1

        dense = pq.read_table(builder.artifact_path(manifest, "dense_panel"))
        dense_rows = dense.to_pylist()
        poisoned = next(
            row
            for row in dense_rows
            if (row["instrument_id"], row["session_date"]) == poisoned_session
        )
        assert poisoned["adj_factor"] is None

        observation_keys = {
            (str(row["instrument_id"]), cast(datetime, row["event_time"]))
            for row in dense_rows
            if row["is_observation"]
        }
        managed_inputs = dense.select(
            (
                "instrument_id",
                "event_time",
                "open",
                "close",
                "adj_factor",
                "total_market_value_10k_cny",
            )
        ).rename_columns(
            [
                "instrument_id",
                "event_time",
                "open",
                "close",
                "adj_factor",
                "total_market_value",
            ]
        )
        expected: dict[tuple[str, datetime, str], dict[str, object]] = {}
        for registration in public_executable_factor_suite().registrations:
            for row in registration.factor.compute(
                cast(Any, SimpleNamespace(inputs=managed_inputs))
            ).to_pylist():
                observation_key = (
                    str(row["instrument_id"]),
                    cast(datetime, row["event_time"]),
                )
                if observation_key in observation_keys:
                    expected[(observation_key[0], observation_key[1], str(row["factor_id"]))] = row

        factor_path = builder.artifact_path(manifest, "factor_values")
        actual = {
            (
                str(row["instrument_id"]),
                cast(datetime, row["event_time"]),
                str(row["factor_id"]),
            ): row
            for row in pq.read_table(factor_path).to_pylist()
        }
        assert set(actual) == set(expected)
        for factor_key in sorted(actual):
            assert actual[factor_key]["is_valid"] is expected[factor_key]["is_valid"]
            if actual[factor_key]["is_valid"]:
                assert math.isfinite(float(cast(Any, actual[factor_key]["value"])))
                assert actual[factor_key]["value"] == pytest.approx(
                    expected[factor_key]["value"], abs=1e-12
                )
            else:
                assert actual[factor_key]["value"] == expected[factor_key]["value"] == 0.0

        november = datetime(2021, 11, 30, 7, tzinfo=UTC)
        # The dense-session delay is exact: a null T-5 invalidates Alpha014, while
        # an earlier null session is retained and must not compress the window.
        assert actual[("000001.SZ", november, "gtja191.alpha014")]["is_valid"] is False
        assert actual[("000999.SZ", november, "gtja191.alpha014")]["is_valid"] is True

        label_path = builder.artifact_path(manifest, "forward_returns")
        with builder.query() as connection:
            label = connection.execute(
                """
                SELECT forward_return, is_valid, invalid_reason
                FROM read_parquet(?)
                WHERE instrument_id = '000001.SZ'
                  AND CAST(event_time AS DATE) = DATE '2021-11-30'
                  AND horizon_sessions = 1
                """,
                [str(label_path)],
            ).fetchone()
            assert label == (0.0, False, "entry_price_unavailable")
            assert connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE is_valid AND NOT isfinite(value)",
                [str(factor_path)],
            ).fetchone() == (0,)
            assert connection.execute(
                """
                SELECT count(*) FROM read_parquet(?)
                WHERE is_valid AND NOT isfinite(forward_return)
                """,
                [str(label_path)],
            ).fetchone() == (0,)
        assert manifest.schema_id == "trademaster.full-a-panel-manifest/v2"
        assert manifest.builder_version == "full-a-panel/v2"
    finally:
        store.close()


def test_panel_builder_fails_closed_on_incomplete_or_corrupt_source(tmp_path: Path) -> None:
    plan = _synthetic_plan()
    incomplete = FullAResearchStore(tmp_path / "incomplete", clock=lambda: NOW)
    try:
        incomplete.register(plan)
        with pytest.raises(RuntimeError, match="not completed"):
            FactorResearchPanelBuilder(
                store=incomplete, output_root=tmp_path / "incomplete-derived"
            ).build(
                plan,
                config=FullAPanelConfig(observation_frequency="all_sessions", horizons=(1,)),
            )
    finally:
        incomplete.close()

    store = _complete_synthetic_store(tmp_path / "source", plan)
    try:
        builder = FactorResearchPanelBuilder(store=store, output_root=tmp_path / "derived")
        manifest = builder.build(
            plan,
            config=FullAPanelConfig(observation_frequency="all_sessions", horizons=(1,)),
        )
        source_ref = next(item for item in manifest.artifacts if item.name == "source_objects")
        assert source_ref.row_count == len(plan.tasks)

        source_path = tmp_path / "source" / "objects" / f"{plan.tasks[0].task_id}.parquet"
        source_path.write_bytes(b"corrupt")
        with pytest.raises(RuntimeError, match="content hash mismatch"):
            builder.build(
                plan,
                config=FullAPanelConfig(observation_frequency="all_sessions", horizons=(1,)),
            )
    finally:
        store.close()


def test_panel_config_has_canonical_twenty_year_research_defaults() -> None:
    config = FullAPanelConfig()

    assert config.observation_frequency == "month_end"
    assert config.horizons == (1, 5, 20, 60, 120, 252)
    assert len(config.config_sha256) == 64
    with pytest.raises(ValueError, match="canonical"):
        FullAPanelConfig(horizons=(5, 1))
