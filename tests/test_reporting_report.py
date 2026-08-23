from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trademaster.reporting import (
    BenchmarkSeries,
    ExposureObservation,
    PerformanceSeries,
    ReportIdentity,
    TradeSummary,
    build_html_report,
    compute_benchmark_metrics,
    compute_exposure_diagnostics,
)

START = datetime(2025, 1, 2, 7, tzinfo=UTC)
TIMES = tuple(START + timedelta(days=index) for index in range(4))


def _strategy() -> PerformanceSeries:
    return PerformanceSeries(
        event_times=TIMES,
        net_asset_values=tuple(
            Decimal(value) for value in ("100", "102", "99", "106")
        ),
        cash_values=tuple(Decimal(value) for value in ("100", "102", "99", "106")),
        market_values=(Decimal(0),) * 4,
    )


def test_benchmark_metrics_require_exact_sessions_and_match_regression() -> None:
    benchmark = BenchmarkSeries(
        benchmark_id="000016.SH",
        event_times=TIMES,
        close_values=tuple(Decimal(value) for value in ("100", "101", "100", "104")),
    )
    result = compute_benchmark_metrics(_strategy(), benchmark)

    assert result.benchmark_id == "000016.SH"
    assert result.tracking_error > 0
    assert result.information_ratio is not None
    assert result.beta is not None
    assert result.correlation is not None
    assert -5 < result.beta < 5
    assert -1 <= result.correlation <= 1

    misaligned = BenchmarkSeries(
        benchmark_id="SPX",
        event_times=TIMES[:-1],
        close_values=(Decimal(100), Decimal(101), Decimal(102)),
    )
    with pytest.raises(ValueError, match="sessions"):
        compute_benchmark_metrics(_strategy(), misaligned)

    short = PerformanceSeries(
        event_times=TIMES[:2],
        net_asset_values=(Decimal(100), Decimal(101)),
        cash_values=(Decimal(100), Decimal(101)),
        market_values=(Decimal(0), Decimal(0)),
    )
    short_benchmark = BenchmarkSeries(
        benchmark_id="flat",
        event_times=TIMES[:2],
        close_values=(Decimal(100), Decimal(100)),
    )
    undefined = compute_benchmark_metrics(short, short_benchmark)
    assert undefined.beta is None
    assert undefined.alpha is None
    assert undefined.correlation is None


def test_exposure_diagnostics_and_standalone_html_include_required_sections(
    tmp_path: Path,
) -> None:
    exposures = tuple(
        ExposureObservation(
            event_time=event_time,
            industry_weights=(("bank", Decimal("0.6")), ("technology", Decimal("0.4"))),
            market_cap_weights=(("large", Decimal("0.7")), ("small", Decimal("0.3"))),
        )
        for event_time in TIMES
    )
    diagnostics = compute_exposure_diagnostics(exposures)
    assert diagnostics.maximum_industry_weight == Decimal("0.6")
    assert diagnostics.maximum_market_cap_weight == Decimal("0.7")
    assert diagnostics.latest_industry_hhi == Decimal("0.52")

    benchmark = BenchmarkSeries(
        benchmark_id="000016.SH",
        event_times=TIMES,
        close_values=tuple(Decimal(value) for value in ("100", "101", "100", "104")),
    )
    trade = TradeSummary.from_scaled_values(
        gross_traded_notional_scaled=200_000_000_000,
        commission_scaled=1_000_000_000,
        tax_scaled=100_000_000,
        transfer_fee_scaled=20_000_000,
        slippage_scaled=200_000_000,
        average_nav_scaled=100_000_000_000,
    )
    identity = ReportIdentity(
        run_id="run-1",
        strategy_id="weekly-value",
        snapshot_id="a" * 64,
        benchmark_ids=("000016.SH",),
    )
    output = tmp_path / "report.html"
    build_html_report(
        output,
        identity=identity,
        strategy=_strategy(),
        benchmarks=(benchmark,),
        trade_summary=trade,
        exposures=exposures,
        annual_sessions=52,
    )

    html = output.read_text(encoding="utf-8")
    assert "plotly.js" in html.lower()
    assert "000016.SH" in html
    assert "a" * 64 in html
    assert "Drawdown" in html
    assert "Rolling Volatility and Sharpe" in html
    assert "Underwater Duration" in html
    assert "Monthly Return Heatmap" in html
    assert "Transaction Costs" in html
    assert "Annualization periods</th><td>52" in html
    assert "Benchmark Diagnostics" in html
    assert "Industry Exposure" in html
    assert "Market-cap Exposure" in html
    assert "指标与图表阅读指南" in html
    assert "Sharpe（夏普比率）" in html
    assert "当前报告按 52 个观测期年化" in html
    assert "换手率是全回测期累计成交金额" in html
    assert "VaR / CVaR 是单个观测期收益" in html
    assert "行业与市值暴露使用 Rust 账本的实际持仓市值" in html
    assert "run-1" in html and "weekly-value" in html
    manifest_match = re.search(
        r"<script id='trademaster-report-manifest' type='application/json'>(.*?)</script>",
        html,
    )
    assert manifest_match is not None
    manifest = json.loads(manifest_match.group(1))
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["input_sha256"])
