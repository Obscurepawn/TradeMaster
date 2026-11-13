# Semiannual Industry-Neutral Fundamental Strategy (Top 5 per Industry)

**Status:** Completed | **Date:** 2026-08-12 | **Scope:** Ten-year CSI 300 semiannual fundamental selection, target-weight execution, and bundle replay (`trademaster.strategies.industry_fundamental_top5`) | **Updated-by:** [M7: Managed Factor Registry, Materialization Store, and Strategy Refactor](20260823-m7-factor-management-strategy-refactor.md), [M9: Twenty-Year All-A-Share Factor Research Pipeline and Quality Audit](20260823-m9-full-a-20y-factor-evaluation-quality.md)

## Motivation and Strategy Design

This specification defines a standalone ten-year semiannual A-share fundamental strategy that ranks constituents within each Shenwan (`SW2021`) level-one industry, selects up to five stocks per industry, assigns equal portfolio target weights across all selected names, executes through the Rust event loop, and emits a self-verifying M6 report bundle.

- **Universe:** Latest historical CSI 300 constituent snapshot (`index_weight`) available in each rebalance month, while keeping `all_a_share` configurable. With `CNY 500,000` initial capital and 100-share board lots, an all-A-share Top-5 portfolio (>140 stocks) would leave many positions below one tradable lot; CSI 300 keeps target positions sized above board-lot minimums while preserving universe-agnostic selector interfaces.
- **Rebalance schedule:** Runs from `2016-08-12` through `2026-08-11` across 20 semiannual rebalances. Signals form after the close of the first trading session on or after May 6 and November 6 and become eligible no earlier than the next trading session's open (`T+1`).
- **Capital, sizing, and exit retries:** Starts with `CNY 500,000` cash. At the eligible open, Rust converts target weights into integer share quantities from current NAV, opening prices, and 100-share lots—processing sells before buys—and leaves sub-lot cash uninvested. Zero-weight exits for dropped constituents retry across up to 20 trading opens when blocked by suspensions or lower price limits.
- **Benchmark and cost schedule:** Benchmarks against the CSI 300 price index (`000300.SH`) under 3 bp brokerage commission (`CNY 5` minimum), 5 bp sell-side stamp duty, 0.1 bp SSE stock transfer fee, and 5 bp slippage. NAV and benchmark series are sampled on each calendar week's final trading day (annualized at 52 periods), while the complete daily trading calendar drives Rust next-open eligibility, exit retries, and T+1 lot settlement.

## Cross-Sectional Scoring Rules

Within each `SW2021` level-one industry, each raw indicator is winsorized at the 5th and 95th percentiles and standardized into a cross-sectional z-score. Missing indicator values receive the industry-neutral score `0.0` so financial issuers lacking `grossprofit_margin` or `ocf_to_or` incur no cross-industry penalty. Eligible candidates require `pe_ttm > 0`, `pb > 0`, and at least five valid indicators (with valid-field counts persisted in selection artifacts):

| Factor Group | Indicators and Weights | Group Weight |
|---|---|---:|
| Value | Earnings yield `1 / pe_ttm` (15%), book yield `1 / pb` (10%), dividend yield `dv_ttm` (10%) | 35% |
| Quality | `roe` (15%), `grossprofit_margin` (10%), operating cash flow to revenue `ocf_to_or` (10%) | 35% |
| Growth | YoY quarterly revenue growth `q_sales_yoy` (10%), YoY quarterly profit growth `q_profit_yoy` (10%) | 20% |
| Safety | Inverse leverage `-debt_to_assets` (10%) | 10% |

Candidates are ranked by descending composite score and ascending `instrument_id`, selecting `min(5, eligible_count)` stocks per industry with exact `Decimal` equal weights summing to `1.00000000`.

## Point-in-Time Rules and Modeling Limitations

1. **Financial restatement isolation:** Financial statements require `ann_date < signal_date`. Because Tushare Pro does not expose point-in-time publication timestamps for restated filings, selection restricts `fina_indicator` to original filings (`update_flag = 0`) and excludes restatements (`update_flag = 1`).
2. **Prior-session valuation multiples:** Valuation fields from `daily_basic` are read from the last trading session strictly before the signal date so post-close publication lag cannot leak into intraday/close selection.
3. **Static industry classification in baseline run:** Because a live Tushare `index_member_all` probe returned current (`is_new=Y`) rows and zero historical (`is_new=N`) rows under the initial query path, this baseline applied a single `SW2021` mapping across the sample and disclosed classification survivorship bias in the report. *(Note: [M9](20260823-m9-full-a-20y-factor-evaluation-quality.md) subsequently fixed `in_date`/`out_date` interval resolution and barred static industry backfills from formal PIT strategies.)*
4. **Continuous adjusted-price approximation:** Stock OHLC and price limits are normalized to period end via `adj_factor` to prevent artificial price drops across splits and distributions; executing against continuous backward-adjusted prices approximates corporate actions without discrete dividend receivable postings.

## Implementation and Verification Record

| Subtask | Status | Verification Evidence |
|---|---|---|
| Within-industry scoring, selection, and equal weighting | Completed | 3 hand-checked Python unit suites |
| Scheduled target-weight execution and exit retries in Rust | Completed | 5 `tm-engine` and 3 `tm-runner` wire/execution tests |
| Content-addressed Tushare Parquet cache and DuckDB catalog | Completed | 1,959 cached Parquet objects verified across endpoints |
| Ten-year backtest execution and clean-process replay | Completed | 20 rebalances, 0 provider calls on replay, byte-identical digests |
| Full repository verification | Completed | 142 Python tests (0 skipped), Ruff, strict `mypy`, Cargo checks, packaging |

An independent review prior to finalizing the study identified three High-severity defects in the initial run: restated financial filings (`update_flag=1`) leaking into historical cross-sections, inconsistent application of the static industry table, and single-day exit orders stranding positions after temporary trading halts. Fixing all three (enforcing `update_flag={'0'}`, applying one deterministic static mapping, and adding 20-session zero-target exit retries) established the final numbers below:

- **Portfolio and execution trace:** 2,335 selection rows across 20 rebalances (`financial_update_flag={'0'}`, 0 future announcements, 0 Top-5 or weight-sum violations), 426 distinct stocks, 2,145 fills, and 383 rejected exit-retry orders under suspension or limit-down rules. One delisting/suspended holding (`601989.SH`) remained at sample end (`0.91%` of ending NAV).
- **Economic summary:** Total return `81.04%` (vs. `41.57%` CSI 300 price return), annualized return `6.23%`, annualized volatility `13.64%`, Sharpe `0.511`, maximum drawdown `-27.43%`, ending cash `21.06%` of NAV, and cumulative trading costs `CNY 16,813.29`.
- **Cache and deterministic replay:** Local cache stored 1,959 request objects (`fina_indicator=611`, `daily=446`, `adj_factor=446`, `daily_basic=20`, `index_weight=20`, `stk_limit=381`, plus calendar, industry, and benchmark objects). Replaying in a fresh process with an invalid token made `0` provider calls and reproduced identical SHA-256 digests across the runner request (`55202d...2d84`), runner result (`2d4a75...9346`), report inputs, and standalone HTML (`195931...a2de`).
- **Suite checks:** `uv run pytest -q` passed 142 tests (0 skipped, using an external `0600` token file with 0 repository credential matches), alongside Ruff, strict `mypy`, `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace`, `uv build`, and `cargo package --workspace --allow-dirty --no-verify`.

## Changed Files

- `specs/INDEX.md`, `specs/20260812-industry-fundamental-top5.md`
- `python/trademaster/strategies/AGENTS.md`, `python/trademaster/strategies/industry_fundamental_top5/`
- `python/trademaster/e2e/__init__.py`, `python/trademaster/reporting/__init__.py`
- `crates/tm-engine/src/lib.rs`, `crates/tm-engine/tests/scheduled_strategy.rs`
- `crates/tm-runner/src/lib.rs`, `crates/tm-runner/tests/wire.rs`
- `tests/test_industry_fundamental_top5.py`, `tests/test_fundamental_strategy_data_cache.py`, `tests/test_reporting_report.py`
