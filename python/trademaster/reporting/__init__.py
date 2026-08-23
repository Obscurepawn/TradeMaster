"""Reproducible performance metrics and standalone visual reports."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape
from itertools import pairwise
from pathlib import Path

import plotly.graph_objects as go  # type: ignore[import-untyped]
import plotly.io as pio  # type: ignore[import-untyped]

from trademaster.contracts import _require_utc

_SCALE = Decimal(100_000_000)


@dataclass(frozen=True, slots=True)
class PerformanceSeries:
    event_times: tuple[datetime, ...]
    net_asset_values: tuple[Decimal, ...]
    cash_values: tuple[Decimal, ...]
    market_values: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if (
            len(self.event_times) < 2
            or len(self.event_times) != len(self.net_asset_values)
            or len(self.event_times) != len(self.cash_values)
            or len(self.event_times) != len(self.market_values)
            or any(
                left >= right
                for left, right in pairwise(self.event_times)
            )
            or any(value <= 0 or not value.is_finite() for value in self.net_asset_values)
            or any(value < 0 or not value.is_finite() for value in self.cash_values)
            or any(value < 0 or not value.is_finite() for value in self.market_values)
            or any(
                cash + market != nav
                for nav, cash, market in zip(
                    self.net_asset_values,
                    self.cash_values,
                    self.market_values,
                    strict=True,
                )
            )
        ):
            raise ValueError(
                "performance series must be aligned, ordered, and reconcile to NAV"
            )
        for event_time in self.event_times:
            _require_utc(event_time)


@dataclass(frozen=True, slots=True)
class StrategyMetrics:
    session_returns: tuple[float, ...]
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    maximum_drawdown: float
    drawdown_peak_time: datetime | None
    drawdown_trough_time: datetime | None
    drawdown_recovery_time: datetime | None
    maximum_recovery_days: int | None
    longest_underwater_days: int
    positive_session_ratio: float
    best_session_return: float
    worst_session_return: float
    value_at_risk_95: float
    conditional_value_at_risk_95: float


@dataclass(frozen=True, slots=True)
class TradeSummary:
    gross_traded_notional: Decimal
    commission: Decimal
    tax: Decimal
    transfer_fee: Decimal
    slippage: Decimal
    total_cost: Decimal
    average_nav: Decimal
    turnover_rate: float
    cost_to_traded_notional: float | None

    @classmethod
    def from_scaled_values(
        cls,
        *,
        gross_traded_notional_scaled: int,
        commission_scaled: int,
        tax_scaled: int,
        transfer_fee_scaled: int,
        slippage_scaled: int,
        average_nav_scaled: int,
    ) -> TradeSummary:
        values = (
            gross_traded_notional_scaled,
            commission_scaled,
            tax_scaled,
            transfer_fee_scaled,
            slippage_scaled,
            average_nav_scaled,
        )
        if any(value < 0 for value in values) or average_nav_scaled <= 0:
            raise ValueError("trade summary values must be non-negative with positive NAV")
        total_cost_scaled = (
            commission_scaled
            + tax_scaled
            + transfer_fee_scaled
            + slippage_scaled
        )
        gross = (Decimal(gross_traded_notional_scaled) / _SCALE).quantize(
            Decimal("0.00000001")
        )
        total_cost = (Decimal(total_cost_scaled) / _SCALE).quantize(
            Decimal("0.00000001")
        )
        if gross_traded_notional_scaled == 0 and total_cost_scaled > 0:
            raise ValueError("costs require positive traded notional")

        def scaled(value: int) -> Decimal:
            return (Decimal(value) / _SCALE).quantize(Decimal("0.00000001"))

        return cls(
            gross_traded_notional=gross,
            commission=scaled(commission_scaled),
            tax=scaled(tax_scaled),
            transfer_fee=scaled(transfer_fee_scaled),
            slippage=scaled(slippage_scaled),
            total_cost=total_cost,
            average_nav=scaled(average_nav_scaled),
            turnover_rate=gross_traded_notional_scaled / average_nav_scaled,
            cost_to_traded_notional=(
                total_cost_scaled / gross_traded_notional_scaled
                if gross_traded_notional_scaled
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkSeries:
    benchmark_id: str
    event_times: tuple[datetime, ...]
    close_values: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if (
            not self.benchmark_id
            or len(self.event_times) < 2
            or len(self.event_times) != len(self.close_values)
            or any(left >= right for left, right in pairwise(self.event_times))
            or any(value <= 0 or not value.is_finite() for value in self.close_values)
        ):
            raise ValueError("benchmark series must be aligned, positive and ordered")
        for event_time in self.event_times:
            _require_utc(event_time)


@dataclass(frozen=True, slots=True)
class BenchmarkMetrics:
    benchmark_id: str
    tracking_error: float
    information_ratio: float | None
    beta: float | None
    alpha: float | None
    correlation: float | None


def compute_benchmark_metrics(
    strategy: PerformanceSeries,
    benchmark: BenchmarkSeries,
    *,
    annual_sessions: int = 252,
) -> BenchmarkMetrics:
    if strategy.event_times != benchmark.event_times:
        raise ValueError("strategy and benchmark sessions must match exactly")
    strategy_returns = [
        float(current / previous - 1)
        for previous, current in pairwise(strategy.net_asset_values)
    ]
    benchmark_returns = [
        float(current / previous - 1)
        for previous, current in pairwise(benchmark.close_values)
    ]
    active = [
        strategy_value - benchmark_value
        for strategy_value, benchmark_value in zip(
            strategy_returns, benchmark_returns, strict=True
        )
    ]
    active_stdev = statistics.stdev(active) if len(active) > 1 else 0.0
    tracking_error = active_stdev * math.sqrt(annual_sessions)
    information_ratio = (
        statistics.fmean(active) / active_stdev * math.sqrt(annual_sessions)
        if active_stdev > 0
        else None
    )
    beta: float | None = None
    alpha: float | None = None
    correlation: float | None = None
    if len(benchmark_returns) > 1:
        benchmark_variance = statistics.variance(benchmark_returns)
        strategy_stdev = statistics.stdev(strategy_returns)
        benchmark_stdev = statistics.stdev(benchmark_returns)
        if benchmark_variance > 0:
            covariance = statistics.covariance(strategy_returns, benchmark_returns)
            beta = covariance / benchmark_variance
            alpha = (
                statistics.fmean(strategy_returns)
                - beta * statistics.fmean(benchmark_returns)
            ) * annual_sessions
        if strategy_stdev > 0 and benchmark_stdev > 0:
            correlation = statistics.correlation(strategy_returns, benchmark_returns)
    return BenchmarkMetrics(
        benchmark_id=benchmark.benchmark_id,
        tracking_error=tracking_error,
        information_ratio=information_ratio,
        beta=beta,
        alpha=alpha,
        correlation=correlation,
    )


@dataclass(frozen=True, slots=True)
class ExposureObservation:
    event_time: datetime
    industry_weights: tuple[tuple[str, Decimal], ...]
    market_cap_weights: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        _require_utc(self.event_time)
        for label, weights in (
            ("industry", self.industry_weights),
            ("market-cap", self.market_cap_weights),
        ):
            names = tuple(name for name, _ in weights)
            if (
                not weights
                or names != tuple(sorted(set(names)))
                or any(value < 0 or not value.is_finite() for _, value in weights)
                or sum((value for _, value in weights), Decimal(0)) != Decimal(1)
            ):
                raise ValueError(f"{label} exposure must be canonical and sum to one")


@dataclass(frozen=True, slots=True)
class ExposureDiagnostics:
    maximum_industry_weight: Decimal
    maximum_market_cap_weight: Decimal
    latest_industry_hhi: Decimal
    latest_market_cap_hhi: Decimal


def compute_exposure_diagnostics(
    observations: tuple[ExposureObservation, ...],
) -> ExposureDiagnostics:
    if not observations or any(
        left.event_time >= right.event_time for left, right in pairwise(observations)
    ):
        raise ValueError("exposure observations must be nonempty and ordered")
    latest = observations[-1]
    return ExposureDiagnostics(
        maximum_industry_weight=max(
            value for item in observations for _, value in item.industry_weights
        ),
        maximum_market_cap_weight=max(
            value for item in observations for _, value in item.market_cap_weights
        ),
        latest_industry_hhi=sum(
            (value * value for _, value in latest.industry_weights), Decimal(0)
        ),
        latest_market_cap_hhi=sum(
            (value * value for _, value in latest.market_cap_weights), Decimal(0)
        ),
    )


@dataclass(frozen=True, slots=True)
class ReportIdentity:
    run_id: str
    strategy_id: str
    snapshot_id: str
    benchmark_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.run_id
            or not self.strategy_id
            or len(self.snapshot_id) != 64
            or any(character not in "0123456789abcdef" for character in self.snapshot_id)
            or self.benchmark_ids != tuple(sorted(set(self.benchmark_ids)))
        ):
            raise ValueError("report identity is not canonical")


def _normalized(values: tuple[Decimal, ...]) -> list[float]:
    base = values[0]
    return [float(value / base - 1) for value in values]


def _figure_html(
    figure: go.Figure, *, include_plotlyjs: bool, div_id: str
) -> str:
    value: str = pio.to_html(
        figure,
        include_plotlyjs=include_plotlyjs,
        full_html=False,
        config={"responsive": True, "displaylogo": False},
        div_id=div_id,
    )
    return value


def _metric_guide_html(*, annual_sessions: int, rolling_window: int) -> str:
    return (
        "<section class='metric-guide'><h2>指标与图表阅读指南</h2>"
        "<p>当前报告按 "
        f"{annual_sessions} 个观测期年化，无风险利率按 0 计算。"
        "收益和风险指标基于扣除交易成本后的账户净值；数值不是未来收益承诺。</p>"
        "<h3>策略指标</h3><table><tr><th>指标</th><th>具体含义</th>"
        "<th>如何阅读</th></tr>"
        "<tr><td>Total return（总收益率）</td><td>期末净值 / 期初净值 - 1。</td>"
        "<td>越高越好，但不反映回测持续时间和期间风险。</td></tr>"
        "<tr><td>Annual return（年化收益率）</td>"
        "<td>把总收益按观测期数量和年化期数折算为复合年收益。</td>"
        "<td>便于比较不同长度的回测；这里不是按自然日精确年化。</td></tr>"
        "<tr><td>Annual volatility（年化波动率）</td>"
        "<td>单期收益率标准差乘以年化期数的平方根。</td>"
        "<td>越低通常越平稳，但必须和收益一起判断。</td></tr>"
        "<tr><td>Sharpe（夏普比率）</td>"
        "<td>年化超额收益除以年化波动；当前无风险利率为 0。</td>"
        "<td>越高越好；低于 1 通常表示每承担一单位波动获得的回报有限。</td></tr>"
        "<tr><td>Sortino（索提诺比率）</td>"
        "<td>类似 Sharpe，但分母只度量低于无风险收益的下行偏离。</td>"
        "<td>越高越好，更聚焦投资者不喜欢的下跌风险。</td></tr>"
        "<tr><td>Calmar（卡玛比率）</td>"
        "<td>年化收益率 / 最大回撤绝对值。</td>"
        "<td>越高越好；直接衡量收益是否足以补偿最深回撤。</td></tr>"
        "<tr><td>Max drawdown（最大回撤）</td>"
        "<td>净值从历史峰值跌到随后谷底的最大跌幅。</td>"
        "<td>越接近 0 越好；例如 -27% 表示峰值后最多曾亏损约 27%。</td></tr>"
        "<tr><td>Recovery days（最大回撤修复天数）</td>"
        "<td>最大回撤对应峰值到净值重新达到该峰值的自然日数。</td>"
        "<td>越短越好；若回测结束仍未修复则显示 N/A。</td></tr>"
        "<tr><td>Longest underwater（最长水下时间）</td>"
        "<td>任意一次净值低于此前历史新高的最长自然日数。</td>"
        "<td>越短越好；它可能并不是跌幅最深的那次回撤。</td></tr>"
        "<tr><td>VaR / CVaR 95%</td>"
        "<td>VaR / CVaR 是单个观测期收益：VaR 是历史最差 5% 区间的门槛，"
        "CVaR 是落入该尾部区间的平均收益。</td>"
        "<td>越接近 0 越好；它们不是最大亏损，也不是损失上限。</td></tr>"
        "<tr><td>Turnover（换手率）</td>"
        "<td>换手率是全回测期累计成交金额 / 全期平均净值，不是年化换手率。</td>"
        "<td>越低通常交易摩擦越小；需结合调仓频率和策略容量判断。</td></tr>"
        "<tr><td>Total costs（总交易成本）</td>"
        "<td>佣金、税费、过户费和滑点的合计，单位为账户本币。</td>"
        "<td>越低越好；报告收益已经通过 Rust 账本反映这些成本。</td></tr></table>"
        "<h3>基准诊断</h3><table><tr><th>指标</th><th>具体含义</th>"
        "<th>如何阅读</th></tr>"
        "<tr><td>Tracking error（跟踪误差）</td>"
        "<td>策略单期收益减基准单期收益后，其差值波动的年化值。</td>"
        "<td>越大表示策略路径偏离基准越多，本身不代表好坏。</td></tr>"
        "<tr><td>Information ratio（信息比率）</td>"
        "<td>年化主动收益 / 跟踪误差。</td>"
        "<td>越高越好，表示每承担一单位偏离基准的风险获得更多超额收益。</td></tr>"
        "<tr><td>Beta</td><td>策略收益对基准收益的敏感度。</td>"
        "<td>1 表示近似同步；小于 1 表示对基准涨跌通常较不敏感。</td></tr>"
        "<tr><td>Alpha</td><td>按单期线性回归截距折算的年化主动收益。</td>"
        "<td>正值较好，但只是历史统计关系，不证明因果或未来超额收益。</td></tr>"
        "<tr><td>Correlation（相关系数）</td>"
        "<td>策略与基准单期收益的线性相关程度，范围 -1 至 1。</td>"
        "<td>越接近 1 越同涨同跌；越接近 0 线性联动越弱。</td></tr></table>"
        "<h3>图表</h3><table><tr><th>图表</th><th>阅读方式</th></tr>"
        "<tr><td>Cumulative Return vs Benchmarks</td>"
        "<td>策略与基准均从 0% 起算的累计收益曲线；曲线更高表示截至该时点累计收益更高。</td></tr>"
        "<tr><td>Drawdown</td>"
        "<td>每个时点相对此前净值最高点的跌幅；越深表示离历史高点越远。</td></tr>"
        "<tr><td>Rolling Volatility and Sharpe</td><td>最近 "
        f"{rolling_window} 个观测期的滚动波动率（左轴）和 Sharpe（右轴），"
        "用于观察风险收益特征是否稳定。</td></tr>"
        "<tr><td>Underwater Duration</td>"
        "<td>从上一次净值新高至当前仍未创新高的自然日数；回到 0 代表创出新高。</td></tr>"
        "<tr><td>Monthly Return Heatmap</td>"
        "<td>每月首末观测净值计算的月度收益；绿色为正、红色为负。</td></tr>"
        "<tr><td>Transaction Costs</td>"
        "<td>拆分展示佣金、税费、过户费和滑点，纵轴单位为账户本币。</td></tr>"
        "<tr><td>Industry / Market-cap Exposure</td>"
        "<td>行业与市值暴露使用 Rust 账本的实际持仓市值，并把现金单列；"
        "每个时点各分类合计为 100%。分类标签来自策略输入元数据。</td></tr></table>"
        "</section>"
    )


def build_html_report(
    output: Path,
    *,
    identity: ReportIdentity,
    strategy: PerformanceSeries,
    benchmarks: tuple[BenchmarkSeries, ...],
    trade_summary: TradeSummary,
    exposures: tuple[ExposureObservation, ...],
    annual_sessions: int = 252,
) -> None:
    if annual_sessions <= 0:
        raise ValueError("annual sessions must be positive")
    if tuple(sorted(item.benchmark_id for item in benchmarks)) != identity.benchmark_ids:
        raise ValueError("report benchmark identities do not match")
    for benchmark in sorted(benchmarks, key=lambda item: item.benchmark_id):
        if benchmark.event_times != strategy.event_times:
            raise ValueError("report benchmark sessions must match strategy")
    compute_exposure_diagnostics(exposures)
    metrics = compute_strategy_metrics(strategy, annual_sessions=annual_sessions)

    performance = go.Figure()
    performance.add_scatter(
        x=strategy.event_times,
        y=_normalized(strategy.net_asset_values),
        name=identity.strategy_id,
    )
    for benchmark in benchmarks:
        performance.add_scatter(
            x=benchmark.event_times,
            y=_normalized(benchmark.close_values),
            name=benchmark.benchmark_id,
        )
    performance.update_layout(
        title="Cumulative Return vs Benchmarks", yaxis_tickformat=".1%"
    )

    running_peak = Decimal(0)
    drawdowns: list[float] = []
    for value in strategy.net_asset_values:
        running_peak = max(running_peak, value)
        drawdowns.append(float(value / running_peak - 1))
    drawdown = go.Figure(
        go.Scatter(
            x=strategy.event_times,
            y=drawdowns,
            fill="tozeroy",
            name="Drawdown",
        )
    )
    drawdown.update_layout(title="Drawdown", yaxis_tickformat=".1%")

    returns = metrics.session_returns
    rolling_window = min(20, len(returns))
    rolling_volatility: list[float | None] = [None]
    rolling_sharpe: list[float | None] = [None]
    for end in range(1, len(strategy.event_times)):
        sample = returns[max(0, end - rolling_window) : end]
        sample_stdev = statistics.stdev(sample) if len(sample) > 1 else 0.0
        rolling_volatility.append(
            sample_stdev * math.sqrt(annual_sessions) if len(sample) > 1 else None
        )
        rolling_sharpe.append(
            statistics.fmean(sample) / sample_stdev * math.sqrt(annual_sessions)
            if sample_stdev > 0
            else None
        )
    rolling_risk = go.Figure()
    rolling_risk.add_scatter(
        x=strategy.event_times,
        y=rolling_volatility,
        name=f"Rolling Volatility ({rolling_window})",
    )
    rolling_risk.add_scatter(
        x=strategy.event_times,
        y=rolling_sharpe,
        name=f"Rolling Sharpe ({rolling_window})",
        yaxis="y2",
    )
    rolling_risk.update_layout(
        title="Rolling Volatility and Sharpe",
        yaxis={"tickformat": ".1%"},
        yaxis2={"overlaying": "y", "side": "right"},
    )

    peak_time = strategy.event_times[0]
    peak_nav = strategy.net_asset_values[0]
    underwater_days: list[int] = []
    for event_time, nav in zip(
        strategy.event_times, strategy.net_asset_values, strict=True
    ):
        if nav >= peak_nav:
            peak_nav = nav
            peak_time = event_time
            underwater_days.append(0)
        else:
            underwater_days.append((event_time - peak_time).days)
    underwater = go.Figure(
        go.Scatter(
            x=strategy.event_times,
            y=underwater_days,
            fill="tozeroy",
            name="Underwater days",
        )
    )
    underwater.update_layout(title="Underwater Duration", yaxis_title="Days")

    month_end: dict[tuple[int, int], Decimal] = {}
    month_start: dict[tuple[int, int], Decimal] = {}
    for event_time, nav in zip(
        strategy.event_times, strategy.net_asset_values, strict=True
    ):
        key = (event_time.year, event_time.month)
        month_start.setdefault(key, nav)
        month_end[key] = nav
    years = sorted({year for year, _ in month_end})
    monthly_values = [
        [
            (
                float(month_end[(year, month)] / month_start[(year, month)] - 1)
                if (year, month) in month_end
                else None
            )
            for month in range(1, 13)
        ]
        for year in years
    ]
    monthly = go.Figure(
        go.Heatmap(
            x=[
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ],
            y=years,
            z=monthly_values,
            colorscale="RdYlGn",
            zmid=0,
            colorbar={"tickformat": ".1%"},
        )
    )
    monthly.update_layout(title="Monthly Return Heatmap")

    costs = go.Figure(
        go.Bar(
            x=["Commission", "Tax", "Transfer fee", "Slippage"],
            y=[
                float(trade_summary.commission),
                float(trade_summary.tax),
                float(trade_summary.transfer_fee),
                float(trade_summary.slippage),
            ],
            name="Transaction costs",
        )
    )
    costs.update_layout(title="Transaction Costs", yaxis_title="Currency units")

    industry = go.Figure()
    industry_names = sorted(
        {name for observation in exposures for name, _ in observation.industry_weights}
    )
    for name in industry_names:
        industry.add_scatter(
            x=[item.event_time for item in exposures],
            y=[float(dict(item.industry_weights).get(name, Decimal(0))) for item in exposures],
            name=name,
            stackgroup="industry",
        )
    industry.update_layout(title="Industry Exposure", yaxis_tickformat=".1%")

    market_cap = go.Figure()
    cap_names = sorted(
        {name for observation in exposures for name, _ in observation.market_cap_weights}
    )
    for name in cap_names:
        market_cap.add_scatter(
            x=[item.event_time for item in exposures],
            y=[float(dict(item.market_cap_weights).get(name, Decimal(0))) for item in exposures],
            name=name,
            stackgroup="market-cap",
        )
    market_cap.update_layout(title="Market-cap Exposure", yaxis_tickformat=".1%")

    input_summary = {
        "annual_sessions": annual_sessions,
        "strategy": {
            "event_times": [value.isoformat() for value in strategy.event_times],
            "net_asset_values": [str(value) for value in strategy.net_asset_values],
            "cash_values": [str(value) for value in strategy.cash_values],
            "market_values": [str(value) for value in strategy.market_values],
        },
        "benchmarks": [
            {
                "benchmark_id": item.benchmark_id,
                "event_times": [value.isoformat() for value in item.event_times],
                "close_values": [str(value) for value in item.close_values],
            }
            for item in benchmarks
        ],
        "trade_summary": {
            "gross_traded_notional": str(trade_summary.gross_traded_notional),
            "commission": str(trade_summary.commission),
            "tax": str(trade_summary.tax),
            "transfer_fee": str(trade_summary.transfer_fee),
            "slippage": str(trade_summary.slippage),
            "total_cost": str(trade_summary.total_cost),
            "average_nav": str(trade_summary.average_nav),
        },
        "exposures": [
            {
                "event_time": item.event_time.isoformat(),
                "industry_weights": [
                    [name, str(value)] for name, value in item.industry_weights
                ],
                "market_cap_weights": [
                    [name, str(value)] for name, value in item.market_cap_weights
                ],
            }
            for item in exposures
        ],
    }
    canonical_input = json.dumps(
        input_summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    manifest = json.dumps(
        {
            "run_id": identity.run_id,
            "strategy_id": identity.strategy_id,
            "snapshot_id": identity.snapshot_id,
            "benchmark_ids": identity.benchmark_ids,
            "input_sha256": hashlib.sha256(canonical_input.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    safe_manifest = (
        manifest.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    metrics_html = (
        "<h2>Strategy Metrics</h2><table><tr><th>Annualization periods</th><td>"
        f"{annual_sessions}</td></tr></table><table><tr><th>Total return</th><th>Annual return</th>"
        "<th>Annual volatility</th><th>Sharpe</th><th>Sortino</th><th>Calmar</th>"
        "<th>Max drawdown</th><th>Recovery days</th><th>Longest underwater</th>"
        "<th>VaR 95%</th><th>CVaR 95%</th><th>Turnover</th><th>Total costs</th></tr><tr>"
        f"<td>{metrics.total_return:.2%}</td>"
        f"<td>{metrics.annualized_return:.2%}</td>"
        f"<td>{metrics.annualized_volatility:.2%}</td>"
        f"<td>{metrics.sharpe_ratio if metrics.sharpe_ratio is not None else 'N/A'}</td>"
        f"<td>{metrics.sortino_ratio if metrics.sortino_ratio is not None else 'N/A'}</td>"
        f"<td>{metrics.calmar_ratio if metrics.calmar_ratio is not None else 'N/A'}</td>"
        f"<td>{metrics.maximum_drawdown:.2%}</td>"
        f"<td>{metrics.maximum_recovery_days if metrics.maximum_recovery_days is not None else 'N/A'}</td>"
        f"<td>{metrics.longest_underwater_days}</td>"
        f"<td>{metrics.value_at_risk_95:.2%}</td>"
        f"<td>{metrics.conditional_value_at_risk_95:.2%}</td>"
        f"<td>{trade_summary.turnover_rate:.2f}x</td>"
        f"<td>{trade_summary.total_cost}</td></tr></table>"
    )
    benchmark_rows = []
    for benchmark in sorted(benchmarks, key=lambda item: item.benchmark_id):
        item = compute_benchmark_metrics(
            strategy, benchmark, annual_sessions=annual_sessions
        )
        benchmark_rows.append(
            "<tr>"
            f"<td>{escape(item.benchmark_id)}</td><td>{item.tracking_error:.4f}</td>"
            f"<td>{item.information_ratio if item.information_ratio is not None else 'N/A'}</td>"
            f"<td>{item.beta if item.beta is not None else 'N/A'}</td>"
            f"<td>{item.alpha if item.alpha is not None else 'N/A'}</td>"
            f"<td>{item.correlation if item.correlation is not None else 'N/A'}</td></tr>"
        )
    metrics_html += (
        "<h2>Benchmark Diagnostics</h2><table><tr><th>Benchmark</th>"
        "<th>Tracking error</th><th>Information ratio</th><th>Beta</th>"
        "<th>Alpha</th><th>Correlation</th></tr>"
        + "".join(benchmark_rows)
        + "</table>"
    )
    metric_guide_html = _metric_guide_html(
        annual_sessions=annual_sessions,
        rolling_window=rolling_window,
    )
    sections = [
        _figure_html(
            performance, include_plotlyjs=True, div_id="trademaster-performance"
        ),
        _figure_html(drawdown, include_plotlyjs=False, div_id="trademaster-drawdown"),
        _figure_html(
            rolling_risk,
            include_plotlyjs=False,
            div_id="trademaster-rolling-risk",
        ),
        _figure_html(
            underwater, include_plotlyjs=False, div_id="trademaster-underwater"
        ),
        _figure_html(monthly, include_plotlyjs=False, div_id="trademaster-monthly"),
        _figure_html(costs, include_plotlyjs=False, div_id="trademaster-costs"),
        _figure_html(industry, include_plotlyjs=False, div_id="trademaster-industry"),
        _figure_html(
            market_cap, include_plotlyjs=False, div_id="trademaster-market-cap"
        ),
    ]
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>TradeMaster Report</title>"
        "<style>body{font-family:sans-serif;margin:24px}table{border-collapse:collapse}"
        "th,td{border:1px solid #ccc;padding:8px;vertical-align:top}"
        ".metric-guide{margin:24px 0;padding:18px;background:#f7f9fc;"
        "border:1px solid #d8dee9;border-radius:8px}.metric-guide table{width:100%;"
        "margin-bottom:18px}.metric-guide th{background:#edf2f7}</style></head><body>"
        f"<h1>Strategy Report: {escape(identity.strategy_id)}</h1>{metrics_html}{metric_guide_html}"
        + "".join(sections)
        + f"<script id='trademaster-report-manifest' type='application/json'>{safe_manifest}</script>"
        "</body></html>"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def compute_strategy_metrics(
    series: PerformanceSeries,
    *,
    annual_sessions: int = 252,
    risk_free_rate: float = 0.0,
) -> StrategyMetrics:
    if annual_sessions <= 0 or not math.isfinite(risk_free_rate):
        raise ValueError("annualization inputs are invalid")
    nav = [float(value) for value in series.net_asset_values]
    returns = tuple(current / previous - 1.0 for previous, current in pairwise(nav))
    count = len(returns)
    total_return = nav[-1] / nav[0] - 1.0
    annualized_return = (nav[-1] / nav[0]) ** (annual_sessions / count) - 1.0
    session_volatility = statistics.stdev(returns) if count > 1 else 0.0
    annualized_volatility = session_volatility * math.sqrt(annual_sessions)
    session_risk_free = (1.0 + risk_free_rate) ** (1.0 / annual_sessions) - 1.0
    mean_excess = statistics.fmean(returns) - session_risk_free
    sharpe = (
        mean_excess / session_volatility * math.sqrt(annual_sessions)
        if session_volatility > 0
        else None
    )
    downside = [min(value - session_risk_free, 0.0) for value in returns]
    downside_deviation = math.sqrt(statistics.fmean(value * value for value in downside))
    sortino = (
        mean_excess / downside_deviation * math.sqrt(annual_sessions)
        if downside_deviation > 0
        else None
    )

    peak_value = nav[0]
    peak_time = series.event_times[0]
    maximum_drawdown = 0.0
    maximum_peak_time: datetime | None = None
    maximum_trough_time: datetime | None = None
    maximum_peak_value: float | None = None
    underwater_start: datetime | None = None
    longest_underwater_days = 0
    for event_time, value in zip(series.event_times, nav, strict=True):
        if value >= peak_value:
            if underwater_start is not None:
                longest_underwater_days = max(
                    longest_underwater_days,
                    (event_time - underwater_start).days,
                )
                underwater_start = None
            peak_value = value
            peak_time = event_time
        else:
            if underwater_start is None:
                underwater_start = peak_time
            drawdown = value / peak_value - 1.0
            if drawdown < maximum_drawdown:
                maximum_drawdown = drawdown
                maximum_peak_time = peak_time
                maximum_trough_time = event_time
                maximum_peak_value = peak_value
    if underwater_start is not None:
        longest_underwater_days = max(
            longest_underwater_days,
            (series.event_times[-1] - underwater_start).days,
        )
    recovery_time: datetime | None = None
    if maximum_trough_time is not None and maximum_peak_value is not None:
        for event_time, value in zip(series.event_times, nav, strict=True):
            if event_time > maximum_trough_time and value >= maximum_peak_value:
                recovery_time = event_time
                break
    recovery_days = (
        (recovery_time - maximum_peak_time).days
        if recovery_time is not None and maximum_peak_time is not None
        else None
    )
    calmar = (
        annualized_return / abs(maximum_drawdown)
        if maximum_drawdown < 0
        else None
    )
    ordered_returns = sorted(returns)
    var_index = max(0, math.ceil(0.05 * len(ordered_returns)) - 1)
    value_at_risk = ordered_returns[var_index]
    tail = [value for value in ordered_returns if value <= value_at_risk]
    return StrategyMetrics(
        session_returns=returns,
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        maximum_drawdown=maximum_drawdown,
        drawdown_peak_time=maximum_peak_time,
        drawdown_trough_time=maximum_trough_time,
        drawdown_recovery_time=recovery_time,
        maximum_recovery_days=recovery_days,
        longest_underwater_days=longest_underwater_days,
        positive_session_ratio=sum(value > 0 for value in returns) / len(returns),
        best_session_return=max(returns),
        worst_session_return=min(returns),
        value_at_risk_95=value_at_risk,
        conditional_value_at_risk_95=statistics.fmean(tail),
    )


__all__ = [
    "BenchmarkMetrics",
    "BenchmarkSeries",
    "ExposureDiagnostics",
    "ExposureObservation",
    "PerformanceSeries",
    "ReportIdentity",
    "StrategyMetrics",
    "TradeSummary",
    "build_html_report",
    "compute_benchmark_metrics",
    "compute_exposure_diagnostics",
    "compute_strategy_metrics",
]
