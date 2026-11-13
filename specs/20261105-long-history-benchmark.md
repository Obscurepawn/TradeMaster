# Fifteen-Year Single-Stock Execution Benchmark

**Status:** Completed | **Date:** 2026-11-05 | **Scope:** Fifteen-year daily moving-average vs. buy-and-hold benchmark across 3,644 Shenzhen sessions (`000702.SZ`, `trademaster.e2e.long_history`) | **Complemented-by:** [Broad ETF Execution and Cash-Distribution Benchmark](20261105-broad-etf-benchmark.md)

## Motivation and Workload Design

The [fifteen-year single-stock benchmark](../docs/examples/long-history.md) measures end-to-end daily Python strategy callbacks and Rust snapshot execution on a fixed, reproducible long-horizon workload, complementing the README's [broad ETF benchmark](20261105-broad-etf-benchmark.md). Full data preparation commands, cost parameters, and replay instructions are maintained in the [run guide](../docs/examples/long-history.md).

The workload spans the Shenzhen Stock Exchange (`SZSE`) trading calendar from `2011` through `2025` (**3,644 trading sessions**) using Tushare Pro daily history for `000702.SZ`:

- **Corporate-action simplicity with real suspensions:** `000702.SZ` combines fifteen years of continuous trading and documented suspension episodes with a constant `adj_factor` across the entire window, allowing the Rust ledger to operate on exact unadjusted share quantities and raw prices without corporate-action distortion.
- **Strategy vs. buy-and-hold control:** Compares a daily `200`-close simple moving-average timing strategy against a single buy-and-hold target. Both runs start with `CNY 100,000` cash, share identical warm-up windows and A-share trading costs, keep suspended sessions on the exchange calendar while excluding missing suspension quotes from the rolling window, and execute close-derived decisions no earlier than the next eligible session open (`T+1`).
- **Measured outputs:** Records decision dates, trading fees, daily closing equity, maximum drawdown, target counts, and fill counts while timing cold runs (including **data snapshot** loading) separately from warm repeated runs on an open snapshot. Ledger digests act as deterministic correctness invariants; wall-clock timings characterize the host hardware.

## Verification and Regression Record

- **Dual-source snapshot equivalence:** Preparing the data snapshot from a direct Tushare Pro download and extracting it from the local M9 research panel produced byte-identical `manifest.json` and market files.
- **Independent oracle & wheel verification:** An independent rolling-price calculation matched all **164 moving-average target decisions**, and an independent ledger reconstruction from fills, fees, and raw closes matched every daily valuation and maximum drawdown in [long-history-reference.json](../docs/examples/long-history-reference.json)—both in the development workspace and from an isolated wheel installation via `tm-replay`.
- **Regression coverage:** Eight focused benchmark tests cover provider inputs, suspended-quote exclusion, opening trade restrictions, warm-up boundaries, exact moving-average equality ties, repeated-run determinism, and reference-digest mismatch detection; the full offline Python suite passed **310 tests** (with 3 live network tests skipped).
