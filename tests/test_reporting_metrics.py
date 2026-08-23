from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from trademaster.reporting import (
    PerformanceSeries,
    TradeSummary,
    compute_strategy_metrics,
)

START = datetime(2025, 1, 2, 7, tzinfo=UTC)


def test_performance_metrics_match_hand_calculated_returns_and_drawdown() -> None:
    series = PerformanceSeries(
        event_times=tuple(START + timedelta(days=index) for index in range(4)),
        net_asset_values=tuple(
            Decimal(value) for value in ("100", "110", "88", "132")
        ),
        cash_values=tuple(Decimal(value) for value in ("100", "110", "88", "132")),
        market_values=(Decimal(0),) * 4,
    )
    metrics = compute_strategy_metrics(series, annual_sessions=252, risk_free_rate=0.0)

    assert metrics.session_returns == pytest.approx((0.1, -0.2, 0.5))
    assert metrics.total_return == pytest.approx(0.32)
    expected_volatility = math.sqrt(
        sum((value - (0.1 - 0.2 + 0.5) / 3) ** 2 for value in (0.1, -0.2, 0.5))
        / 2
    ) * math.sqrt(252)
    assert metrics.annualized_volatility == pytest.approx(expected_volatility)
    assert metrics.sharpe_ratio == pytest.approx(
        ((0.1 - 0.2 + 0.5) / 3) / (expected_volatility / math.sqrt(252))
        * math.sqrt(252)
    )
    assert metrics.maximum_drawdown == pytest.approx(-0.2)
    assert metrics.drawdown_peak_time == START + timedelta(days=1)
    assert metrics.drawdown_trough_time == START + timedelta(days=2)
    assert metrics.drawdown_recovery_time == START + timedelta(days=3)
    assert metrics.maximum_recovery_days == 2
    assert metrics.longest_underwater_days == 2
    assert metrics.positive_session_ratio == pytest.approx(2 / 3)
    assert metrics.best_session_return == pytest.approx(0.5)
    assert metrics.worst_session_return == pytest.approx(-0.2)


def test_flat_series_returns_explicit_none_instead_of_nan_or_infinity() -> None:
    series = PerformanceSeries(
        event_times=(START, START + timedelta(days=1)),
        net_asset_values=(Decimal(100), Decimal(100)),
        cash_values=(Decimal(100), Decimal(100)),
        market_values=(Decimal(0), Decimal(0)),
    )
    metrics = compute_strategy_metrics(series)

    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None
    assert metrics.calmar_ratio is None
    assert metrics.drawdown_recovery_time is None
    assert metrics.maximum_recovery_days is None
    assert metrics.maximum_drawdown == 0.0


def test_trade_summary_uses_average_nav_turnover_and_exact_cost_totals() -> None:
    summary = TradeSummary.from_scaled_values(
        gross_traded_notional_scaled=200_000_000_000,
        commission_scaled=1_000_000_000,
        tax_scaled=100_000_000,
        transfer_fee_scaled=20_000_000,
        slippage_scaled=200_000_000,
        average_nav_scaled=100_000_000_000,
    )

    assert summary.turnover_rate == pytest.approx(2.0)
    assert summary.total_cost == Decimal("13.20000000")
    assert summary.commission == Decimal("10.00000000")
    assert summary.tax == Decimal("1.00000000")
    assert summary.transfer_fee == Decimal("0.20000000")
    assert summary.slippage == Decimal("2.00000000")
    assert summary.average_nav == Decimal("1000.00000000")
    assert summary.cost_to_traded_notional == pytest.approx(0.0066)


def test_zero_notional_cost_ratio_is_none_or_rejected_when_costs_exist() -> None:
    empty = TradeSummary.from_scaled_values(
        gross_traded_notional_scaled=0,
        commission_scaled=0,
        tax_scaled=0,
        transfer_fee_scaled=0,
        slippage_scaled=0,
        average_nav_scaled=100_000_000,
    )
    assert empty.cost_to_traded_notional is None
    with pytest.raises(ValueError, match="costs require positive traded notional"):
        TradeSummary.from_scaled_values(
            gross_traded_notional_scaled=0,
            commission_scaled=100_000_000,
            tax_scaled=0,
            transfer_fee_scaled=0,
            slippage_scaled=0,
            average_nav_scaled=100_000_000,
        )
