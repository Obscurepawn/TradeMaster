# M4: Unified Rust Event Runtime

**Status:** Completed | **Date:** 2026-08-11 | **Scope:** Deterministic event loop, strategy adapters, settlement ordering, and in-memory audit traces (`tm-engine`)

## Motivation and Scope

Milestone M4 unifies precomputed Python signal batches and event-driven online strategies on a single Rust execution loop. Both strategy modes implement `StrategyAdapter` and pass through the same `OrderValidator`, `ExecutionModel`, and `Ledger` instances from [M3](20260811-m3-rust-rules-ledger.md), recording a deterministic `RunTrace` in memory for downstream Parquet persistence and reporting ([M5](20260811-m5-metrics-reporting.md)–[M6](20260811-m6-full-e2e.md)).

## Design and Public Interfaces

- **`StrategyAdapter` and `ScheduledSignalStrategy`:** `StrategyAdapter` provides a single callback contract for both online strategies and precomputed signal tables. `ScheduledSignalStrategy` consumes precomputed quantity signals ordered by `eligible_execution_time` exactly once, converting them into deterministic `OrderIntent` records at the first eligible market event (in M4, target-weight signals require an explicit portfolio rebalancer before entering quantity-only adapters).
- **`RuntimeInstrumentRegistry`:** Resolves immutable `InstrumentSpec` records by `instrument_id` and fails closed on unknown instruments.
- **`EventLoop`:** Orders settlement, session-open execution, and session-close valuation deterministically by timestamp and event phase, halting immediately if any validator, execution model, or ledger step returns an error.
- **`RunTrace`:** Captures consumed signals, order intents, acceptances, rejections, fills/expiries, ledger effects, account snapshots, and monotonic `event_seq` numbers required by the eleven shared `trademaster.run-artifacts/v1` tables.

### Per-Session Event Sequence

1. **Settlement:** Unlock T+1 lots whose holding period has completed at the start of the trading session.
2. **Opening snapshot:** Materialize the read-only portfolio and market view at the session-open timestamp.
3. **Order submission:** Invoke `StrategyAdapter` on the opening view to emit eligible `OrderIntent` records in stable instrument and signal order.
4. **Sequential validation, execution, and posting:** For each submitted order, build an updated `ValidationView` reflecting prior fills in the same batch, record acceptance or rejection, execute accepted orders via `ExecutionModel`, and apply each resulting `LedgerEffect` atomically before sizing or validating the next order.
5. **Closing valuation:** At session close, mark positions to closing prices and expose the updated valuation to the strategy; any signal formed from session `T` closing prices becomes eligible no earlier than the session `T+1` open.

## Verification and Regression Record

Equivalence tests verified that precomputed signal tables and online `StrategyAdapter` implementations produce identical fills, ledger postings, final NAV, and account state hashes when fed the same data snapshot and configuration.

| Test Suite / Review Stage | Verified Behavior |
|---|---|
| Scheduled signals (`crates/tm-engine/tests/scheduled_strategy.rs`) | Verified close-to-next-open (`T+1`) eligibility waiting, single-consumption semantics, integer share deltas, stable order IDs, unknown-instrument rejection, and rejection of unallocated target-weight intents (2 suites). |
| Unified event loop (`crates/tm-engine/tests/unified_event_loop.rs`) | Traced multi-session execution from purchase and same-day T+1 sale rejection through next-session settlement, FIFO sale, ledger postings, and deterministic replay hashes. |
| Independent review & fixes | Resolved 4 High findings (0 Critical) with regression tests: (1) enforced that settlement always precedes session open; (2) tracked pending share-count deltas when multiple signals for the same instrument fall due at one open; (3) recorded consumed signals, `event_seq`, and pending-signal counts in `RunTrace`; and (4) revalued available cash and holdings after each order fill rather than computing cash once before the batch. |
| Final milestone checks | 39 Rust workspace tests and doc-tests, `cargo fmt --check`, `cargo clippy -D warnings`, 122 Python tests (0 skipped), Ruff, strict `mypy`, and `git diff --check` all passed. |
