# M5: Performance Metrics, Exposure Diagnostics, and Standalone Reports

**Status:** Completed | **Date:** 2026-08-11 | **Updated:** 2026-11-05 | **Scope:** Strategy and benchmark metrics, exposure diagnostics, and standalone Plotly HTML reports (`trademaster.reporting`, `trademaster.e2e`)

## Motivation and Scope

Milestone M5 transforms persisted Rust run traces, benchmark index series, and classification exposures into reproducible performance summaries and self-contained offline HTML reports. Missing trade observations, undefined statistical ratios, and exact benchmark index identities are surfaced explicitly rather than interpolated or proxied.

## Design and Public Interfaces

- **`PerformanceSeries`:** Holds strictly increasing UTC valuation timestamps alongside non-negative cash, market value, and positive net asset value (`cash + market_value == NAV`).
- **`BenchmarkSeries`:** Identifies each benchmark index alongside its UTC session timestamps and closing levels. Strategy-to-benchmark comparisons align strictly on shared trading sessions without forward-filling missing dates or substituting proxy indices.
- **`TradeSummary`:** Aggregates traded notional, brokerage commissions, stamp duty, transfer fees, slippage, and the average-NAV turnover denominator (preserved even when a run executes zero fills).
- **`ExposureObservation`:** Records per-session industry taxonomy/label, market-cap bucket, and portfolio weight summing to `1.0` across held assets and cash.
- **`StrategyMetrics`:** Computes total and annualized return, annualized volatility, Sharpe, Sortino, and Calmar ratios, maximum drawdown with peak/trough/recovery timestamps, longest underwater duration, positive-session hit rate, best and worst single-session returns, historical VaR/CVaR, benchmark tracking error, information ratio, beta, alpha, turnover, total trading costs, and cost-to-notional ratio. Undefined ratios (such as zero-volatility Sharpe, zero-variance beta/alpha/correlation, or unrecovered drawdown recovery time) return `None`.

### Standalone HTML Report and Monthly Return Basis

Reports bundle Plotly.js inline for offline viewing and render cumulative return curves against any number of benchmarks, drawdown and days-underwater charts, rolling volatility and Sharpe series, a monthly return heatmap, stacked industry and size exposures, turnover and cost breakdowns, a metrics summary table, and a run manifest (`run_id`, `snapshot_id`, `strategy_id`, benchmarks, and SHA-256 input digest).

Each calendar month's return equals that month's last observed closing NAV divided by the *previous* calendar month's last observed closing NAV, minus one (`monthly_return_basis: previous_month_close` in `report-input.json`):

- The initial month is measured from the first observed NAV, and the final month ends at the last available close even when the sample ends mid-month.
- Any calendar month with zero observations—and the month immediately following that gap—reports no monthly return (`None`).
- When replaying older archived event bundles that omit `monthly_return_basis`, the report builder selects the legacy within-month calculation, chart guide, and input digest so archived HTML outputs reproduce byte for byte.

## Verification and Regression Record

| Verification Stage | Verified Behavior |
|---|---|
| Core metrics & reports (`test_reporting_metrics.py`, `test_reporting_report.py`) | Matched hand-calculated return, volatility, Sharpe, drawdown, recovery, underwater duration, benchmark alignment, tracking error, beta/alpha, turnover, exposure sums (`1.0`), and offline multi-benchmark HTML output (5 initial suites). |
| Independent review & fixes | Resolved 4 High findings (0 Critical): enforced `cash + market_value == NAV` on `PerformanceSeries`; preserved zero-fill cost breakdowns and average-NAV denominators; returned `None` for undefined beta, alpha, and correlation on short or constant-variance series; and required the HTML builder to recompute `StrategyMetrics` from inputs and embed a deterministic input SHA-256. |
| Milestone closure (2026-08-11) | 6 focused reporting suites and 128 total Python tests passed (including live Tushare smoke test), alongside Ruff, strict `mypy` (30 source files), wheel/sdist builds, 39 Rust workspace tests, doc-tests, `cargo fmt --check`, `cargo clippy -D warnings`, and credential scans (0 matches). |
| Cross-month boundary update (2026-11-05) | Updated the [reporter](../python/trademaster/reporting/__init__.py) to anchor monthly returns on the prior month's close (fixing e.g. a `110 -> 121 -> 133.1` boundary sequence to report `21%` rather than `10%`), persisted `monthly_return_basis` in the [event bundle](../python/trademaster/e2e/__init__.py), and verified partial first months, year boundaries, single-observation months, and missing-month gaps in [heatmap tests](../tests/test_reporting_report.py) and [bundle tests](../tests/test_e2e_orchestrator.py) (including SHA-256 reproduction of unversioned archives). On the 2024 weekly momentum ledger (`CNY 107,650.01` on March 29, `CNY 109,945.27` on April 1, `CNY 105,129.30` on April 30), April's return is `-2.34%` (versus `-4.38%` under the legacy April 1 baseline), while all three archived Tushare demo bundles reproduced their saved HTML bytes across 14 passing reporting, orchestrator, and demo tests. |
