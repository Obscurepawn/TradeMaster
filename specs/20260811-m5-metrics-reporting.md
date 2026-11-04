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

Each month’s return is its last observed closing NAV divided by the previous calendar month’s last observed closing NAV, minus one. The first month starts at the first observed NAV. The last month ends at the last available close, even when the sample ends mid-month. A month with no observations and the month immediately after it have no return value. New event reports record `monthly_return_basis: previous_month_close` in `report-input.json`. When the field is absent, replay uses the archived within-month calculation, chart guide and input digest to reproduce the original HTML.

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

### Monthly returns across a month boundary

- The HTML regression initially reported a 10% January gain from 121 to 133.1. Including the move from December’s last close of 110 gives 21%. The new bundle test also failed because the report inputs omitted the calculation basis.
- The [reporter](../python/trademaster/reporting/__init__.py) now uses the previous month’s close. The [event bundle](../python/trademaster/e2e/__init__.py) saves the basis and selects it during replay. The [heatmap test](../tests/test_reporting_report.py) covers a partial first month, a year boundary, a single-observation month, a missing month and the resumption of returns. The [bundle tests](../tests/test_e2e_orchestrator.py) rebuild both formats, including an unversioned two-month report checked against its original SHA-256.
- The weekly momentum event ledger closed at CNY 107,650.01 on March 29, 2024, CNY 109,945.27 on April 1 and CNY 105,129.30 on April 30. April’s return is −2.34%. The archived report used April 1 as its baseline and showed −4.38%. All three original TuShare demo bundles reproduced their saved HTML bytes. The 14 reporting, orchestrator and demo runner tests passed.
