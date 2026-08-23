from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from trademaster.strategies.industry_fundamental_top5 import (
    FundamentalRecord,
    IndustryFundamentalTop5Config,
    IndustryFundamentalTop5Strategy,
)
from trademaster.strategies.industry_fundamental_top5.real import (
    FundamentalStrategyRunError,
    RealStrategyConfig,
    _active_industry,
    _adjust_price,
    _adjusted_execution_limits,
    _latest_financial,
    _rejection_counts,
    _resolve_industry_membership,
    _retry_open_dates,
    _strategy_definition_sha256,
    _venue_for_instrument,
    _weekly_event_days,
)

AS_OF = datetime(2025, 5, 6, 7, tzinfo=UTC)


def _record(
    instrument_id: str,
    industry_id: str,
    *,
    pe_ttm: float = 10,
    pb: float = 1,
    dv_ttm: float = 2,
    roe: float | None = 15,
    grossprofit_margin: float | None = 30,
    ocf_to_or: float | None = 20,
    q_sales_yoy: float | None = 10,
    q_profit_yoy: float | None = 12,
    debt_to_assets: float | None = 40,
    total_market_value: float = 100_000,
) -> FundamentalRecord:
    return FundamentalRecord(
        instrument_id=instrument_id,
        industry_id=industry_id,
        industry_name=industry_id,
        signal_time=AS_OF,
        pe_ttm=pe_ttm,
        pb=pb,
        dv_ttm=dv_ttm,
        roe=roe,
        grossprofit_margin=grossprofit_margin,
        ocf_to_or=ocf_to_or,
        q_sales_yoy=q_sales_yoy,
        q_profit_yoy=q_profit_yoy,
        debt_to_assets=debt_to_assets,
        total_market_value=total_market_value,
    )


def test_scores_value_quality_growth_and_safety_in_the_documented_directions() -> None:
    strategy = IndustryFundamentalTop5Strategy()
    records = (
        _record(
            "000001.SZ",
            "bank",
            pe_ttm=6,
            pb=0.7,
            dv_ttm=5,
            roe=20,
            grossprofit_margin=45,
            ocf_to_or=35,
            q_sales_yoy=20,
            q_profit_yoy=25,
            debt_to_assets=25,
        ),
        _record("000002.SZ", "bank"),
        _record(
            "000003.SZ",
            "bank",
            pe_ttm=30,
            pb=4,
            dv_ttm=0,
            roe=5,
            grossprofit_margin=10,
            ocf_to_or=-5,
            q_sales_yoy=-10,
            q_profit_yoy=-20,
            debt_to_assets=80,
        ),
    )

    scored = strategy.score(records)

    assert [item.instrument_id for item in scored] == [
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
    ]
    assert scored[0].score > scored[1].score > scored[2].score
    assert all(item.valid_metric_count == 9 for item in scored)


def test_missing_metrics_are_neutral_but_invalid_valuation_or_too_few_fields_fail() -> None:
    strategy = IndustryFundamentalTop5Strategy()
    valid_bank = _record(
        "600000.SH",
        "bank",
        grossprofit_margin=None,
        ocf_to_or=None,
        q_profit_yoy=None,
    )
    invalid_pe = _record("600001.SH", "bank", pe_ttm=-1)
    too_sparse = _record(
        "600002.SH",
        "bank",
        roe=None,
        grossprofit_margin=None,
        ocf_to_or=None,
        q_sales_yoy=None,
        q_profit_yoy=None,
        debt_to_assets=None,
    )

    scored = strategy.score((valid_bank, invalid_pe, too_sparse))

    assert [item.instrument_id for item in scored] == ["600000.SH"]
    assert scored[0].valid_metric_count == 6


def test_selects_top_five_per_industry_with_stable_ties_and_exact_equal_weights() -> None:
    strategy = IndustryFundamentalTop5Strategy()
    records = tuple(
        _record(f"00000{index}.SZ", "industry-a", roe=10 + index) for index in range(1, 7)
    ) + tuple(_record(f"60000{index}.SH", "industry-b", roe=20) for index in range(1, 4))

    selected = strategy.select(records)

    by_industry = {
        industry: [item for item in selected if item.industry_id == industry]
        for industry in {item.industry_id for item in selected}
    }
    assert [item.instrument_id for item in by_industry["industry-a"]] == [
        "000006.SZ",
        "000005.SZ",
        "000004.SZ",
        "000003.SZ",
        "000002.SZ",
    ]
    assert [item.instrument_id for item in by_industry["industry-b"]] == [
        "600001.SH",
        "600002.SH",
        "600003.SH",
    ]
    assert len(selected) == 8
    assert sum((item.target_weight for item in selected), Decimal(0)) == Decimal("1.00000000")
    assert {item.target_weight for item in selected} == {Decimal("0.12500000")}
    assert [item.industry_rank for item in by_industry["industry-a"]] == [1, 2, 3, 4, 5]


def test_selects_only_industries_with_the_best_mean_global_fundamental_scores() -> None:
    strategy = IndustryFundamentalTop5Strategy(
        IndustryFundamentalTop5Config(
            top_per_industry=1,
            top_industries_by_mean_score=2,
        )
    )
    records = (
        _record(
            "000001.SZ",
            "strong",
            pe_ttm=5,
            pb=0.5,
            dv_ttm=6,
            roe=25,
            grossprofit_margin=50,
            ocf_to_or=40,
            q_sales_yoy=25,
            q_profit_yoy=30,
            debt_to_assets=20,
        ),
        _record(
            "000002.SZ",
            "strong",
            pe_ttm=7,
            pb=0.8,
            dv_ttm=5,
            roe=22,
            grossprofit_margin=45,
            ocf_to_or=35,
            q_sales_yoy=20,
            q_profit_yoy=24,
            debt_to_assets=25,
        ),
        _record("300001.SZ", "middle", roe=16),
        _record("300002.SZ", "middle", roe=14),
        _record(
            "600001.SH",
            "weak",
            pe_ttm=30,
            pb=4,
            dv_ttm=0,
            roe=5,
            grossprofit_margin=10,
            ocf_to_or=-5,
            q_sales_yoy=-10,
            q_profit_yoy=-20,
            debt_to_assets=80,
        ),
        _record(
            "600002.SH",
            "weak",
            pe_ttm=25,
            pb=3,
            dv_ttm=1,
            roe=7,
            grossprofit_margin=15,
            ocf_to_or=0,
            q_sales_yoy=-5,
            q_profit_yoy=-10,
            debt_to_assets=70,
        ),
    )

    selected = strategy.select(records)

    assert [item.industry_id for item in selected] == ["middle", "strong"]
    assert [item.instrument_id for item in selected] == ["300001.SZ", "000001.SZ"]
    assert {item.industry_rank for item in selected} == {1}
    assert {item.target_weight for item in selected} == {Decimal("0.50000000")}


def test_single_cross_section_strategy_rejects_mixed_signal_times() -> None:
    strategy = IndustryFundamentalTop5Strategy()
    later = replace(
        _record("000002.SZ", "bank"),
        signal_time=datetime(2030, 5, 6, 7, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="single signal_time"):
        strategy.select((_record("000001.SZ", "bank"), later))


def test_exact_top_industry_selection_fails_when_coverage_is_too_small() -> None:
    strategy = IndustryFundamentalTop5Strategy(
        IndustryFundamentalTop5Config(
            top_per_industry=1,
            top_industries_by_mean_score=3,
        )
    )

    with pytest.raises(ValueError, match="eligible industries"):
        strategy.select(
            (
                _record("000001.SZ", "bank"),
                _record("600001.SH", "technology"),
            )
        )


def test_weekly_observations_keep_week_end_and_required_signal_execution_days() -> None:
    open_days = tuple(date(2025, 5, day) for day in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16))

    selected = _weekly_event_days(
        open_days,
        required=frozenset({date(2025, 5, 6), date(2025, 5, 7)}),
        start=date(2025, 5, 5),
        end=date(2025, 5, 16),
    )

    assert selected == (
        date(2025, 5, 6),
        date(2025, 5, 7),
        date(2025, 5, 9),
        date(2025, 5, 16),
    )


def test_price_adjustment_uses_same_day_factor_and_end_of_range_base() -> None:
    assert _adjust_price(Decimal("20.00"), Decimal(4), Decimal(8)) == Decimal("10.00000000")


def test_execution_limits_fail_closed_without_exact_tushare_evidence() -> None:
    with pytest.raises(FundamentalStrategyRunError, match="limit evidence"):
        _adjusted_execution_limits(
            None,
            factor=Decimal(4),
            base_factor=Decimal(8),
        )

    assert _adjusted_execution_limits(
        {"up_limit": "22", "down_limit": "18"},
        factor=Decimal(4),
        base_factor=Decimal(8),
    ) == (Decimal("11.00000000"), Decimal("9.00000000"))


def test_instrument_suffix_maps_to_the_correct_a_share_venue() -> None:
    assert _venue_for_instrument("600000.SH") == "sse"
    assert _venue_for_instrument("000001.SZ") == "szse"
    assert _venue_for_instrument("920001.BJ") == "bse"
    with pytest.raises(FundamentalStrategyRunError, match="suffix"):
        _venue_for_instrument("UNKNOWN")


def test_rejection_summary_uses_the_rust_wire_code_field() -> None:
    assert _rejection_counts(
        (
            {"code": "suspended"},
            {"code": "suspended"},
            {"code": "insufficient_cash"},
        )
    ) == {"insufficient_cash": 1, "suspended": 2}


def test_financial_pit_uses_original_version_when_revision_time_is_unknown() -> None:
    rows: list[dict[str, object]] = [
        {
            "ann_date": "20250430",
            "end_date": "20250331",
            "update_flag": "0",
            "roe": 10.0,
        },
        {
            "ann_date": "20250430",
            "end_date": "20250331",
            "update_flag": "1",
            "roe": 99.0,
        },
    ]

    selected = _latest_financial(rows, date(2025, 5, 6))

    assert selected is not None
    assert selected["update_flag"] == "0"
    assert selected["roe"] == 10.0


def test_future_sw2021_membership_is_not_backfilled_before_its_in_date() -> None:
    rows: list[dict[str, object]] = [
        {
            "ts_code": "000001.SZ",
            "l1_code": "801780.SI",
            "in_date": "20211213",
            "out_date": None,
            "is_new": "Y",
        }
    ]

    assert _active_industry(rows, "000001.SZ", date(2016, 11, 7)) is None


def test_sw2021_membership_uses_the_historical_active_interval() -> None:
    rows: list[dict[str, object]] = [
        {
            "ts_code": "000001.SZ",
            "l1_code": "801780.SI",
            "in_date": "20100101",
            "out_date": "20201231",
            "is_new": "N",
        },
        {
            "ts_code": "000001.SZ",
            "l1_code": "801790.SI",
            "in_date": "20210101",
            "out_date": None,
            "is_new": "Y",
        },
    ]

    assert _active_industry(rows, "000001.SZ", date(2020, 6, 30)) == (
        "801780.SI",
        date(2010, 1, 1),
        None,
    )
    assert _active_industry(rows, "000001.SZ", date(2021, 6, 30)) == (
        "801790.SI",
        date(2021, 1, 1),
        None,
    )
    assert _active_industry(rows[:1], "000001.SZ", date(2021, 1, 1)) is None


def test_formal_industry_policy_fails_closed_while_static_experiment_is_explicit() -> None:
    rows: list[dict[str, object]] = [
        {
            "ts_code": "000001.SZ",
            "l1_code": "801780.SI",
            "in_date": "20211213",
            "out_date": None,
            "is_new": "Y",
        }
    ]

    with pytest.raises(FundamentalStrategyRunError, match="historical industry"):
        _resolve_industry_membership(
            rows,
            "000001.SZ",
            date(2016, 11, 7),
            policy="historical_interval_required",
        )
    assert _resolve_industry_membership(
        rows,
        "000001.SZ",
        date(2016, 11, 7),
        policy="static_latest_experiment",
    ) == ("801780.SI", date(2021, 12, 13), None)


def test_exit_retry_window_includes_execution_and_next_nineteen_open_days() -> None:
    open_days = tuple(date(2025, 5, day) for day in range(1, 31))

    assert _retry_open_dates(open_days, date(2025, 5, 7), attempts=20) == tuple(
        date(2025, 5, day) for day in range(7, 27)
    )


def test_real_run_config_exposes_a_checked_top_per_industry_dimension() -> None:
    assert RealStrategyConfig(top_per_industry=1).top_per_industry == 1
    assert RealStrategyConfig(top_industries_by_mean_score=10).top_industries_by_mean_score == 10

    with pytest.raises(ValueError, match="config"):
        RealStrategyConfig(top_per_industry=0)
    with pytest.raises(ValueError, match="config"):
        RealStrategyConfig(top_industries_by_mean_score=0)
    with pytest.raises(ValueError, match="config"):
        RealStrategyConfig(industry_membership_policy="unknown")  # type: ignore[arg-type]


def test_strategy_definition_identity_binds_full_config_and_factor_definitions() -> None:
    factor_definitions = ("a" * 64, "b" * 64)
    baseline = _strategy_definition_sha256(
        RealStrategyConfig(), factor_definition_sha256s=factor_definitions
    )
    changed_universe = _strategy_definition_sha256(
        RealStrategyConfig(universe_index="000905.SH"),
        factor_definition_sha256s=factor_definitions,
    )
    changed_benchmark = _strategy_definition_sha256(
        RealStrategyConfig(benchmark_id="000905.SH"),
        factor_definition_sha256s=factor_definitions,
    )
    changed_factor = _strategy_definition_sha256(
        RealStrategyConfig(),
        factor_definition_sha256s=("a" * 64, "c" * 64),
    )

    assert len(baseline) == 64
    assert len({baseline, changed_universe, changed_benchmark, changed_factor}) == 4
