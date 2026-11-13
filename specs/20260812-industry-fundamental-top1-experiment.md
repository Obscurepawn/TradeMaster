# Industry Concentration Study: One Stock vs. Five Stocks per Industry

**Status:** Completed | **Date:** 2026-08-12 | **Scope:** Controlled single-variable comparison (`top_per_industry=1` vs. `5`) on the ten-year CSI 300 cache (`trademaster.strategies.industry_fundamental_top5`) | **Builds-on:** [Semiannual Industry-Neutral Fundamental Strategy (Top 5 per Industry)](20260812-industry-fundamental-top5.md) | **Updated-by:** [M7: Managed Factor Registry, Materialization Store, and Strategy Refactor](20260823-m7-factor-management-strategy-refactor.md), [M9: Twenty-Year All-A-Share Factor Research Pipeline and Quality Audit](20260823-m9-full-a-20y-factor-evaluation-quality.md)

## Motivation and Experimental Design

This study isolates the effect of within-industry concentration by changing a single parameter—`RealStrategyConfig(top_per_industry=1)` versus `5`—while holding every other modeling assumption constant without post-hoc parameter tuning:

- **Shared inputs and execution rules:** Reuses the exact `2016-08-12` to `2026-08-11` Tushare Parquet/DuckDB cache, 20 semiannual rebalance dates, `CNY 500,000` starting cash, nine fundamental indicators and weights, 5%/95% within-industry winsorization and z-scores, `update_flag=0` financial filing filter, static `SW2021` baseline mapping, and Rust execution rules (100-share board lots, T+1 settlement, price limits, fees, slippage, and 20-day exit retries).
- **Separate output provenance:** `top_per_industry` parameterizes selection logic, deterministic `strategy_id`/`run_id` hashes, report titles, and output directories (`artifacts/strategies/industry-fundamental-top1-10year-final/`), preserving the Top-5 baseline artifacts intact.
- **Report documentation:** Standalone HTML reports include an embedded metrics and chart guide specifying the 52-week annualization convention, zero risk-free rate, single-period VaR/CVaR definitions, cumulative full-study turnover, and ledger-derived exposures.

*(Note: See the [M7](20260823-m7-factor-management-strategy-refactor.md) and [M9](20260823-m9-full-a-20y-factor-evaluation-quality.md) audit notice regarding static `SW2021` industry classifications and continuous adjusted-price execution approximations in these diagnostic runs.)*

## Results and Verification Record

| Strategy Variant | Total Return | Annualized Return | Volatility | Sharpe | Max Drawdown | Cumulative Turnover |
|---|---:|---:|---:|---:|---:|---:|
| Top 1 per industry (`top_per_industry=1`) | 108.39% | 7.76% | 16.69% | 0.531 | -27.64% | 22.33x |
| Top 5 per industry (`top_per_industry=5`) | 81.04% | 6.23% | 13.64% | 0.511 | -27.43% | 12.44x |

| Verification Area | Outcome |
|---|---|
| Parameterized `top_per_industry` & HTML guide | 11 focused strategy tests and updated HTML report tests passed alongside Ruff and strict `mypy`. |
| Selection invariants & cache replay | Produced 560 selections across 20 rebalances (at most 1 stock per industry per date, exact `1.0` weight sums). Both the initial run and clean replay made `0` provider calls and reproduced identical request, result, report-input, and HTML SHA-256 digests (`e2d68e8bc5b2d8c3af362ed51854e0743af4ac1d37d3b8ed693669ef7e0dd1b9`), summarized in `top1-vs-top5-comparison.md`. |
| Repository checks & independent review | 143 Python tests, `cargo fmt --check`, `cargo clippy`, and Rust workspace tests passed; an independent review confirmed all 20 rebalance cross-sections, rank=1 bounds, weight sums, and comparison metrics with zero Critical or High findings. |

## Changed Files

- `specs/INDEX.md`, `specs/20260812-industry-fundamental-top1-experiment.md`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `python/trademaster/reporting/__init__.py`
- `tests/test_industry_fundamental_top5.py`, `tests/test_reporting_report.py`
- `artifacts/strategies/industry-fundamental-top1-10year-final/`, `artifacts/strategies/industry-fundamental-top1-10year-final-replay/`
