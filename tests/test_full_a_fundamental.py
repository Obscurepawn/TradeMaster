from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.factors import factor_output_schema
from trademaster.factors.fundamental import fundamental_factor_suite
from trademaster.research.full_a import (
    DownloadPlanManifest,
    DownloadTask,
    FullAResearchConfig,
    FullAResearchStore,
)
from trademaster.research.full_a_fundamental import (
    FullAFundamentalConfig,
    FullAFundamentalPanelBuilder,
    fundamental_input_schema,
)
from trademaster.research.full_a_panel import FactorResearchPanelBuilder, FullAPanelConfig

NOW = datetime(2026, 8, 23, tzinfo=UTC)
INSTRUMENTS = ("000001.SZ", "000002.SZ", "600001.SH")
SESSIONS = (date(2021, 1, 30), date(2021, 1, 31), date(2021, 2, 1), date(2021, 2, 28))


def _schema(fields: list[pa.Field[Any]]) -> pa.Schema:
    return pa.schema(fields)


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
    "fina_indicator": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("ann_date", pa.string()),
            pa.field("end_date", pa.string()),
            pa.field("update_flag", pa.string()),
            pa.field("roe", pa.float64()),
            pa.field("grossprofit_margin", pa.float64()),
            pa.field("ocf_to_or", pa.float64()),
            pa.field("q_sales_yoy", pa.float64()),
            pa.field("q_profit_yoy", pa.float64()),
            pa.field("debt_to_assets", pa.float64()),
        ]
    ),
    "income": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("ann_date", pa.string()),
            pa.field("f_ann_date", pa.string()),
            pa.field("end_date", pa.string()),
            pa.field("update_flag", pa.string()),
        ]
    ),
    "balancesheet": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("ann_date", pa.string()),
            pa.field("f_ann_date", pa.string()),
            pa.field("end_date", pa.string()),
            pa.field("update_flag", pa.string()),
        ]
    ),
    "cashflow": _schema(
        [
            pa.field("ts_code", pa.string()),
            pa.field("ann_date", pa.string()),
            pa.field("f_ann_date", pa.string()),
            pa.field("end_date", pa.string()),
            pa.field("update_flag", pa.string()),
        ]
    ),
    "index_member_all": _schema(
        [
            pa.field("l1_code", pa.string()),
            pa.field("l1_name", pa.string()),
            pa.field("l2_code", pa.string()),
            pa.field("l2_name", pa.string()),
            pa.field("l3_code", pa.string()),
            pa.field("l3_name", pa.string()),
            pa.field("ts_code", pa.string()),
            pa.field("name", pa.string()),
            pa.field("in_date", pa.string()),
            pa.field("out_date", pa.string()),
            pa.field("is_new", pa.string()),
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


def _task(
    endpoint: str,
    key: str,
    *,
    phase: Literal["bootstrap", "market", "status", "statement", "industry"],
    params: dict[str, str],
    allow_empty: bool = False,
) -> DownloadTask:
    return DownloadTask.build(
        task_key=key,
        phase=phase,
        endpoint=endpoint,
        params=params,
        fields=tuple(_SCHEMAS[endpoint].names),
        page_limit=6000,
        allow_empty=allow_empty,
    )


def _plan() -> DownloadPlanManifest:
    tasks: list[DownloadTask] = []
    for status in ("L", "D", "P", "G"):
        tasks.append(
            _task(
                "stock_basic",
                f"bootstrap.stock_basic.{status}",
                phase="bootstrap",
                params={"list_status": status},
                allow_empty=True,
            )
        )
    for session in SESSIONS:
        compact = session.strftime("%Y%m%d")
        for endpoint, phase in (
            ("daily", "market"),
            ("adj_factor", "market"),
            ("daily_basic", "market"),
            ("stk_limit", "status"),
            ("suspend_d", "status"),
            ("stock_st", "status"),
        ):
            tasks.append(
                _task(
                    endpoint,
                    f"{phase}.{endpoint}.{compact}",
                    phase=cast(Any, phase),
                    params={"trade_date": compact},
                    allow_empty=endpoint in {"suspend_d", "stock_st"},
                )
            )
    for instrument in INSTRUMENTS:
        for endpoint in ("fina_indicator", "income", "balancesheet", "cashflow"):
            tasks.append(
                _task(
                    endpoint,
                    f"statement.{endpoint}.{instrument}",
                    phase="statement",
                    params={
                        "ts_code": instrument,
                        "start_date": "20200101",
                        "end_date": "20210228",
                    },
                    allow_empty=True,
                )
            )
    for is_new in ("N", "Y"):
        tasks.append(
            _task(
                "index_member_all",
                f"industry.index_member_all.{is_new}",
                phase="industry",
                params={"is_new": is_new},
                allow_empty=True,
            )
        )
    config = FullAResearchConfig(
        start=SESSIONS[0],
        requested_end=SESSIONS[-1],
        statement_start=date(2020, 1, 1),
        benchmarks=("000300.SH",),
    )
    return DownloadPlanManifest.build(
        config=config,
        resolved_end=SESSIONS[-1],
        instruments=INSTRUMENTS,
        excluded_instruments=(),
        sessions=SESSIONS,
        tasks=tuple(tasks),
    )


def _rows(
    task: DownloadTask,
    *,
    future_revision_roe: float,
    include_industry: bool,
) -> list[dict[str, object]]:
    endpoint = task.endpoint
    params = dict(task.params)
    if endpoint == "stock_basic":
        if params["list_status"] != "L":
            return []
        return [
            {
                "ts_code": instrument,
                "exchange": "SSE" if instrument.endswith(".SH") else "SZSE",
                "curr_type": "CNY",
                "list_status": "L",
                "list_date": "20200101",
                "delist_date": None,
            }
            for instrument in INSTRUMENTS
        ]
    if endpoint == "index_member_all":
        if not include_industry or params["is_new"] != "Y":
            return []
        return [
            {
                "l1_code": "801010" if instrument != "600001.SH" else "801020",
                "l1_name": "行业一" if instrument != "600001.SH" else "行业二",
                "l2_code": None,
                "l2_name": None,
                "l3_code": None,
                "l3_name": None,
                "ts_code": instrument,
                "name": instrument,
                "in_date": "20200101",
                "out_date": None,
                "is_new": "Y",
            }
            for instrument in INSTRUMENTS
        ]
    if endpoint in {"income", "balancesheet", "cashflow"}:
        return []
    if endpoint == "fina_indicator":
        instrument = params["ts_code"]
        base = float(INSTRUMENTS.index(instrument) + 1)
        return [
            {
                "ts_code": instrument,
                # Date-only announcements become usable on the next eligible session.
                "ann_date": "20210131",
                "end_date": "20201231",
                "update_flag": "0",
                "roe": 10.0 * base,
                "grossprofit_margin": None if instrument == "600001.SH" else 20.0 * base,
                "ocf_to_or": 3.0 * base,
                "q_sales_yoy": 4.0 * base,
                "q_profit_yoy": None if instrument == "600001.SH" else 5.0 * base,
                "debt_to_assets": 50.0 - base,
            },
            {
                "ts_code": instrument,
                "ann_date": "20210215",
                "end_date": "20201231",
                "update_flag": "1",
                "roe": future_revision_roe + base,
                "grossprofit_margin": 21.0 * base,
                "ocf_to_or": 3.5 * base,
                "q_sales_yoy": 4.5 * base,
                "q_profit_yoy": 5.5 * base,
                "debt_to_assets": 49.0 - base,
            },
        ]

    compact = params["trade_date"]
    day_index = next(
        index for index, item in enumerate(SESSIONS) if item.strftime("%Y%m%d") == compact
    )
    if endpoint == "daily":
        return [
            {
                "ts_code": instrument,
                "trade_date": compact,
                "open": 10.0 + day_index,
                "high": 11.0 + day_index,
                "low": 9.0 + day_index,
                "close": 10.5 + day_index,
                "pre_close": 9.5 + day_index,
                "vol": 1000.0,
                "amount": 10_000.0,
            }
            for instrument in INSTRUMENTS
        ]
    if endpoint == "adj_factor":
        return [{"ts_code": item, "trade_date": compact, "adj_factor": 1.0} for item in INSTRUMENTS]
    if endpoint == "daily_basic":
        return [
            {
                "ts_code": instrument,
                "trade_date": compact,
                "turnover_rate": 1.0,
                "total_mv": 100_000.0,
                "circ_mv": 80_000.0,
                "pe_ttm": (-10.0 if instrument == "000002.SZ" else 10.0 + day_index),
                "pb": float(INSTRUMENTS.index(instrument) + 1),
                "ps_ttm": 2.0,
                "dv_ttm": float(INSTRUMENTS.index(instrument)),
            }
            for instrument in INSTRUMENTS
        ]
    if endpoint == "stk_limit":
        return [
            {
                "ts_code": item,
                "trade_date": compact,
                "up_limit": 100.0,
                "down_limit": 1.0,
            }
            for item in INSTRUMENTS
        ]
    return []


def _completed_run(
    root: Path,
    *,
    future_revision_roe: float,
    include_industry: bool = False,
) -> tuple[DownloadPlanManifest, FullAResearchStore, FactorResearchPanelBuilder, str]:
    plan = _plan()
    store = FullAResearchStore(root / "source", clock=lambda: NOW)
    store.register(plan)
    objects = store.root / "objects"
    objects.mkdir()
    for task in plan.tasks:
        table = pa.Table.from_pylist(
            _rows(
                task,
                future_revision_roe=future_revision_roe,
                include_industry=include_industry,
            ),
            schema=_SCHEMAS[task.endpoint],
        ).replace_schema_metadata(_request_metadata(task))
        path = objects / f"{task.task_id}.parquet"
        pq.write_table(table, path)
        store.mark_running(plan.plan_sha256, task)
        store.mark_completed(
            plan.plan_sha256,
            task,
            request_sha256=task.request_sha256,
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            row_count=table.num_rows,
            evidence_path=path,
        )
    panel_builder = FactorResearchPanelBuilder(
        store=store,
        output_root=root / "market-panel",
    )
    panel = panel_builder.build(
        plan,
        config=FullAPanelConfig(observation_frequency="month_end", horizons=(1,)),
    )
    return plan, store, panel_builder, panel.manifest_sha256


def _factor_rows(
    builder: FullAFundamentalPanelBuilder, manifest_sha: str
) -> list[dict[str, object]]:
    manifest = builder.load_manifest(manifest_sha)
    return pq.read_table(builder.artifact_path(manifest, "factor_values")).to_pylist()


def test_fundamental_panel_is_pit_safe_and_matches_managed_factor_semantics(
    tmp_path: Path,
) -> None:
    plan, store, panel_builder, panel_sha = _completed_run(
        tmp_path / "first", future_revision_roe=90.0
    )
    try:
        builder = FullAFundamentalPanelBuilder(
            store=store,
            panel_builder=panel_builder,
            output_root=tmp_path / "first" / "fundamental-panel",
        )
        manifest = builder.build(plan, panel_sha, config=FullAFundamentalConfig())
        assert manifest.panel_manifest_sha256 == panel_sha
        assert len(manifest.builder_code_sha256) == 64
        assert len(manifest.factor_definition_bindings) == 11
        assert manifest.coverage.observation_rows == 6
        assert manifest.coverage.factor_rows == 60
        assert manifest.coverage.valid_atomic_rows < 54
        assert {item.name for item in manifest.artifacts} == {
            "factor_values",
            "fundamental_inputs",
            "source_objects",
        }
        assert manifest.output("fundamental.composite.global", "1").status == "materialized"
        industry = manifest.output("fundamental.composite.industry_relative", "1")
        assert industry.status == "blocked"
        assert industry.blocker_code == "historical_industry_membership_interval_gap"

        input_path = builder.artifact_path(manifest, "fundamental_inputs")
        assert pq.read_schema(input_path).remove_metadata() == fundamental_input_schema()
        with builder.query() as connection:
            revision_rows = connection.execute(
                """
                SELECT CAST(event_time AS DATE), financial_ann_date,
                       financial_known_at_session, financial_update_flag, roe
                FROM read_parquet(?) WHERE instrument_id = '000001.SZ'
                ORDER BY event_time
                """,
                [str(input_path)],
            ).fetchall()
            assert revision_rows == [
                (date(2021, 1, 31), None, None, None, None),
                (date(2021, 2, 28), date(2021, 1, 31), date(2021, 2, 1), "0", 10.0),
            ]

            factor_path = builder.artifact_path(manifest, "factor_values")
            negative_pe = connection.execute(
                """
                SELECT value, is_valid FROM read_parquet(?)
                WHERE instrument_id = '000002.SZ'
                  AND CAST(event_time AS DATE) = DATE '2021-01-31'
                  AND factor_id = 'fundamental.earnings_yield'
                """,
                [str(factor_path)],
            ).fetchone()
            assert negative_pe == (0.0, False)
            missing_profit = connection.execute(
                """
                SELECT value, is_valid FROM read_parquet(?)
                WHERE instrument_id = '600001.SH'
                  AND CAST(event_time AS DATE) = DATE '2021-01-31'
                  AND factor_id = 'fundamental.profit_growth'
                """,
                [str(factor_path)],
            ).fetchone()
            assert missing_profit == (0.0, False)

        assert manifest.schema_id == "trademaster.full-a-fundamental-manifest/v2"
        assert manifest.builder_version == "full-a-fundamental/v2"
        assert manifest.config.announcement_availability_policy == (
            "date_only_next_eligible_session"
        )
        assert manifest.config.revision_availability_policy == (
            "original_only_without_revision_known_at"
        )

        all_rows = _factor_rows(builder, manifest.manifest_sha256)
        atomic_ids = {item.definition.factor_id for item in fundamental_factor_suite().atomic}
        atomic = pa.Table.from_pylist(
            [row for row in all_rows if row["factor_id"] in atomic_ids],
            schema=factor_output_schema(),
        )
        expected = (
            fundamental_factor_suite()
            .global_composite.factor.compute(cast(Any, SimpleNamespace(inputs=atomic)))
            .to_pylist()
        )
        actual = [row for row in all_rows if row["factor_id"] == "fundamental.composite.global"]
        assert [{key: value for key, value in row.items() if key != "value"} for row in actual] == [
            {key: value for key, value in row.items() if key != "value"} for row in expected
        ]
        assert [row["value"] for row in actual] == pytest.approx(
            [row["value"] for row in expected], abs=1e-12
        )
        assert builder.build(plan, panel_sha) == manifest
    finally:
        store.close()


def test_revision_without_known_at_never_changes_historical_exposure(
    tmp_path: Path,
) -> None:
    historical: list[list[dict[str, object]]] = []
    for name, future_roe in (("low", 90.0), ("high", 9000.0)):
        plan, store, panel_builder, panel_sha = _completed_run(
            tmp_path / name, future_revision_roe=future_roe
        )
        try:
            builder = FullAFundamentalPanelBuilder(
                store=store,
                panel_builder=panel_builder,
                output_root=tmp_path / name / "fundamental-panel",
            )
            manifest = builder.build(plan, panel_sha)
            historical.append(_factor_rows(builder, manifest.manifest_sha256))
        finally:
            store.close()
    assert [
        {key: value for key, value in row.items() if key != "value"} for row in historical[0]
    ] == [{key: value for key, value in row.items() if key != "value"} for row in historical[1]]
    assert [row["value"] for row in historical[0]] == pytest.approx(
        [row["value"] for row in historical[1]], abs=1e-12
    )


def test_industry_composite_materializes_only_with_complete_interval_coverage(
    tmp_path: Path,
) -> None:
    plan, store, panel_builder, panel_sha = _completed_run(
        tmp_path,
        future_revision_roe=90.0,
        include_industry=True,
    )
    try:
        builder = FullAFundamentalPanelBuilder(
            store=store,
            panel_builder=panel_builder,
            output_root=tmp_path / "fundamental-panel",
        )
        manifest = builder.build(plan, panel_sha)
        assert manifest.output("fundamental.composite.industry_relative", "1").status == (
            "materialized"
        )
        assert manifest.coverage.industry_composite_rows == 6
        assert manifest.coverage.factor_rows == 66
    finally:
        store.close()


def test_fundamental_panel_fails_closed_on_incomplete_statement_evidence(
    tmp_path: Path,
) -> None:
    plan = _plan()
    store = FullAResearchStore(tmp_path / "source", clock=lambda: NOW)
    store.register(plan)
    try:
        # A panel manifest SHA is intentionally irrelevant: source completeness is checked first.
        with pytest.raises(RuntimeError, match="fundamental source task is not completed"):
            FullAFundamentalPanelBuilder(
                store=store,
                panel_builder=cast(Any, SimpleNamespace()),
                output_root=tmp_path / "derived",
            ).build(plan, "0" * 64)
    finally:
        store.close()
