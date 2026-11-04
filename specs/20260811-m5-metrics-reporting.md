# M5 performance metrics and reports

Status: completed
Owner: Codex

## Scope and interfaces

M5 reads the saved run records, benchmark series, and exposure observations. It produces reproducible metrics and a standalone Plotly HTML report. The report identifies missing trade data and names the actual benchmark index used.

- `PerformanceSeries` holds strictly increasing UTC timestamps, positive NAV, cash, and market value.
- `BenchmarkSeries` identifies a benchmark and its UTC timestamps and closes. Comparison uses the trading sessions shared by the benchmark and the strategy.
- `TradeSummary` records traded notional, commission, tax, transfer fees, slippage, and the denominator used for turnover.
- `ExposureObservation` records the session, industry taxonomy and label, market-cap bucket, and portfolio weight.
- `StrategyMetrics` includes total and annualized return, annualized volatility, Sharpe, Sortino, Calmar, maximum drawdown and its peak, trough and recovery times, longest period underwater, positive-return share, best and worst sessions, VaR/CVaR, tracking error, information ratio, beta, alpha, turnover, total costs, and costs as a fraction of traded notional.

## Reporting

Show normalized cumulative returns for the strategy and any number of user-selected benchmarks on one chart. Separate plots show drawdown, days underwater, rolling volatility and Sharpe, and monthly returns as a heatmap. The report also includes stacked industry and market-cap exposures, turnover and costs, and a table of the main metrics. Embed Plotly JS for offline viewing. Give each chart a title and units, and distinguish missing observations from undefined metrics. The manifest identifies the `run_id`, `snapshot_id`, `strategy_id`, benchmarks, and input digests.

## Test and implementation sequence

1. Red. Hand-calculate returns, volatility, Sharpe ratio, maximum drawdown, time to recovery, and time underwater from a NAV series.
2. Green. Implement performance metrics as pure functions.
3. Red. Test benchmark alignment, tracking error, information ratio, beta, alpha, turnover, and costs.
4. Green. Implement benchmark and trade metrics.
5. Red. Require industry and market-cap weights to sum to 1. Test unknown buckets and duplicate observations. Require the HTML to include multiple benchmarks, drawdown, exposures, and the manifest.
6. Green. Implement exposure diagnostics and the standalone Plotly report.
7. Integrate using real Parquet files in run format and conduct one independent milestone review.

## Acceptance criteria

Match hand-calculated results field by field. Return `None` for undefined values, including ratios with zero volatility and a recovery time when a drawdown has never recovered. When the calendars differ, require an explicit choice to use only their common sessions. Do not fill missing observations with earlier values or substitute a proxy index. The HTML must open offline as one file and identify input data and units. Required checks included full Python tests, Ruff, strict mypy, wheel/sdist builds, and Rust regression tests. The full system review was scheduled after M6 end-to-end testing.

## Implementation and verification record

- M0 through M4 were complete. Red 1 failed to collect `test_reporting_metrics.py` because the reporting module did not exist. After implementation, 3 tests covered hand-calculated performance, drawdown, recovery, undefined ratios, turnover, and costs.
- Red 2 failed to collect `test_reporting_report.py` because interfaces including `BenchmarkSeries` were missing. After implementation, 2 tests covered strict benchmark alignment, tracking and regression metrics, exposures, and standalone HTML.
- The first focused run passed 5 tests. One independent review found 0 Critical and 4 High issues involving NAV components, costs when there are no fills, benchmark metrics with short samples or zero variance, and verification of the metrics and manifest embedded in HTML.
- Failing regression tests led to enforcing `cash + market_value = NAV`, preserving individual cost components and average NAV, and returning `None` for undefined beta, alpha, or correlation. The HTML builder recalculates strategy metrics and records a deterministic input SHA-256. No further local review was held.
- Final checks passed for 6 focused tests and 128 Python tests, including the real Tushare smoke test. Ruff, strict mypy (30 source files), wheel/sdist builds, Rust formatting, Clippy, 39 Rust tests, doc-tests, the diff check, and the scan for literal credentials passed. M5 was complete.
