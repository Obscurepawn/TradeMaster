# Ten-year comparison of one stock vs. five per industry

Status: complete
Owner: Codex
Updated: 2026-08-12
Open questions: none

## Experiment

Change `top_per_industry` from 5 to 1 in the Top5 backtest. Hold the real data, scoring, point-in-time rules, rebalance dates, costs, Rust trading rules and reporting conventions constant. Retain the Top5 artifacts and write Top1 to separate output directories. The user chose a single-variable comparison with no changes to factor weights or other parameters after seeing the results.

The study ran from 2016-08-12 to 2026-08-11 with 20 semiannual rebalances and 500,000 CNY starting cash. It selected the highest-scoring stock in each eligible SW2021 level-one industry and equally weighted all selections across industries. The nine indicators, weights, winsorization, standardization, missing-data treatment, original financial statement versions and static industry mapping all followed Top5. Rust continued to enforce 100-share lots, T+1, price limits, fees, slippage and exit retries. Both runs used the same Tushare Parquet/DuckDB cache.

## Implementation and acceptance

| Task | Owner | Status | Evidence |
|---|---|---|---|
| Make TopN configurable | Codex | complete | Initially an unknown config argument, then 11 focused tests passed |
| Run Top1 on the real cache and replay | Codex | complete | Zero provider calls and four matching SHA hashes |
| Compare Top1 and Top5 | Codex | complete | `top1-vs-top5-comparison.md` |
| Run checks and one independent review | Codex | complete | 143 Python tests, Rust checks and a passing review |

- [x] `RealStrategyConfig(top_per_industry=1)` controls selection, the strategy and run identities, and the report title.
- [x] All 20 rebalances contained at most one stock per industry, with target weights summing to exactly one in each period.
- [x] The real cache fed the Rust runtime, and a clean replay made zero provider calls and reproduced the key hashes.
- [x] The comparison covered total and annualized returns, volatility, Sharpe, maximum drawdown, time to recovery, turnover, costs, cash and residual holdings.
- [x] The relevant tests, Ruff, mypy and Rust checks passed. One independent review covered the specified scope.

The HTML includes a Chinese guide to its metrics and charts. It explains 52 periods per year, a zero risk-free rate, single-period VaR/CVaR, turnover accumulated over the full study and exposures measured from the actual ledger.

## Results and verification

| Strategy | Total return | Annualized return | Volatility | Sharpe | Maximum drawdown | Turnover |
|---|---:|---:|---:|---:|---:|---:|
| Top1 | 108.39% | 7.76% | 16.69% | 0.531 | -27.64% | 22.33x |
| Top5 | 81.04% | 6.23% | 13.64% | 0.511 | -27.43% | 12.44x |

Across 20 dates, Top1 produced 560 selections. The first and replay runs made zero provider calls. Each reproduced the runner request, runner result, report input and HTML hashes. The updated report SHA-256 was `e2d68e8bc5b2d8c3af362ed51854e0743af4ac1d37d3b8ed693669ef7e0dd1b9`.


The new TopN configuration first failed as an unexpected keyword and then passed 11 focused tests. The report test first failed because the Chinese metrics-and-charts guide was missing. The focused test, Ruff and mypy passed once the guide was added. Final checks reported `143 passed` in Python, with Ruff, mypy, Cargo fmt, Clippy and workspace tests passing. The independent review found no Critical or High issues in scope. It confirmed the 20 dates, 560 selections, industry rank of 1, exact weight sums and comparison metrics.

## Changed files

- `specs/INDEX.md`
- `specs/20260812-industry-fundamental-top1-experiment.md`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `python/trademaster/reporting/__init__.py`
- `tests/test_industry_fundamental_top5.py`
- `tests/test_reporting_report.py`
- `artifacts/strategies/industry-fundamental-top1-10year-final/`
- `artifacts/strategies/industry-fundamental-top1-10year-final-replay/`
