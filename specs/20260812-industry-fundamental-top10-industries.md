# Ten-year experiment selecting one stock in each of ten industries

Status: complete
Owner: Codex
Updated: 2026-08-12
Open questions: none

## Experiment and ranking rules

Apply an industry filter to the existing ten-year Tushare backtest of one stock per industry. At each of the 20 semiannual rebalances, select the ten industries with the highest average fundamental scores. Within each industry, select the stock with the highest original industry-neutral score, then equally weight all selected stocks.

To select a stock, winsorize and standardize the nine indicators within its industry, then apply the original Top1 weights. Those scores average near zero within each industry, so they cannot distinguish industries.

To rank an industry, recompute the nine indicators' standardized scores across *all eligible stocks*, using the same winsorization and weights. Average the resulting fundamental scores for its members. Each stock counts once, regardless of market capitalization.

Sort by `(-industry_mean_score, industry_id)` and take the first ten. The usual target portfolio is ten equally weighted stocks. Keep the all-industry Top1 baseline's data, PIT rules, costs, slippage, Rust trading constraints, starting cash of 500,000 CNY and reporting conventions.

## Implementation and acceptance

| Task | Owner | Status | Evidence |
|---|---|---|---|
| Define the public industry-filter option | Codex | complete | Strategy and configuration regression checks |
| Connect the CLI, run identity, outputs and report | Codex | complete | Configuration, selections, summary and report |
| Run ten years of real data and replay | Codex | complete | Zero provider calls and four matching hashes |
| Compare with all-industry Top1 and run final checks | Codex | complete | Comparison, test suites and passing independent review |

- [x] Validate `top_industries_by_mean_score=10`. The default `None` preserves the baseline.
- [x] Tests confirm the market-wide means, deterministic ordering and exact equal weights.
- [x] All 20 real selections contain ten industries, one stock per industry and an exact weight sum of one.
- [x] Both runs use the cache without a provider call and reproduce the key hashes.
- [x] Produce HTML, Markdown, JSON and the all-industry Top1 comparison.
- [x] Pass the Python and Rust checks and one independent review of the specified scope.

## Results and verification

| Strategy | Total return | Annualized return | Volatility | Sharpe | Maximum drawdown |
|---|---:|---:|---:|---:|---:|
| Ten industries, Top1 | 54.16% | 4.50% | 17.92% | 0.335 | -40.63% |
| All industries, Top1 | 108.39% | 7.76% | 16.69% | 0.531 | -27.64% |

The industry filter reduced return and increased drawdown in this experiment. Across 20 dates and 200 selections, the audit found ten industries per date, one stock per industry, industry ranks 1–10, a weight of 0.1 per stock and an exact weight sum of one. There were zero violations.


The first real run and clean replay both made zero provider calls. Their request `a076c7d6…abbe4`, result `b9e7b6dd…c0f56`, report input `0db6376b…dbe5d` and HTML `0cbaf2fd…3f1ae` hashes matched.

Python reported `144 passed`. Ruff check, mypy, Cargo fmt, Clippy and Rust workspace tests passed. The four changed Python files passed Ruff's format check. A separate repository-wide format check found 30 files with pre-existing formatting differences. They were left untouched.

The independent review passed with no Critical or High issues in scope. It reproduced the scores, selections, artifact hashes, performance table and industry concentration.

## Changed files

- `specs/INDEX.md`
- `specs/20260812-industry-fundamental-top10-industries.md`
- `python/trademaster/strategies/industry_fundamental_top5/__init__.py`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `tests/test_industry_fundamental_top5.py`
- `artifacts/strategies/industry-fundamental-top10-industries-top1-10year-final/`
- `artifacts/strategies/industry-fundamental-top10-industries-top1-10year-final-replay/`
