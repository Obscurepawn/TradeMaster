# Two-Stage Industry Selection Study: Top Ten Industries, One Stock Each

**Status:** Completed | **Date:** 2026-08-12 | **Scope:** Market-wide industry mean filter (`top_industries_by_mean_score=10`) with within-industry stock selection (`trademaster.strategies.industry_fundamental_top5`) | **Builds-on:** [Industry Concentration Study: One Stock vs. Five Stocks per Industry](20260812-industry-fundamental-top1-experiment.md) | **Updated-by:** [M7: Managed Factor Registry, Materialization Store, and Strategy Refactor](20260823-m7-factor-management-strategy-refactor.md), [M9: Twenty-Year All-A-Share Factor Research Pipeline and Quality Audit](20260823-m9-full-a-20y-factor-evaluation-quality.md)

## Motivation and Two-Stage Ranking Design

This experiment adds an optional top-down industry filter (`top_industries_by_mean_score=10`, defaulting to `None` to preserve the all-industry baseline) to the ten-year `top_per_industry=1` CSI 300 strategy:

1. **Cross-market industry ranking:** Because within-industry z-scores average near zero inside every industry, cross-industry comparison recomputes the nine fundamental indicators' winsorized (5%/95%) z-scores across *all eligible stocks market-wide* using the same indicator weights. Each industry's score is the equal-weighted mean of its eligible members' market-wide composite scores, sorted deterministically by `(-industry_mean_score, industry_id)` to retain the top ten industries.
2. **Within-industry stock selection:** Inside each of the ten selected industries, the selector chooses the single stock with the highest *within-industry* composite score and assigns exact equal target weights (`0.1` per stock, summing to `1.00000000`).

All data inputs, point-in-time rules, `CNY 500,000` initial capital, transaction costs, slippage, and Rust execution constraints match the all-industry Top-1 baseline. *(Note: [M7](20260823-m7-factor-management-strategy-refactor.md) F-12 subsequently corrected signal-ID ordering in small-account board-lot allocation, changing the ten-industry strategy's corrected total return to `47.16%`, and [M9](20260823-m9-full-a-20y-factor-evaluation-quality.md) documented the historical `SW2021` interval gaps affecting both runs.)*

## Results and Verification Record

| Strategy Variant | Total Return | Annualized Return | Volatility | Sharpe | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| Top 10 industries, 1 stock each | 54.16% | 4.50% | 17.92% | 0.335 | -40.63% |
| All industries, 1 stock each | 108.39% | 7.76% | 16.69% | 0.531 | -27.64% |

Concentrating the portfolio into ten industries reduced return and deepened maximum drawdown relative to the diversified all-industry Top-1 portfolio.

- **Selection and replay audit:** Across all 20 rebalance dates (200 total selections), automated checks verified exactly 10 industries per date, 1 stock per industry, industry ranks `1..10`, target weights of `0.1`, and exact weight sums of `1.0` with zero violations. Both the initial run and clean replay made `0` provider calls and produced matching SHA-256 digests across request (`a076c7d6...abbe4`), result (`b9e7b6dd...c0f56`), report input (`0db6376b...dbe5d`), and standalone HTML (`0cbaf2fd...3f1ae`).
- **Repository checks & independent review:** Python passed 144 tests alongside Ruff lint/format checks on all modified files (leaving 30 pre-existing untouched files unchanged), strict `mypy`, `cargo fmt --check`, `cargo clippy`, and Rust workspace tests. An independent review reproduced the market-wide industry means, selections, artifact digests, and performance comparison with zero Critical or High findings.

## Changed Files

- `specs/INDEX.md`, `specs/20260812-industry-fundamental-top10-industries.md`
- `python/trademaster/strategies/industry_fundamental_top5/__init__.py`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `tests/test_industry_fundamental_top5.py`
- `artifacts/strategies/industry-fundamental-top10-industries-top1-10year-final/`
- `artifacts/strategies/industry-fundamental-top10-industries-top1-10year-final-replay/`
