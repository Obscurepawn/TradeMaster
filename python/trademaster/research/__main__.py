"""CLI for resumable twenty-year full-A research data and factor evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from .full_a import (
    DownloadPlanManifest,
    FullAResearchConfig,
    FullAResearchDownloader,
    FullAResearchPlanner,
    FullAResearchStore,
)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _managed_factor_directions(
    bindings: tuple[tuple[str, str, str], ...],
    registrations: Sequence[Any],
) -> tuple[tuple[str, str, Literal[-1, 0, 1]], ...]:
    definitions = {
        cast(tuple[str, str], item.definition.identity): item.definition for item in registrations
    }
    directions: list[tuple[str, str, Literal[-1, 0, 1]]] = []
    for factor_id, version, definition_sha256 in bindings:
        definition = definitions.get((factor_id, version))
        if definition is None or str(definition.definition_sha256) != definition_sha256:
            raise ValueError(
                f"factor definition binding differs from managed registry: {factor_id}@{version}"
            )
        directions.append(
            (
                factor_id,
                version,
                cast(Literal[-1, 0, 1], definition.direction),
            )
        )
    return tuple(sorted(directions))


def _minimum_spacing_from_dense_panel(path: Path) -> int:
    import duckdb

    if not path.is_file():
        raise ValueError("dense panel is missing")
    with duckdb.connect() as connection:
        row = connection.execute(
            """
            WITH observations AS (
                SELECT DISTINCT session_index
                FROM read_parquet(?) WHERE is_observation
            ), gaps AS (
                SELECT session_index - lag(session_index) OVER (ORDER BY session_index) AS gap
                FROM observations
            )
            SELECT min(gap) FROM gaps WHERE gap IS NOT NULL
            """,
            [str(path)],
        ).fetchone()
    if row is None or row[0] is None or int(row[0]) < 1:
        raise ValueError("dense panel has fewer than two observation sessions")
    return int(row[0])


def _register_reconciled_plan(
    store: FullAResearchStore,
    source: DownloadPlanManifest,
    target: DownloadPlanManifest,
) -> int:
    store.register(target)
    if source.plan_sha256 == target.plan_sha256:
        return 0
    return store.inherit_completed(source, target)


def _compact_date(value: object) -> date:
    raw = str(value)
    if len(raw) != 8 or not raw.isdigit():
        raise ValueError(f"provider date is not YYYYMMDD: {raw}")
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))


def _config(args: argparse.Namespace) -> FullAResearchConfig:
    return FullAResearchConfig(
        start=args.start,
        requested_end=args.end,
        statement_start=args.statement_start,
        benchmarks=tuple(sorted(set(args.benchmark))),
    )


def _source(args: argparse.Namespace) -> tuple[Any, Any]:
    from trademaster.strategies.industry_fundamental_top5.data import StrategyDataCache

    from .tushare import RateLimitedTushareClient

    client = RateLimitedTushareClient.from_environment(
        token_env=args.token_env,
        minimum_interval_seconds=args.minimum_interval,
        max_attempts=args.max_attempts,
    )
    cache = StrategyDataCache(
        root=args.data_root,
        client=client,
        clock=lambda: datetime.now(UTC),
    )
    return cache, client


def main() -> None:
    parser = argparse.ArgumentParser(description="TradeMaster twenty-year full-A factor research")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--token-env", default="TUSHARE_TOKEN")
    parser.add_argument("--minimum-interval", type=float, default=0.13)
    parser.add_argument("--max-attempts", type=int, default=4)
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser(
        "plan", help="cache bootstrap data and persist an immutable download plan"
    )
    plan_parser.add_argument("--start", type=date.fromisoformat, default=date(2006, 8, 23))
    plan_parser.add_argument("--end", type=date.fromisoformat, default=datetime.now(UTC).date())
    plan_parser.add_argument("--statement-start", type=date.fromisoformat, default=date(2005, 1, 1))
    plan_parser.add_argument(
        "--benchmark",
        action="append",
        default=[
            "000001.SH",
            "000016.SH",
            "000300.SH",
            "000688.SH",
            "000985.CSI",
            "399001.SZ",
            "399006.SZ",
            "899050.BJ",
        ],
    )

    download = commands.add_parser("download", help="resume pending download tasks")
    download.add_argument("--plan-sha256", required=True)
    download.add_argument("--max-tasks", type=int)
    download.add_argument(
        "--phase",
        action="append",
        choices=("bootstrap", "market", "status", "statement", "industry", "benchmark"),
        default=[],
    )

    status = commands.add_parser("status", help="show durable task progress")
    status.add_argument("--plan-sha256", required=True)
    reconcile = commands.add_parser(
        "reconcile-universe",
        help="add market identities missing from current stock_basic without provider access",
    )
    reconcile.add_argument("--plan-sha256", required=True)
    verify = commands.add_parser("verify", help="rehash every completed object")
    verify.add_argument("--plan-sha256", required=True)
    materialize = commands.add_parser(
        "materialize", help="build dense PIT market, factor and label Parquets"
    )
    materialize.add_argument("--plan-sha256", required=True)
    materialize.add_argument("--output-root", type=Path, required=True)
    materialize.add_argument(
        "--observation-frequency",
        choices=("all_sessions", "month_end"),
        default="month_end",
    )
    materialize.add_argument("--horizons", default="1,5,20,60,120,252")
    fundamental_parser = commands.add_parser(
        "materialize-fundamental",
        help="build PIT fundamental atomic and composite factor Parquets",
    )
    fundamental_parser.add_argument("--plan-sha256", required=True)
    fundamental_parser.add_argument("--panel-output-root", type=Path, required=True)
    fundamental_parser.add_argument("--panel-manifest-sha256", required=True)
    fundamental_parser.add_argument("--output-root", type=Path, required=True)
    evaluate = commands.add_parser(
        "evaluate", help="run out-of-core diagnostics and persist an offline report"
    )
    evaluate.add_argument("--plan-sha256", required=True)
    evaluate.add_argument("--panel-output-root", type=Path, required=True)
    evaluate.add_argument("--panel-manifest-sha256", required=True)
    evaluate.add_argument(
        "--factor-manifest-kind",
        choices=("public", "fundamental"),
        default="public",
    )
    evaluate.add_argument("--factor-output-root", type=Path)
    evaluate.add_argument("--factor-manifest-sha256")
    evaluate.add_argument("--report-root", type=Path, required=True)
    evaluate.add_argument("--horizons", default="1,5,20,60,120,252")
    evaluate.add_argument("--quantiles", type=int, default=5)
    evaluate.add_argument("--minimum-observations", type=int, default=100)
    evaluate.add_argument("--newey-west-lag", type=int, default=5)
    evaluate.add_argument("--annual-observations", type=int, default=12)
    evaluate.add_argument("--minimum-observation-spacing-sessions", type=int)
    evaluate.add_argument("--capacity-participation-rate", type=float, default=0.01)
    evaluate.add_argument("--memory-limit", default="8GB")
    verify_report = commands.add_parser(
        "verify-report",
        help="rehash and validate one immutable factor report bundle",
    )
    verify_report.add_argument("--report-root", type=Path, required=True)
    verify_report.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "plan":
        planner = FullAResearchPlanner()
        config = _config(args)
        cache, client = _source(args)
        try:
            tables = {
                task.task_key: cache.query_with_evidence(
                    task.endpoint,
                    params=dict(task.params),
                    fields=task.fields,
                    page_limit=task.page_limit,
                ).table
                for task in planner.bootstrap_tasks(config)
            }
            plan = planner.build_from_bootstrap(config, tables)
            with FullAResearchStore(args.data_root, clock=lambda: datetime.now(UTC)) as store:
                store.register(plan)
                result = store.status(plan.plan_sha256)
            _print(
                {
                    "schema_id": "trademaster.full-a-plan-result/v1",
                    "plan_sha256": plan.plan_sha256,
                    "start": plan.config.start.isoformat(),
                    "resolved_end": plan.resolved_end.isoformat(),
                    "instrument_count": len(plan.instruments),
                    "session_count": len(plan.sessions),
                    "task_count": len(plan.tasks),
                    "provider_calls": len(client.calls),
                    "status": result.model_dump(mode="json"),
                }
            )
        finally:
            cache.close()
        return

    if args.command == "verify-report":
        from .factor_report import FullAFactorReportStore

        report_manifest = FullAFactorReportStore(args.report_root).verify(args.manifest)
        _print(
            {
                "schema_id": "trademaster.full-a-report-verify-result/v1",
                "report_id": report_manifest.report_id,
                "manifest": str(args.manifest.resolve()),
                "input_manifests": list(report_manifest.identity.evaluation_input_manifest_sha256s),
                "object_count": len(report_manifest.objects),
            }
        )
        return

    with FullAResearchStore(args.data_root, clock=lambda: datetime.now(UTC)) as store:
        if args.command == "status":
            _print(store.status(args.plan_sha256).model_dump(mode="json"))
            return
        plan = store.load_plan(args.plan_sha256)
        if args.command == "verify":
            _print(store.verify(plan).model_dump(mode="json"))
            return
        if args.command == "reconcile-universe":
            import duckdb

            from trademaster.strategies.industry_fundamental_top5.data import StrategyDataCache

            from .tushare import CacheOnlyTushareClient

            planner = FullAResearchPlanner()
            cache_only = CacheOnlyTushareClient()
            cache = StrategyDataCache(
                root=args.data_root,
                client=cache_only,
                clock=lambda: datetime.now(UTC),
            )
            try:
                bootstrap = {
                    task.task_key: cache.query_with_evidence(
                        task.endpoint,
                        params=dict(task.params),
                        fields=task.fields,
                        page_limit=task.page_limit,
                    ).table
                    for task in planner.bootstrap_tasks(plan.config)
                }
            finally:
                cache.close()
            daily_evidence = store.completed_evidence_snapshot(
                plan,
                endpoints=("daily",),
            )
            expected_daily_tasks = sum(item.endpoint == "daily" for item in plan.tasks)
            if len(daily_evidence.evidence) != expected_daily_tasks:
                raise RuntimeError("universe reconciliation requires every planned daily task")
            daily_paths = [
                str((store.root / item.evidence_uri).resolve()) for item in daily_evidence.evidence
            ]
            with duckdb.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT CAST(ts_code AS VARCHAR), min(CAST(trade_date AS VARCHAR)),
                           max(CAST(trade_date AS VARCHAR))
                    FROM read_parquet(?, union_by_name=true)
                    GROUP BY ts_code ORDER BY ts_code
                    """,
                    [daily_paths],
                ).fetchall()
            lifecycles = {
                str(instrument_id): (
                    _compact_date(first_date),
                    _compact_date(last_date),
                )
                for instrument_id, first_date, last_date in rows
            }
            reconciled_plan = planner.build_from_bootstrap(
                plan.config,
                bootstrap,
                market_lifecycles=lifecycles,
            )
            inherited = _register_reconciled_plan(store, plan, reconciled_plan)
            _print(
                {
                    "schema_id": "trademaster.full-a-universe-reconcile/v1",
                    "source_plan_sha256": plan.plan_sha256,
                    "plan_sha256": reconciled_plan.plan_sha256,
                    "source_instrument_count": len(plan.instruments),
                    "instrument_count": len(reconciled_plan.instruments),
                    "inherited_completed_tasks": inherited,
                    "provider_calls": len(cache_only.calls),
                    "status": store.status(reconciled_plan.plan_sha256).model_dump(mode="json"),
                }
            )
            return
        if args.command == "materialize":
            from .full_a_panel import FactorResearchPanelBuilder, FullAPanelConfig

            horizons = tuple(sorted({int(value) for value in args.horizons.split(",") if value}))
            builder = FactorResearchPanelBuilder(
                store=store,
                output_root=args.output_root,
            )
            manifest = builder.build(
                plan,
                config=FullAPanelConfig(
                    observation_frequency=args.observation_frequency,
                    horizons=horizons,
                ),
            )
            _print(
                {
                    "schema_id": "trademaster.full-a-materialize-result/v1",
                    "manifest_sha256": manifest.manifest_sha256,
                    "materialization_sha256": manifest.materialization_sha256,
                    "coverage": manifest.coverage.model_dump(mode="json"),
                    "artifacts": [item.model_dump(mode="json") for item in manifest.artifacts],
                }
            )
            return
        if args.command == "materialize-fundamental":
            from .full_a_fundamental import (
                FullAFundamentalConfig,
                FullAFundamentalPanelBuilder,
            )
            from .full_a_panel import FactorResearchPanelBuilder

            panel_builder = FactorResearchPanelBuilder(
                store=store,
                output_root=args.panel_output_root,
            )
            panel = panel_builder.load_manifest(args.panel_manifest_sha256)
            if panel.plan_sha256 != plan.plan_sha256:
                raise ValueError("panel and requested download plan differ")
            fundamental_builder = FullAFundamentalPanelBuilder(
                store=store,
                panel_builder=panel_builder,
                output_root=args.output_root,
            )
            fundamental_manifest = fundamental_builder.build(
                plan,
                panel.manifest_sha256,
                config=FullAFundamentalConfig(),
            )
            _print(
                {
                    "schema_id": "trademaster.full-a-fundamental-result/v1",
                    "manifest_sha256": fundamental_manifest.manifest_sha256,
                    "materialization_sha256": fundamental_manifest.materialization_sha256,
                    "coverage": fundamental_manifest.coverage.model_dump(mode="json"),
                    "outputs": [
                        item.model_dump(mode="json") for item in fundamental_manifest.outputs
                    ],
                    "artifacts": [
                        item.model_dump(mode="json") for item in fundamental_manifest.artifacts
                    ],
                }
            )
            return
        if args.command == "evaluate":
            from trademaster.factors.public_factors import public_executable_factor_suite

            from .factor_evaluation import ForwardReturnPolicy, FullAFactorEvaluationConfig
            from .factor_evaluation_duckdb import DuckDBFullAFactorEvaluator
            from .factor_report import FullAFactorReportIdentity, FullAFactorReportStore
            from .full_a_panel import FactorResearchPanelBuilder

            panel_builder = FactorResearchPanelBuilder(
                store=store,
                output_root=args.panel_output_root,
            )
            panel = panel_builder.load_manifest(args.panel_manifest_sha256)
            if panel.plan_sha256 != plan.plan_sha256:
                raise ValueError("panel and requested download plan differ")
            factor_values_path = panel_builder.artifact_path(panel, "factor_values")
            input_manifest_sha256s: tuple[str, ...] = (panel.manifest_sha256,)
            data_snapshot_sha256 = panel.source_snapshot_sha256
            factor_definition_sha256s = tuple(
                sorted(item[2] for item in panel.factor_definition_bindings)
            )
            factor_directions = _managed_factor_directions(
                panel.factor_definition_bindings,
                public_executable_factor_suite().registrations,
            )
            research_run_prefix = "full-a-20y-public"
            input_limitations: tuple[str, ...] = ()
            if args.factor_manifest_kind == "fundamental":
                if args.factor_output_root is None or args.factor_manifest_sha256 is None:
                    raise ValueError(
                        "fundamental evaluation requires factor output root and manifest SHA"
                    )
                from trademaster.factors.fundamental import fundamental_factor_suite

                from .full_a_fundamental import FullAFundamentalPanelBuilder

                fundamental_builder = FullAFundamentalPanelBuilder(
                    store=store,
                    panel_builder=panel_builder,
                    output_root=args.factor_output_root,
                )
                fundamental_manifest = fundamental_builder.load_manifest(
                    args.factor_manifest_sha256,
                )
                if (
                    fundamental_manifest.plan_sha256 != plan.plan_sha256
                    or fundamental_manifest.panel_manifest_sha256 != panel.manifest_sha256
                ):
                    raise ValueError("fundamental factors and market panel differ")
                factor_values_path = fundamental_builder.artifact_path(
                    fundamental_manifest,
                    "factor_values",
                )
                input_manifest_sha256s = tuple(
                    sorted((panel.manifest_sha256, fundamental_manifest.manifest_sha256))
                )
                data_snapshot_sha256 = _sha256_json(
                    {
                        "market": panel.source_snapshot_sha256,
                        "fundamental": fundamental_manifest.source_snapshot_sha256,
                    }
                )
                materialized = {
                    item.identity
                    for item in fundamental_manifest.outputs
                    if item.status == "materialized"
                }
                materialized_bindings = tuple(
                    item
                    for item in fundamental_manifest.factor_definition_bindings
                    if item[:2] in materialized
                )
                factor_definition_sha256s = tuple(sorted(item[2] for item in materialized_bindings))
                factor_directions = _managed_factor_directions(
                    materialized_bindings,
                    fundamental_factor_suite().registrations,
                )
                research_run_prefix = "full-a-20y-fundamental"
                input_limitations = tuple(
                    sorted(
                        f"{item.factor_id}@{item.factor_version} blocked: "
                        f"{item.blocker_code} - {item.blocker_detail}"
                        for item in fundamental_manifest.outputs
                        if item.status == "blocked"
                    )
                )
            horizons = tuple(sorted({int(value) for value in args.horizons.split(",") if value}))
            minimum_observation_spacing_sessions = (
                args.minimum_observation_spacing_sessions
                if args.minimum_observation_spacing_sessions is not None
                else _minimum_spacing_from_dense_panel(
                    panel_builder.artifact_path(panel, "dense_panel")
                )
            )
            evaluation_config = FullAFactorEvaluationConfig(
                horizons=horizons,
                quantiles=args.quantiles,
                minimum_observations=args.minimum_observations,
                newey_west_lag=args.newey_west_lag,
                annual_observations=args.annual_observations,
                minimum_observation_spacing_sessions=(minimum_observation_spacing_sessions),
                capacity_participation_rate=args.capacity_participation_rate,
                factor_directions=factor_directions,
            )
            evaluation_result = DuckDBFullAFactorEvaluator(
                temp_directory=args.report_root / ".duckdb-tmp",
                memory_limit=args.memory_limit,
            ).evaluate(
                factor_values_path=factor_values_path,
                forward_returns_path=panel_builder.artifact_path(panel, "forward_returns"),
                universe_path=panel_builder.artifact_path(panel, "universe"),
                config=evaluation_config,
            )
            label_policy = ForwardReturnPolicy(
                horizons=horizons,
                alignment="next_session_close",
                price_adjustment="hfq_raw_times_adj_factor",
                require_tradable_entry=panel.config.require_tradable_entry,
            )
            identity = FullAFactorReportIdentity(
                research_run_id=(f"{research_run_prefix}-{panel.materialization_sha256[:16]}"),
                data_snapshot_sha256=data_snapshot_sha256,
                evaluation_input_manifest_sha256s=input_manifest_sha256s,
                universe_policy_sha256=panel.config.config_sha256,
                label_policy_sha256=label_policy.policy_sha256,
                evaluation_config_sha256=_sha256_json(evaluation_config.model_dump(mode="json")),
                factor_definition_sha256s=factor_definition_sha256s,
            )
            report_store = FullAFactorReportStore(args.report_root)
            artifact = report_store.persist(
                evaluation_result,
                identity=identity,
                limitations=(
                    *input_limitations,
                    "BSE交易日历使用SSE session的版本化派生映射。",
                    "HFQ只用于因子研究；执行价格仍应使用原始行情。",
                    "SW2021历史行业PIT覆盖不足时行业内RankIC显示N/A。",
                    "容量仅为成交额参与率proxy，未包含冲击、费用和涨跌停排队。",
                    (
                        "观察session最小间隔为"
                        f"{minimum_observation_spacing_sessions}；超过该间隔的forward "
                        "spread不做独立复利、Sharpe或回撤。"
                    ),
                    (
                        "ST、上市板块、上市年龄和涨跌停状态分组尚未进入evaluation input "
                        "schema，诊断状态为blocked_schema_extension_required。"
                    ),
                    (
                        "forward return是next-session-close研究标签，不是Rust交易规则回放；"
                        "涨停买入、跌停退出延迟及退市处置需在策略E2E中验证。"
                    ),
                    f"观察频率为{panel.config.observation_frequency}。",
                    (
                        "稠密面板无法由停牌证据解释的缺bar行数为"
                        f"{panel.coverage.unexplained_missing_bar_rows}。"
                    ),
                    (
                        "有bar但缺同日adj_factor的行数为"
                        f"{panel.coverage.bar_without_adj_factor_rows}。"
                    ),
                    (
                        "有bar但缺同日daily_basic的行数为"
                        f"{panel.coverage.bar_without_daily_basic_rows}。"
                    ),
                ),
            )
            objects = {item.name: item for item in artifact.manifest.objects}
            _print(
                {
                    "schema_id": "trademaster.full-a-evaluate-result/v1",
                    "report_id": artifact.report_id,
                    "manifest": str(artifact.manifest_path),
                    "html": str(args.report_root / objects["report_html"].uri),
                    "markdown": str(args.report_root / objects["report_markdown"].uri),
                    "summary": str(args.report_root / objects["summary"].uri),
                    "factor_count": len(evaluation_result.summaries),
                }
            )
            return

    cache, client = _source(args)
    try:
        with FullAResearchStore(args.data_root, clock=lambda: datetime.now(UTC)) as store:

            def progress(current: object, task: object) -> None:
                from .full_a import DownloadRunStatus, DownloadTask

                if isinstance(current, DownloadRunStatus) and isinstance(task, DownloadTask):
                    print(
                        json.dumps(
                            {
                                "completed": current.completed,
                                "pending": current.pending,
                                "task": task.task_key,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

            download_result = FullAResearchDownloader(source=cache, store=store).run(
                plan,
                max_tasks=args.max_tasks,
                phases=tuple(args.phase),
                progress=progress,
            )
        _print(
            {
                "schema_id": "trademaster.full-a-download-result/v1",
                "provider_calls": len(client.calls),
                "status": download_result.model_dump(mode="json"),
            }
        )
    finally:
        cache.close()


if __name__ == "__main__":
    main()
