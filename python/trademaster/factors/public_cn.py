"""Built-in, source-grounded catalog of public A-share factor candidates."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from .public_library import (
    DataAvailability,
    FactorCandidateRecord,
    FactorCollectionDefinition,
    FactorSourceRecord,
    ImplementationStatus,
    PublicFactorLibrary,
)

_REVISION = "2026.08.23"


def _strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _source(**values: object) -> FactorSourceRecord:
    values["notes"] = _strings(values.get("notes", ()))  # type: ignore[arg-type]
    return FactorSourceRecord.build(**values)


def _candidate(
    candidate_id: str,
    *,
    collection_id: str,
    source_ids: tuple[str, ...],
    display_name: str,
    category: str,
    formula_reference: str,
    formula_expression: str | None,
    required_inputs: tuple[str, ...],
    data_availability: DataAvailability,
    implementation_status: ImplementationStatus,
    aliases: tuple[str, ...] = (),
    required_operators: tuple[str, ...] = (),
    implementation_identity: str | None = None,
    semantic_differences: tuple[str, ...] = (),
    blocked_reasons: tuple[str, ...] = (),
    expected_direction: int = 0,
    distribution_status: str = "metadata-only",
) -> FactorCandidateRecord:
    return FactorCandidateRecord.build(
        candidate_id=candidate_id,
        revision=_REVISION,
        collection_id=collection_id,
        source_ids=_strings(source_ids),
        display_name=display_name,
        aliases=_strings(aliases),
        category=category,
        formula_reference=formula_reference,
        formula_expression=formula_expression,
        required_inputs=_strings(required_inputs),
        required_operators=_strings(required_operators),
        data_availability=data_availability,
        implementation_status=implementation_status,
        implementation_identity=implementation_identity,
        semantic_differences=_strings(semantic_differences),
        blocked_reasons=_strings(blocked_reasons),
        expected_direction=expected_direction,
        distribution_status=distribution_status,
    )


def _collection(
    collection_id: str,
    *,
    display_name: str,
    collection_type: str,
    source_ids: tuple[str, ...],
    candidates: tuple[FactorCandidateRecord, ...],
    notes: tuple[str, ...],
    reported_count: int | None = None,
) -> FactorCollectionDefinition:
    return FactorCollectionDefinition.build(
        collection_id=collection_id,
        display_name=display_name,
        collection_type=collection_type,
        source_ids=_strings(source_ids),
        member_ids=tuple(item.candidate_id for item in candidates),
        expected_count=len(candidates),
        reported_count=reported_count,
        notes=_strings(notes),
    )


def _sources() -> tuple[FactorSourceRecord, ...]:
    return (
        _source(
            source_id="huatai-53-2020.06.02",
            publisher="华泰证券",
            title="行业内因子选股实证分析",
            released_on="2020-06-02",
            reference_url=(
                "https://crm.htsc.com.cn/doc/2020/10750101/8426eb61-a15e-4a02-b613-d2e8aa110415.pdf"
            ),
            artifact_sha256="cf99f5c7dc8a9029ce4bcb07143fb5a71b1c34167f05617a3e2ac930783a3ffd",
            license_class="paper-disclosure",
            redistribution="metadata-only",
            notes=("Independent reimplementation only", "Public reading is not an OSS license"),
        ),
        _source(
            source_id="huatai-moneyflow-2018.05.17",
            publisher="华泰证券",
            title="华泰单因子测试之资金流向因子",
            released_on="2018-05-17",
            reference_url=(
                "https://crm.htsc.com.cn/doc/2018/10750101/71376742-b7ed-4bb0-a7af-8dcaeaa6d310.pdf"
            ),
            artifact_sha256=None,
            license_class="paper-disclosure",
            redistribution="metadata-only",
            notes=("Underlying Wind order-flow fields are not reconstructible from daily bars",),
        ),
        _source(
            source_id="huatai-quality-2018.05.25",
            publisher="华泰证券",
            title="华泰单因子测试之财务质量因子",
            released_on="2018-05-25",
            reference_url=(
                "https://crm.htsc.com.cn/doc/2018/10750101/1ba488e8-20d3-47c1-96e2-a5129032d378.pdf"
            ),
            artifact_sha256=None,
            license_class="paper-disclosure",
            redistribution="metadata-only",
            notes=("Quarter, YTD and TTM definitions require separate PIT contracts",),
        ),
        _source(
            source_id="huatai-consensus-2018.12.14",
            publisher="华泰证券",
            title="华泰单因子测试之一致预期因子",
            released_on="2018-12-14",
            reference_url=(
                "https://crm.htsc.com.cn/doc/2018/10750101/285844b6-90d7-49b7-98ae-8950848540ed.pdf"
            ),
            artifact_sha256=None,
            license_class="paper-disclosure",
            redistribution="metadata-only",
            notes=("Requires historical point-in-time analyst-consensus snapshots",),
        ),
        _source(
            source_id="huatai-risk-model-2019.06.12",
            publisher="华泰证券",
            title="桑土之防：结构化多因子风险模型",
            released_on="2019-06-12",
            reference_url=(
                "https://crm.htsc.com.cn/doc/2019/10750101/a3d3cf93-7eff-4066-9a43-64f7881ce93b.pdf"
            ),
            artifact_sha256=None,
            license_class="paper-disclosure",
            redistribution="metadata-only",
            notes=("Risk descriptors are not promoted to predictive alpha definitions",),
        ),
        _source(
            source_id="huatai-historical-2019.10.15",
            publisher="华泰证券",
            title="华泰单因子测试之历史分位数因子",
            released_on="2019-10-15",
            reference_url=(
                "https://crm.htsc.com.cn/doc/2019/10750101/19fbd14f-887e-46ac-95b3-404582bed81f.pdf"
            ),
            artifact_sha256=None,
            license_class="paper-disclosure",
            redistribution="metadata-only",
            notes=("Report says 89 while the visible parameter grid enumerates 86",),
        ),
        _source(
            source_id="gtja-alpha191-2017.06.15",
            publisher="国泰君安证券",
            title="基于短周期价量特征的多因子选股体系",
            released_on="2017-06-15",
            reference_url="https://guorn.com/static/upload/file/3/134065454575605.pdf",
            artifact_sha256="863f62c2e23bd87ddb42b8338c8fe2b0276d94260ac985e1a0edfff318693c6c",
            license_class="paper-disclosure",
            redistribution="metadata-only",
            notes=("Original formulas require an independent semantic and correction review",),
        ),
        _source(
            source_id="dolphindb-gtja191-43ace2c",
            publisher="DolphinDB",
            title="DolphinDBModules gtja191Alpha reference implementation",
            released_on="2025-12-30",
            reference_url=(
                "https://github.com/dolphindb/DolphinDBModules/tree/"
                "43ace2cc4b81d048864ec2e40c25728d5d464e05/gtja191Alpha"
            ),
            artifact_sha256="e3d93adcdacff263795b8f0b97de1b806ce3666b118f7819af59d9ef5ff98543",
            license_class="apache-2.0",
            redistribution="notice-required",
            notes=(
                "Fixed-commit source is a parity oracle, not the authoritative report",
                "TradeMaster does not require the DolphinDB runtime",
            ),
        ),
    )


def _huatai53() -> tuple[FactorCandidateRecord, ...]:
    collection = "huatai-53-2020.06.02"
    source = ("huatai-53-2020.06.02",)
    reference = "Huatai 2020 Figure 3"
    rows: list[dict[str, object]] = [
        {
            "id": "value.ep",
            "alias": "EP",
            "cat": "value",
            "formula": "1 / pe_ttm",
            "inputs": ("daily_basic.pe_ttm",),
            "tier": "current_cache_partial",
            "direction": 1,
            "mapping": "fundamental.earnings_yield@1",
            "differences": (
                "Local positive_reciprocal invalidates negative PE while the report exposure does not",
            ),
        },
        {
            "id": "value.epcut",
            "alias": "EPcut",
            "cat": "value",
            "formula": "profit_deducted_ttm / total_market_value",
            "inputs": ("cashflow.profit_deducted_ttm", "daily_basic.total_market_value"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "value.bp",
            "alias": "BP",
            "cat": "value",
            "formula": "1 / pb",
            "inputs": ("daily_basic.pb",),
            "tier": "current_cache_partial",
            "direction": 1,
            "mapping": "fundamental.book_yield@1",
            "differences": ("Local positive_reciprocal invalidates non-positive PB",),
        },
        {
            "id": "value.sp",
            "alias": "SP",
            "cat": "value",
            "formula": "1 / ps_ttm",
            "inputs": ("daily_basic.ps_ttm",),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "value.ncfp",
            "alias": "NCFP",
            "cat": "value",
            "formula": "net_cashflow_ttm / total_market_value",
            "inputs": ("cashflow.net_cashflow_ttm", "daily_basic.total_market_value"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "value.ocfp",
            "alias": "OCFP",
            "cat": "value",
            "formula": "operating_cashflow_ttm / total_market_value",
            "inputs": ("cashflow.operating_cashflow_ttm", "daily_basic.total_market_value"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "value.dp",
            "alias": "DP",
            "cat": "value",
            "formula": "dv_ttm / 100",
            "inputs": ("daily_basic.dv_ttm",),
            "tier": "current_cache_partial",
            "direction": 1,
            "mapping": "fundamental.dividend_yield@1",
            "differences": (
                "Local value keeps the Tushare percentage unit instead of converting it to a ratio",
            ),
        },
        {
            "id": "value.g_pe",
            "alias": "G/PE",
            "cat": "value",
            "formula": "net_profit_ttm_yoy / pe_ttm",
            "inputs": ("daily_basic.pe_ttm", "financial_indicators.net_profit_ttm_yoy"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "growth.sales_g_ytd",
            "alias": "Sales_G_q",
            "cat": "growth",
            "formula": "operating_revenue_ytd_yoy",
            "inputs": ("financial_indicators.operating_revenue_ytd_yoy",),
            "tier": "provider_extension",
            "direction": 1,
            "mapping": "fundamental.sales_growth@1",
            "differences": (
                "Local q_sales_yoy is a single-quarter metric; the report factor is latest disclosed YTD year-on-year growth",
            ),
        },
        {
            "id": "growth.profit_g_ytd",
            "alias": "Profit_G_q",
            "cat": "growth",
            "formula": "net_profit_ytd_yoy",
            "inputs": ("financial_indicators.net_profit_ytd_yoy",),
            "tier": "provider_extension",
            "direction": 1,
            "mapping": "fundamental.profit_growth@1",
            "differences": (
                "Local q_profit_yoy is a single-quarter metric; the report factor is latest disclosed YTD year-on-year growth",
            ),
        },
        {
            "id": "growth.ocf_g_ytd",
            "alias": "OCF_G_q",
            "cat": "growth",
            "formula": "operating_cashflow_ytd_yoy",
            "inputs": ("financial_indicators.operating_cashflow_ytd_yoy",),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "growth.roe_g_ytd",
            "alias": "ROE_G_q",
            "cat": "growth",
            "formula": "roe_ytd_yoy",
            "inputs": ("financial_indicators.roe_ytd_yoy",),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.roe_ytd",
            "alias": "ROE_q",
            "cat": "quality",
            "formula": "roe_ytd",
            "inputs": ("financial_indicators.roe",),
            "tier": "current_cache_partial",
            "direction": 1,
            "mapping": "fundamental.roe@1",
            "differences": (
                "Local input preserves the Tushare percentage unit and strategy-specific PIT assembly",
            ),
        },
        {
            "id": "quality.roe_ttm",
            "alias": "ROE_ttm",
            "cat": "quality",
            "formula": "net_profit_ttm / average_equity_ttm",
            "inputs": ("balance.average_equity_ttm", "income.net_profit_ttm"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.roa_ytd",
            "alias": "ROA_q",
            "cat": "quality",
            "formula": "net_profit_ytd / average_assets_ytd",
            "inputs": ("balance.average_assets_ytd", "income.net_profit_ytd"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.roa_ttm",
            "alias": "ROA_ttm",
            "cat": "quality",
            "formula": "net_profit_ttm / average_assets_ttm",
            "inputs": ("balance.average_assets_ttm", "income.net_profit_ttm"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.gross_margin_ytd",
            "alias": "grossprofitmargin_q",
            "cat": "quality",
            "formula": "gross_profit_ytd / operating_revenue_ytd",
            "inputs": ("income.gross_profit_ytd", "income.operating_revenue_ytd"),
            "tier": "current_cache_partial",
            "direction": 1,
            "mapping": "fundamental.gross_margin@1",
            "differences": (
                "Local field is provider-derived grossprofit_margin and has not been parity-checked against the report formula",
            ),
        },
        {
            "id": "quality.gross_margin_ttm",
            "alias": "grossprofitmargin_ttm",
            "cat": "quality",
            "formula": "gross_profit_ttm / operating_revenue_ttm",
            "inputs": ("income.gross_profit_ttm", "income.operating_revenue_ttm"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.deducted_profit_margin_ytd",
            "alias": "profitmargin_q",
            "cat": "quality",
            "formula": "profit_deducted_ytd / operating_revenue_ytd",
            "inputs": ("income.operating_revenue_ytd", "income.profit_deducted_ytd"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.deducted_profit_margin_ttm",
            "alias": "profitmargin_ttm",
            "cat": "quality",
            "formula": "profit_deducted_ttm / operating_revenue_ttm",
            "inputs": ("income.operating_revenue_ttm", "income.profit_deducted_ttm"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.asset_turnover_ytd",
            "alias": "assetturnover_q",
            "cat": "quality",
            "formula": "operating_revenue_ytd / average_assets_ytd",
            "inputs": ("balance.average_assets_ytd", "income.operating_revenue_ytd"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.asset_turnover_ttm",
            "alias": "assetturnover_ttm",
            "cat": "quality",
            "formula": "operating_revenue_ttm / average_assets_ttm",
            "inputs": ("balance.average_assets_ttm", "income.operating_revenue_ttm"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "quality.ocf_to_net_profit_ytd",
            "alias": "operationcashflowratio_q",
            "cat": "quality",
            "formula": "operating_cashflow_ytd / net_profit_ytd",
            "inputs": ("cashflow.operating_cashflow_ytd", "income.net_profit_ytd"),
            "tier": "provider_extension",
            "direction": 1,
            "differences": (
                "Existing ocf_to_or is operating cash flow divided by revenue, not net_profit",
            ),
        },
        {
            "id": "quality.ocf_to_net_profit_ttm",
            "alias": "operationcashflowratio_ttm",
            "cat": "quality",
            "formula": "operating_cashflow_ttm / net_profit_ttm",
            "inputs": ("cashflow.operating_cashflow_ttm", "income.net_profit_ttm"),
            "tier": "provider_extension",
            "direction": 1,
            "differences": (
                "Existing ocf_to_or is operating cash flow divided by revenue, not net_profit",
            ),
        },
        {
            "id": "leverage.debt_cap_ratio",
            "alias": "debtcapratio",
            "cat": "leverage",
            "formula": "total_noncurrent_liabilities / total_market_value",
            "inputs": ("balance.total_noncurrent_liabilities", "daily_basic.total_market_value"),
            "tier": "provider_extension",
            "direction": -1,
        },
        {
            "id": "leverage.financial_leverage",
            "alias": "financial_leverage",
            "cat": "leverage",
            "formula": "total_assets / equity",
            "inputs": ("balance.equity", "balance.total_assets"),
            "tier": "provider_extension",
            "direction": -1,
        },
        {
            "id": "leverage.debt_equity_ratio",
            "alias": "debtequityratio",
            "cat": "leverage",
            "formula": "total_noncurrent_liabilities / equity",
            "inputs": ("balance.equity", "balance.total_noncurrent_liabilities"),
            "tier": "provider_extension",
            "direction": -1,
        },
        {
            "id": "leverage.cash_ratio",
            "alias": "cashratio",
            "cat": "leverage",
            "formula": "cash_and_equivalents / current_liabilities",
            "inputs": ("balance.cash_and_equivalents", "balance.current_liabilities"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "leverage.current_ratio",
            "alias": "currentratio",
            "cat": "leverage",
            "formula": "current_assets / current_liabilities",
            "inputs": ("balance.current_assets", "balance.current_liabilities"),
            "tier": "provider_extension",
            "direction": 1,
        },
        {
            "id": "size.log_total_market_value",
            "alias": "size",
            "cat": "size",
            "formula": "ln(total_market_value_cny)",
            "inputs": ("daily_basic.total_market_value",),
            "tier": "current_cache_partial",
            "direction": -1,
            "mapping": "huatai53.size.log_total_market_value@1",
            "exact_mapping": True,
        },
        {
            "id": "beta.market",
            "alias": "beta",
            "cat": "beta",
            "formula": "slope(stock_return ~ csi_all_share_return)",
            "inputs": ("derived_market.adjusted_close", "index_bars.close"),
            "tier": "benchmark_contract_required",
            "direction": 0,
        },
        {
            "id": "momentum_reversal.halpha_market",
            "alias": "HAlpha",
            "cat": "momentum_reversal",
            "formula": "intercept(stock_return ~ csi_all_share_return)",
            "inputs": ("derived_market.adjusted_close", "index_bars.close"),
            "tier": "benchmark_contract_required",
            "direction": -1,
        },
    ]
    for months in (1, 3, 6, 12):
        rows.append(
            {
                "id": f"momentum_reversal.return_{months}m",
                "alias": f"return_{months}m",
                "cat": "momentum_reversal",
                "formula": f"adjusted_close / delay_calendar_month(adjusted_close, {months}) - 1",
                "inputs": ("adj_factors.adj_factor", "daily_bars.close", "trade_calendar.is_open"),
                "tier": "current_cache_partial",
                "direction": -1,
            }
        )
        rows.append(
            {
                "id": f"momentum_reversal.turnover_weighted_return_{months}m",
                "alias": f"wgt_return_{months}m",
                "cat": "momentum_reversal",
                "formula": f"mean(turnover_rate * simple_return, {months} calendar months)",
                "inputs": (
                    "adj_factors.adj_factor",
                    "daily_bars.close",
                    "daily_basic.turnover_rate",
                    "trade_calendar.is_open",
                ),
                "tier": "provider_extension",
                "direction": -1,
            }
        )
        rows.append(
            {
                "id": f"momentum_reversal.exp_turnover_weighted_return_{months}m",
                "alias": f"exp_wgt_return_{months}m",
                "cat": "momentum_reversal",
                "formula": f"mean(turnover_rate * exp(-age / ({months} * 4)) * simple_return, {months} calendar months)",
                "inputs": (
                    "adj_factors.adj_factor",
                    "daily_bars.close",
                    "daily_basic.turnover_rate",
                    "trade_calendar.is_open",
                ),
                "tier": "provider_extension",
                "direction": -1,
            }
        )
    rows.append(
        {
            "id": "volatility.residual_volatility_market",
            "alias": "resvol",
            "cat": "volatility",
            "formula": "std(residual(stock_return ~ csi_all_share_return))",
            "inputs": ("derived_market.adjusted_close", "index_bars.close"),
            "tier": "benchmark_contract_required",
            "direction": -1,
        }
    )
    for months in (1, 3, 6, 12):
        rows.append(
            {
                "id": f"volatility.return_std_{months}m",
                "alias": f"std_{months}m",
                "cat": "volatility",
                "formula": f"std(simple_return, {months} calendar months)",
                "inputs": ("adj_factors.adj_factor", "daily_bars.close", "trade_calendar.is_open"),
                "tier": "current_cache_partial",
                "direction": -1,
            }
        )
    for months in (1, 3, 6, 12):
        rows.append(
            {
                "id": f"turnover.mean_{months}m",
                "alias": f"turn_{months}m",
                "cat": "turnover",
                "formula": f"mean(turnover_rate on eligible normal sessions, {months} calendar months)",
                "inputs": (
                    "daily_basic.turnover_rate",
                    "daily_limits_status.down_limit",
                    "daily_limits_status.suspended",
                    "daily_limits_status.up_limit",
                    "trade_calendar.is_open",
                ),
                "tier": "provider_extension",
                "direction": -1,
            }
        )

    result: list[FactorCandidateRecord] = []
    for row in rows:
        tier = cast(DataAvailability, row["tier"])
        mapping = row.get("mapping")
        if mapping is not None:
            status: ImplementationStatus = (
                "implemented" if row.get("exact_mapping") else "implemented_variant"
            )
            blockers: tuple[str, ...] = ()
        elif tier == "current_cache_partial":
            status = "ready"
            blockers = ()
        elif tier == "benchmark_contract_required":
            status = "blocked_semantics"
            blockers = (
                "Regression window, weighting, missing-session rules and CSI All Share benchmark identity are not frozen",
            )
        else:
            status = "blocked_data"
            blockers = (
                "Canonical field and point-in-time assembly are not yet available in the formal data registry",
            )
        result.append(
            _candidate(
                f"huatai53.{row['id']}",
                collection_id=collection,
                source_ids=source,
                display_name=f"Huatai {row['alias']}",
                aliases=(str(row["alias"]),),
                category=str(row["cat"]),
                formula_reference=reference,
                formula_expression=str(row["formula"]),
                required_inputs=cast(tuple[str, ...], row["inputs"]),
                required_operators=("tm-huatai53-operators/v1",),
                data_availability=tier,
                implementation_status=status,
                implementation_identity=(None if mapping is None else str(mapping)),
                semantic_differences=cast(tuple[str, ...], row.get("differences", ())),
                blocked_reasons=blockers,
                expected_direction=cast(int, row["direction"]),
            )
        )
    if len(result) != 53:
        raise RuntimeError(f"Huatai 53 manifest generated {len(result)} candidates")
    return tuple(result)


_MONEYFLOW_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("main_inflow", ("mfd_buyamt_d", "mfd_buyvol_d", "mfd_buyord", "mfd_buyamt_a", "mfd_buyvol_a")),
    (
        "main_outflow",
        ("mfd_sellamt_d", "mfd_sellvol_d", "mfd_sellord", "mfd_sellamt_a", "mfd_sellvol_a"),
    ),
    (
        "net_active_buy",
        (
            "mfd_netbuyamt",
            "mfd_netbuyvol",
            "mfd_netbuyamt_a",
            "mfd_netbuyvol_a",
            "mf_amt",
            "mf_amt_ratio",
            "mfd_inflowproportion_a",
            "mf_vol",
            "mfd_volinflowrate_a",
            "mf_vol_ratio",
        ),
    ),
    (
        "open_net_active_buy",
        (
            "mf_amt_open",
            "mfd_inflowrate_open_a",
            "mfd_inflowproportion_open_a",
            "mfd_inflowvolume_open_a",
            "mfd_volinflowrate_open_a",
            "mfd_volinflowproportion_open_a",
        ),
    ),
    (
        "close_net_active_buy",
        (
            "mf_amt_close",
            "mfd_inflowrate_close_a",
            "mfd_inflowproportion_close_a",
            "mfd_inflowvolume_close_a",
            "mfd_volinflowrate_close_a",
            "mfd_volinflowproportion_close_a",
        ),
    ),
    (
        "main_net_inflow",
        (
            "mfd_inflow_m",
            "mfd_inflowrate_m",
            "mfd_inflowproportion_m",
            "mfd_buyvol_m",
            "mfd_volinflowrate_m",
            "mfd_volinflowproportion_m",
        ),
    ),
    (
        "open_main_net_inflow",
        (
            "mfd_inflow_open_m",
            "mfd_inflowrate_open_m",
            "mfd_inflowproportion_open_m",
            "mfd_buyvol_open_m",
            "mfd_volinflowrate_open_m",
            "mfd_volinflowproportion_open_m",
        ),
    ),
    (
        "close_main_net_inflow",
        (
            "mfd_inflow_close_m",
            "mfd_inflowrate_close_m",
            "mfd_inflowproportion_close_m",
            "mfd_buyvol_close_m",
            "mfd_volinflowrate_close_m",
            "mfd_volinflowproportion_close_m",
        ),
    ),
)


def _huatai_moneyflow() -> tuple[FactorCandidateRecord, ...]:
    result = tuple(
        _candidate(
            f"huatai.moneyflow.{field}",
            collection_id="huatai-moneyflow-50-2018.05.17",
            source_ids=("huatai-moneyflow-2018.05.17",),
            display_name=f"Huatai moneyflow {field}",
            aliases=(field,),
            category=category,
            formula_reference="Huatai money-flow report field table",
            formula_expression=None,
            required_inputs=(f"wind_moneyflow.{field}",),
            data_availability="blocked_data",
            implementation_status="blocked_data",
            blocked_reasons=(
                "Tushare daily bars cannot reconstruct Wind order-size, aggressor-side and intraday-segment fields",
            ),
        )
        for category, fields in _MONEYFLOW_GROUPS
        for field in fields
    )
    if len(result) != 50:
        raise RuntimeError("Huatai money-flow manifest must contain 50 candidates")
    return result


_QUALITY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "profitability",
        (
            "qfa_roe",
            "qfa_roe_deducted",
            "roe_basic",
            "roe_avg",
            "roe_diluted",
            "roe_exbasic",
            "roe_deducted",
            "roe_exdiluted",
            "roe_ttm2",
            "qfa_roa",
            "roa",
            "roa2",
            "roa_ttm2",
            "roa2_ttm2",
            "qfa_grossprofitmargin",
            "grossprofitmargin",
            "grossprofitmargin_ttm2",
            "qfa_netprofitmargin",
            "netprofitmargin",
            "netprofitmargin_ttm2",
            "nptocostexpense_qfa",
            "nptocostexpense",
            "roic",
            "roic_ttm2",
        ),
    ),
    (
        "earnings_quality",
        (
            "qfa_operateincometoebt",
            "operateincometoebt",
            "operateincometoebt_ttm2",
            "qfa_deductedprofittoprofit",
            "deductedprofittoprofit",
            "taxtoebt",
            "taxtoebt_ttm",
        ),
    ),
    (
        "cashflow",
        (
            "qfa_ocftosales",
            "ocftoor",
            "ocftoor_ttm2",
            "ocftocf_qfa",
            "ocftocf",
            "ocftoassets",
            "ocftodividend",
        ),
    ),
    ("capital_structure", ("debttoassets", "catoassets", "currentdebttodebt")),
    (
        "solvency",
        ("current", "quick", "cashtocurrentdebt", "debttotangibleequity", "ebittointerest"),
    ),
    ("operation", ("invturn", "caturn", "assetsturn", "arturn", "apturn")),
)


def _huatai_quality51() -> tuple[FactorCandidateRecord, ...]:
    result = tuple(
        _candidate(
            f"huatai.quality51.{field}",
            collection_id="huatai-financial-quality-51-2018.05.25",
            source_ids=("huatai-quality-2018.05.25",),
            display_name=f"Huatai quality {field}",
            aliases=(field,),
            category=category,
            formula_reference="Huatai financial-quality factor table",
            formula_expression=None,
            required_inputs=(f"financial_statements.{field}",),
            data_availability="provider_extension",
            implementation_status="blocked_data",
            blocked_reasons=(
                "A typed report-period, announcement, revision and formula-grain PIT contract is required",
            ),
            expected_direction=0,
        )
        for category, fields in _QUALITY_GROUPS
        for field in fields
    )
    if len(result) != 51:
        raise RuntimeError("Huatai financial-quality manifest must contain 51 candidates")
    return result


def _huatai_consensus() -> tuple[FactorCandidateRecord, ...]:
    base = tuple(
        f"CON_{metric}{suffix}"
        for metric in ("NP", "EPS", "EP", "BP", "ROE")
        for suffix in ("", "_RANK", "_REL")
    )
    names = (*base, "REPORT_NUMBER", "AUTHOR_NUMBER", "ORGAN_NUMBER", "BUY_NUMBER")
    return tuple(
        _candidate(
            f"huatai.consensus.{name.lower()}",
            collection_id="huatai-consensus-19-2018.12.14",
            source_ids=("huatai-consensus-2018.12.14",),
            display_name=f"Huatai consensus {name}",
            aliases=(name,),
            category="analyst_consensus",
            formula_reference="Huatai analyst-consensus factor table",
            formula_expression=None,
            required_inputs=(f"analyst_consensus.{name.lower()}",),
            data_availability="blocked_data",
            implementation_status="blocked_data",
            blocked_reasons=(
                "No licensed historical point-in-time analyst-consensus snapshot source is configured",
            ),
            expected_direction=0,
        )
        for name in names
    )


_HISTORICAL_QUALITY_BASES = (
    "qfa_roe",
    "roe_deducted",
    "qfa_roa",
    "roa",
    "qfa_grossprofitmargin",
    "grossprofitmargin_ttm2",
    "qfa_netprofitmargin",
    "netprofitmargin_ttm2",
    "qfa_operateincometoebt",
    "operateincometoebt",
    "qfa_deductedprofittoprofit",
    "deductedprofittoprofit",
    "invturn",
    "caturn",
    "assetsturn",
    "apturn",
    "ocftoor",
    "ocftoor_ttm2",
    "debttoassets",
    "currentdebttodebt",
    "ebittointerest",
    "debttotangibleequity",
)


def _huatai_historical_quantiles() -> tuple[FactorCandidateRecord, ...]:
    pairs = [
        *((base, years) for base in ("EP", "BP", "SP", "NCFP", "OCFP") for years in (2, 4, 8, 12)),
        *((base, years) for base in _HISTORICAL_QUALITY_BASES for years in (6, 8, 12)),
    ]
    result = tuple(
        _candidate(
            f"huatai.historical_quantile.{base.lower()}.{years}y",
            collection_id="huatai-historical-quantile-2019.10.15",
            source_ids=("huatai-historical-2019.10.15",),
            display_name=f"Huatai historical percentile {base} {years}Y",
            aliases=(f"{base}_{years}y_percentile",),
            category="historical_percentile",
            formula_reference="Huatai historical-percentile visible parameter grid",
            formula_expression=f"time_series_percentile({base}, {years} years)",
            required_inputs=(f"historical_factor.{base.lower()}",),
            required_operators=("time_series_percentile",),
            data_availability="provider_extension",
            implementation_status="blocked_data",
            blocked_reasons=(
                "Long point-in-time financial history and percentile missing-value semantics are not frozen",
            ),
            expected_direction=0,
        )
        for base, years in pairs
    )
    if len(result) != 86:
        raise RuntimeError("Huatai visible historical-percentile grid must contain 86 candidates")
    return result


def _huatai_risk_model() -> tuple[FactorCandidateRecord, ...]:
    descriptors = {
        "size": "LNCAP",
        "beta": "BETA",
        "momentum": "RSTR",
        "residual_volatility": "0.74*DASTD + 0.16*CMRA + 0.10*HSIGMA",
        "nonlinear_size": "NLSIZE",
        "book_to_price": "BTOP",
        "liquidity": "0.35*STOM + 0.35*STOQ + 0.30*STOA",
        "earning_yield": "0.66*ETOP + 0.34*CETOP",
        "growth": "0.34*EGRO + 0.66*SGRO",
        "leverage": "0.38*MLEV + 0.35*DTOA + 0.27*BLEV",
    }
    return tuple(
        _candidate(
            f"huatai.risk_model.{name}",
            collection_id="huatai-risk-model-2019.06.12",
            source_ids=("huatai-risk-model-2019.06.12",),
            display_name=f"Huatai risk model {name}",
            aliases=(expression,),
            category="risk_style",
            formula_reference="Huatai structured risk-model descriptor table",
            formula_expression=expression,
            required_inputs=("risk_model.descriptor_inputs",),
            data_availability="provider_extension",
            implementation_status="risk_model_only",
            blocked_reasons=(),
            expected_direction=0,
        )
        for name, expression in descriptors.items()
    )


_GTJA_INPUT_GROUPS: tuple[tuple[tuple[str, ...], tuple[int, ...]], ...] = (
    (("open", "close"), (15, 37, 54, 184, 185)),
    (("open", "close", "high", "low"), (55, 107, 137, 171)),
    (("open", "close", "high", "low", "vol"), (140,)),
    (("open", "close", "vol"), (1, 136)),
    (("open", "close", "vol", "vwap"), (39, 45)),
    (("open", "close", "vwap"), (12,)),
    (("open", "high"), (6, 187)),
    (("open", "high", "low"), (118,)),
    (("open", "high", "low", "vol"), (56, 69)),
    (("open", "high", "low", "vwap"), (87,)),
    (("open", "low"), (93,)),
    (("open", "low", "vwap"), (156,)),
    (("open", "vol"), (35, 105, 139, 148)),
    (("open", "vol", "vwap"), (119,)),
    (
        ("close",),
        (
            10,
            14,
            18,
            19,
            20,
            21,
            22,
            23,
            24,
            27,
            31,
            34,
            46,
            53,
            58,
            63,
            65,
            66,
            67,
            71,
            79,
            86,
            88,
            89,
            98,
            106,
            112,
            116,
            122,
            127,
            129,
            135,
            143,
            146,
            147,
            151,
            152,
            153,
            157,
            160,
            162,
            165,
            166,
            167,
            169,
            173,
            174,
            183,
            189,
            190,
        ),
    ),
    (
        ("close", "high", "low"),
        (2, 3, 28, 47, 57, 59, 72, 78, 82, 96, 110, 126, 158, 159, 161, 164, 172, 175, 186),
    ),
    (("close", "high", "low", "vol"), (11, 52, 60, 111, 115, 117, 128, 150, 176, 191)),
    (("close", "high", "low", "vol", "vwap"), (114,)),
    (("close", "high", "vol"), (104,)),
    (("close", "high", "vol", "vwap"), (101, 163, 170)),
    (("close", "low", "vol"), (33, 91)),
    (("close", "vol"), (4, 25, 29, 40, 43, 48, 76, 84, 85, 94, 99, 113, 134, 142, 178, 180)),
    (("close", "vol", "vwap"), (7, 64, 73, 92, 125, 131, 144)),
    (("close", "vwap"), (17, 26, 120, 124)),
    (("close", "index_close"), (30, 149, 181)),
    (("close", "open", "index_close", "index_open"), (75, 182)),
    (("high", "low", "vol"), (9, 68, 123)),
    (("high", "low", "vol", "vwap"), (77, 130)),
    (("high", "low", "vwap"), (8, 13)),
    (("high", "vol"), (5, 32, 42, 62, 83, 141)),
    (("high", "vol", "vwap"), (108,)),
    (("low",), (103,)),
    (("low", "vol", "vwap"), (44, 61, 74, 138, 179)),
    (("vol",), (80, 81, 97, 100, 102, 145, 155, 168)),
    (("vol", "vwap"), (16, 36, 70, 90, 95, 121, 132, 154)),
    (("vwap",), (41,)),
    (("high",), (38, 177)),
    (("high", "low"), (49, 50, 51, 109, 133, 188)),
)

_GTJA_REVIEWED = {
    14: ("close - delay(close, 5)", ("delay", "subtract")),
    15: ("open / delay(close, 1) - 1", ("delay", "divide", "subtract")),
    18: ("close / delay(close, 5)", ("delay", "divide")),
    20: (
        "100 * (close - delay(close, 6)) / delay(close, 6)",
        ("delay", "divide", "multiply", "subtract"),
    ),
    31: (
        "100 * (close - mean(close, 12)) / mean(close, 12)",
        ("divide", "mean", "multiply", "subtract"),
    ),
    34: ("mean(close, 12) / close", ("divide", "mean")),
    46: (
        "(mean(close, 3) + mean(close, 6) + mean(close, 12) + mean(close, 24)) / (4 * close)",
        ("add", "divide", "mean", "multiply"),
    ),
    53: (
        "100 * count(close > delay(close, 1), 12) / 12",
        ("count", "delay", "divide", "greater", "multiply"),
    ),
    58: (
        "100 * count(close > delay(close, 1), 20) / 20",
        ("count", "delay", "divide", "greater", "multiply"),
    ),
    88: (
        "100 * (close - delay(close, 20)) / delay(close, 20)",
        ("delay", "divide", "multiply", "subtract"),
    ),
}

_GTJA_HIGH_RISK = {
    21: "REGBETA and SEQUENCE regression alignment is not frozen",
    27: "The report WMA operator semantics are not frozen",
    30: "The report regression requires MKT, SMB and HML factor returns",
    143: "Recursive SELF initialization and restart semantics are not frozen",
    149: "Filtered regression and benchmark identity are not frozen",
    165: "SUMAC and report-parenthesis semantics are disputed",
    181: "Report parentheses, benchmark identity and dimensional consistency are disputed",
    183: "SUMAC and report-parenthesis semantics are disputed",
    190: "The source PDF formula typography is damaged and requires correction review",
}


def _gtja_input_map() -> dict[int, tuple[str, ...]]:
    result: dict[int, tuple[str, ...]] = {}
    for inputs, numbers in _GTJA_INPUT_GROUPS:
        for number in numbers:
            if number in result:
                raise RuntimeError(f"GTJA Alpha{number} input dependency is duplicated")
            result[number] = inputs
    if set(result) != set(range(1, 192)):
        missing = sorted(set(range(1, 192)) - set(result))
        raise RuntimeError(f"GTJA input table is incomplete: {missing}")
    return result


def _gtja_required_inputs(number: int, names: tuple[str, ...]) -> tuple[str, ...]:
    fields: set[str] = set()
    equity_input = False
    for name in names:
        if name == "vwap":
            fields.add("derived_market.vwap_cny_per_share")
            fields.add("daily_bars.amount")
            fields.add("daily_bars.volume")
            equity_input = True
        elif name == "vol":
            fields.add("daily_bars.volume")
            equity_input = True
        elif name.startswith("index_"):
            fields.add(f"index_bars.{name.removeprefix('index_')}")
        else:
            fields.add(f"daily_bars.{name}")
            equity_input = True
    if equity_input:
        fields.add("adj_factors.adj_factor")
    if number == 30:
        fields.update(("factor_returns.mkt", "factor_returns.smb", "factor_returns.hml"))
    return _strings(fields)


def _gtja191() -> tuple[FactorCandidateRecord, ...]:
    dependencies = _gtja_input_map()
    result: list[FactorCandidateRecord] = []
    for number in range(1, 192):
        inputs = dependencies[number]
        formula, operators = _GTJA_REVIEWED.get(number, (None, ()))
        blockers: list[str] = []
        if number == 30:
            availability: DataAvailability = "blocked_data"
            status: ImplementationStatus = "blocked_data"
            blockers.append(_GTJA_HIGH_RISK[number])
        elif "index_close" in inputs or "index_open" in inputs:
            availability = "benchmark_contract_required"
            status = "blocked_semantics"
            blockers.append("The original benchmark identity and adjustment policy are not frozen")
        elif "vwap" in inputs:
            availability = "derived_from_current"
            status = "implemented" if number in _GTJA_REVIEWED else "blocked_semantics"
        else:
            availability = "current_cache_partial"
            status = "implemented" if number in _GTJA_REVIEWED else "blocked_semantics"
        if number not in _GTJA_REVIEWED and number != 30:
            blockers.append(
                "Source formula AST, corrections and versioned operator semantics have not passed review"
            )
        if number in _GTJA_HIGH_RISK and number != 30:
            blockers.append(_GTJA_HIGH_RISK[number])
        semantic: tuple[str, ...]
        if "vwap" in inputs:
            semantic = (
                "VWAP must use amount * 10 / volume for Tushare units and share the adjusted price basis",
            )
        else:
            semantic = ()
        result.append(
            _candidate(
                f"gtja191.alpha{number:03d}",
                collection_id="gtja-alpha191-2017.06.15",
                source_ids=("dolphindb-gtja191-43ace2c", "gtja-alpha191-2017.06.15"),
                display_name=f"GTJA Alpha{number:03d}",
                aliases=(f"Alpha{number}", f"alpha{number:03d}"),
                category="technical",
                formula_reference=f"GTJA report Table 6 Alpha{number}; DolphinDB fixed-commit oracle",
                formula_expression=formula,
                required_inputs=_gtja_required_inputs(number, inputs),
                required_operators=operators,
                data_availability=availability,
                implementation_status=status,
                implementation_identity=(
                    f"gtja191.alpha{number:03d}@1" if number in _GTJA_REVIEWED else None
                ),
                semantic_differences=semantic,
                blocked_reasons=tuple(blockers),
                expected_direction=0,
            )
        )
    return tuple(result)


def builtin_public_factor_library() -> PublicFactorLibrary:
    """Return the deterministic public-factor governance catalog shipped with TradeMaster."""

    sources = _sources()
    huatai53 = _huatai53()
    moneyflow = _huatai_moneyflow()
    quality51 = _huatai_quality51()
    consensus = _huatai_consensus()
    historical = _huatai_historical_quantiles()
    risk_model = _huatai_risk_model()
    gtja191 = _gtja191()
    collections = (
        _collection(
            "huatai-53-2020.06.02",
            display_name="华泰九类53风格因子",
            collection_type="style_factor_set",
            source_ids=("huatai-53-2020.06.02",),
            candidates=huatai53,
            notes=(
                "Current cache coverage is partial and cannot support unbiased full-A research",
                "Historical CITIC industry membership is still required for report-level parity",
            ),
        ),
        _collection(
            "huatai-moneyflow-50-2018.05.17",
            display_name="华泰资金流向50因子",
            collection_type="data_vendor_factor_set",
            source_ids=("huatai-moneyflow-2018.05.17",),
            candidates=moneyflow,
            notes=("All members are blocked by unavailable proprietary order-flow fields",),
        ),
        _collection(
            "huatai-financial-quality-51-2018.05.25",
            display_name="华泰财务质量51因子",
            collection_type="financial_factor_set",
            source_ids=("huatai-quality-2018.05.25",),
            candidates=quality51,
            notes=("Equivalent Tushare fields require a typed PIT formula and revision contract",),
        ),
        _collection(
            "huatai-consensus-19-2018.12.14",
            display_name="华泰一致预期19因子",
            collection_type="data_vendor_factor_set",
            source_ids=("huatai-consensus-2018.12.14",),
            candidates=consensus,
            notes=("All members require licensed historical consensus snapshots",),
        ),
        _collection(
            "huatai-historical-quantile-2019.10.15",
            display_name="华泰历史分位数可见86因子",
            collection_type="derived_factor_set",
            source_ids=("huatai-historical-2019.10.15",),
            candidates=historical,
            reported_count=89,
            notes=("The report abstract says 89 but the visible 5x4 plus 22x3 grid contains 86",),
        ),
        _collection(
            "huatai-risk-model-2019.06.12",
            display_name="华泰结构化风险模型10风格",
            collection_type="risk_model",
            source_ids=("huatai-risk-model-2019.06.12",),
            candidates=risk_model,
            notes=(
                "These descriptors belong to risk exposure and attribution, not predictive alpha",
            ),
        ),
        _collection(
            "gtja-alpha191-2017.06.15",
            display_name="国泰君安 Alpha191",
            collection_type="alpha_library",
            source_ids=("dolphindb-gtja191-43ace2c", "gtja-alpha191-2017.06.15"),
            candidates=gtja191,
            notes=(
                "All 191 names and field dependencies are cataloged; only reviewed formulas may become ready",
                "Current OHLCV cache contains 446 selected-union stocks rather than full A-share history",
                "DolphinDB is a fixed-commit parity oracle and does not replace source semantic review",
            ),
        ),
    )
    library = PublicFactorLibrary(
        sources=sources,
        collections=collections,
        candidates=(
            *huatai53,
            *moneyflow,
            *quality51,
            *consensus,
            *historical,
            *risk_model,
            *gtja191,
        ),
    )
    from .builtin import builtin_managed_factor_registry

    library.validate_implementations(builtin_managed_factor_registry())
    return library


__all__ = ["builtin_public_factor_library"]
