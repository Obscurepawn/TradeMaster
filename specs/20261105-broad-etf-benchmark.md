# Broad ETF Execution and Cash-Distribution Benchmark

**Status:** Completed | **Date:** 2026-11-05 | **Scope:** Fifteen-year SSE 50 index signal, ETF execution (`510050.SH`), and cash-dividend ledger benchmark (`trademaster.e2e.broad_etf_data`, `tm-engine`) | **Builds-on:** [Fifteen-Year Single-Stock Execution Benchmark](20261105-long-history-benchmark.md)

## Motivation and Workload Design

This specification introduces the primary fifteen-year README benchmark demonstrating how a close-time index signal translates into next-open ETF orders, fills, and exact cash-dividend accounting across **3,644 Shanghai sessions** (`2011-01-04` through `2025`), while preserving the [single-stock `000702.SZ` workload](20261105-long-history-benchmark.md) as a companion ledger regression.

- **Separate signal and traded instruments:** Both runs start with `CNY 100,000` cash on `2011-01-04`. At each session close starting on the 200th index observation, the timing strategy reads the SSE 50 price index (`000016.SH`) and targets the ChinaAMC SSE 50 ETF (`510050.SH`) when the index exceeds its 200-close rolling average, or `100%` cash otherwise; the buy-and-hold control issues a single target for `510050.SH` at the end of the shared warm-up.
- **Execution and cash-dividend lifecycle:** Orders match at `510050.SH` opening prices and mark to raw closing prices under 100-unit board lots, a `CNY 0.001` price tick, `T+1` sellability, and identical commission schedules. Across **14 ETF cash distributions**, Rust counts entitled units at the record-date close, includes the dividend receivable in account equity on the ex-date (adjusting pre-open sizing marks), and credits cash after opening trades on the payment date—holding accumulated distributions in cash until the strategy emits a new target. Loader validation reconciles every `adj_factor` and published `pre_close` jump against the recorded cash distributions.

## Implementation Artifacts

- **Data preparation & runner:** The [ETF loader](../python/trademaster/e2e/broad_etf_data.py) builds the 3,644-session **data snapshot** and 14 distribution records from Tushare Pro, and the [benchmark harness](../python/trademaster/e2e/long_history.py) runs both accounts (plus cold vs. warm snapshot timings documented in the [run guide](../docs/examples/broad-etf.md)).
- **Dividend accounting:** Documented in the [cash dividend API](../docs/reference/snapshot-backtest.md#cash-dividends) and implemented in the [Rust dividend ledger](../crates/tm-engine/src/snapshot/dividends.rs).
- **Visualizations:** The [README P&L chart](../docs/examples/broad-etf-pnl.svg) plots daily closing equity minus initial `CNY 100,000`, while the [HTML report preview](../README.md#backtest-reports) illustrates the January–May 2024 weekly momentum event bundle.

## Verification and Regression Record

- **Edge-case test coverage:** Focused suites guard against forward-filled index closes and verify exact-date index alignment, provider price-limit precision, duplicate distribution rejection, ex-date sales, payment dates falling beyond sample end, and same-day ex/pay schedules (**329 offline Python tests** passed with 3 live network tests skipped; **58 Rust workspace tests** passed).
- **Independent oracle & release-wheel replay:** Preparing the snapshot from Tushare Pro and cross-checking against an independent Python calculation from raw quotes matched every scheduled decision, fee, cash balance, dividend receivable, and closing equity:
  - **Index-timing strategy:** `79` targets, `113` fills, **`CNY 202,977.32`** ending equity.
  - **Buy-and-hold control:** `1` target, `2` fills, **`CNY 208,949.55`** ending equity.
  - Installing the release wheel in a clean environment reproduced both [ETF ledger digests](../docs/examples/broad-etf-reference.json), both [single-stock digests](../docs/examples/long-history-reference.json), and deterministic `tm-replay` output.
