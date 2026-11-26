# Design Specifications & RFC Index

This directory records TradeMaster's architectural RFCs, subsystem data and execution contracts, reproducible benchmarks, and milestone verification records. Each specification documents its motivation, point-in-time and accounting invariants, modeling limitations, and regression evidence so contributors can trace how every subsystem evolved.

## Active

- [Code Architecture Refactoring: Daily Snapshot SDK and Rust Portfolio Sizing](20261104-code-architecture-refactor.md) — Defines whole-portfolio daily snapshot backtests, Rust pre-open sizing and cash-dividend accounting, and three 98-session Tushare Pro examples, with migration of the legacy long-history `industry_fundamental` strategy remaining open.

## Core Architecture & Execution RFCs

- [Open-Source Documentation and Repository Governance Overhaul](20261113-open-source-documentation-overhaul.md) — Isolates exploratory empirical studies under `studies/`, adds contributor and CI governance, stratifies user/reference/agent docs, and standardizes all `specs/` records as clean RFCs.
- [Python and Rust Native Backtest Interface](20261105-python-rust-backtest-interface.md) — Embeds the Rust daily and event engines in process via PyO3 (`tm-python`), exposing live closing accounts and fills to `on_close` strategy callbacks and unifying `tm-replay`.
- [Configurable Trading Rules and Price-Limit Enforcement](20261106-configurable-trading-rules.md) — Adds optional daily price-limit enforcement with explicit limit-block markers and zero-commission event support while preserving A-share defaults and saved-request replay.
- [IndexOne Research Data Source Integration](20261106-indexone-data-source.md) — Integrates schema-validated read-only IndexOne EOD, fundamental, security-reference, and FX queries into an immutable Parquet/DuckDB evidence cache with offline `tm-indexone` replay.

## Performance & Execution Benchmarks

- [Compact Market Rows and Streaming Snapshot Loading](20261126-snapshot-compact-market-rows.md) — Proposes a columnar in-memory market (~33 B/row instead of ~285 B), a streaming SHA-256-verifying loader, and streamed feature grids to cut `Snapshot.open` peak memory several-fold without changing snapshot formats or results.
- [Ledger State Hashing Cost in Daily Snapshot Runs](20261126-ledger-state-hash-cost.md) — Profiles state-hash consumers and lets daily snapshot runs post fills without serializing and hashing every open lot (about 10× faster on a Hong Kong replay and 97× on a lot-accumulating run), while event-run state hashes and audit outputs stay byte-identical.

- [Broad ETF Execution and Cash-Distribution Benchmark](20261105-broad-etf-benchmark.md) — Benchmarks fifteen years (3,644 Shanghai sessions) of SSE 50 index signals, `510050.SH` ETF opening fills, and 14 cash distributions against an independent reference ledger.
- [Fifteen-Year Single-Stock Execution Benchmark](20261105-long-history-benchmark.md) — Benchmarks a daily 200-close moving-average strategy against a buy-and-hold control across 3,644 Shenzhen sessions of `000702.SZ` with documented trading suspensions.

## Factor Library, Research Pipeline & Reference Strategies

- [M9: Twenty-Year All-A-Share Factor Research Pipeline and Quality Audit](20260823-m9-full-a-20y-factor-evaluation-quality.md) — Acquires 20 years of survivorship-free A-share data (5,846 instruments, 52,563 verified tasks), builds dense PIT panels, evaluates factors in streaming DuckDB, and resolves a 28-finding cross-system audit.
- [M8: Huatai Style Factors and Guotai Junan Alpha191 Catalog](20260823-m8-huatai-gtja-factor-library.md) — Catalogs 460 public factor candidates across Huatai style collections and GTJA Alpha191 with provenance, licensing, and PIT readiness tracking, promoting initial executable factors.
- [M7: Managed Factor Registry, Materialization Store, and Strategy Refactor](20260823-m7-factor-management-strategy-refactor.md) — Introduces content-addressed factor DAG definitions, Parquet/DuckDB materializations, IC/quantile evaluation, `tm-factor`, and v3 bundle migration resolving twelve strategy and scoring defects.
- [Survey of Public Factor Libraries and Institutional Research](20260823-public-factor-library-survey.md) — Classifies academic, open-source, broker-disclosed, and commercial factor libraries by access tier, licensing, and point-in-time data requirements (`docs/research/public-factor-library-survey.md`).
- [Two-Stage Industry Selection Study: Top Ten Industries, One Stock Each](20260812-industry-fundamental-top10-industries.md) — Evaluates adding a market-wide mean-score filter (`top_industries_by_mean_score=10`) ahead of within-industry single-stock fundamental selection over ten years.
- [Industry Concentration Study: One Stock vs. Five Stocks per Industry](20260812-industry-fundamental-top1-experiment.md) — Compares `top_per_industry=1` against `top_per_industry=5` on an identical ten-year CSI 300 cache, isolating portfolio concentration and turnover effects.
- [Semiannual Industry-Neutral Fundamental Strategy (Top 5 per Industry)](20260812-industry-fundamental-top5.md) — Defines the baseline ten-year CSI 300 semiannual within-industry nine-indicator fundamental strategy, Rust target-weight execution, and deterministic replay bundle.

## Foundational Milestones (v1 & M1–M6) and Release

- [Initial Open-Source Repository Publication](20260823-open-source-repository-publication.md) — Establishes the Apache-2.0 license, `.gitignore` data/secret boundaries, package metadata, architecture diagrams, and initial public `main` baseline (`7b12346`).
- [M6: End-to-End Backtest Bundles and Deterministic Replay](20260811-m6-full-e2e.md) — Connects data snapshots, factors/signals, the Rust event runtime, and HTML reports into self-verifying synthetic and live Tushare Pro run bundles.
- [M5: Performance Metrics, Exposure Diagnostics, and Standalone Reports](20260811-m5-metrics-reporting.md) — Specifies strategy and multi-benchmark return/risk metrics, cross-month return boundaries, ledger-derived exposures, and offline Plotly HTML reports.
- [M4: Unified Rust Event Runtime](20260811-m4-unified-event-runtime.md) — Unifies precomputed signal batches and online strategies under a deterministic Rust `EventLoop` enforcing settlement-before-open and sequential per-order valuation.
- [M3: A-Share Execution Rules and Lot Accounting in Rust](20260811-m3-rust-rules-ledger.md) — Implements daily A-share board lots, suspensions, price limits, brokerage/stamp/transfer fees, and atomic `Fixed8` FIFO T+1 lot accounting in `tm-engine`.
- [M2: Factor Computation and Signal Generation](20260811-m2-factor-signal-layer.md) — Defines the initial point-in-time factor executor, momentum/value/composite transforms, and close-to-next-open (`T+1`) target-weight and threshold signal generators.
- [M1: Immutable Parquet Data Store and Point-in-Time Catalog](20260810-m1-data-foundation.md) — Establishes three-tier raw/staging/canonical Parquet storage, transactional DuckDB coverage proofs, Tushare Pro ingestion, and cutoff-verified data snapshots.
- [TradeMaster v1 Architecture and System Contract](20260810-trademaster-v1.md) — Defines the initial Python/Rust system boundary, shared 11-table `trademaster.run-artifacts/v1` Arrow schemas, and M0–M6 acceptance and contract-hardening records.
