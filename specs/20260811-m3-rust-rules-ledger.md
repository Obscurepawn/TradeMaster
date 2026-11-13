# M3: A-Share Execution Rules and Lot Accounting in Rust

**Status:** Completed | **Date:** 2026-08-11 | **Scope:** Daily A-share order validation, exact fee schedules, and atomic FIFO T+1 lot accounting (`tm-core`, `tm-engine`) | **Updated-by:** [Configurable Trading Rules and Price-Limit Enforcement](20261106-configurable-trading-rules.md)

## Motivation and Scope

Milestone M3 implements daily A-share trading constraints, execution matching, fee schedules, and lot-level accounting in Rust against the domain traits defined in [TradeMaster v1](20260810-trademaster-v1.md). Strategy scheduling and the unified event loop belong to [M4](20260811-m4-unified-event-runtime.md), and performance reporting belongs to [M5](20260811-m5-metrics-reporting.md).

## Design and Public Interfaces

- **`CnAshareOrderValidator` (`OrderValidator`):** Inspects `SubmittedOrder` and `ValidationView` at the target market event. It rejects orders submitted before their eligible execution time, missing or stale market bars, instrument mismatches, suspended instruments, buys at or above `up_limit`, sells at or below `down_limit`, invalid buy board lots, insufficient cash (including fees and slippage), and insufficient unlocked sellable shares.
- **`DailyBarExecutionModel` (`ExecutionModel`):** Matches accepted orders at the configured daily bar price field (such as session open) and applies parts-per-million slippage. Fills never exceed the accepted order quantity; when execution is impossible (for example, due to a missing bar), the model records an explicit rejection or session-end expiry.
- **`CnFeeSchedule` (`FeeSchedule`):** Computes exact brokerage commission (subject to a per-fill minimum commission), sell-side stamp duty, and Shanghai Stock Exchange (SSE) transfer fees from a versioned rate configuration.
- **`LotLedger` (`Ledger`):** Maintains cash, aggregate positions, and FIFO tax/settlement lots using exact `Fixed8` (`i128`) arithmetic. Every fill produces a balanced group of ledger postings linked to that fill and advances the account state hash atomically; a failed transition leaves cash, lots, and the prior state hash unchanged.

## Trading Rules and Limitations

- **Board lots and odd-lot exits:** Purchases must be positive integer multiples of `InstrumentSpec.buy_lot_size` (100 shares for standard A-shares). Partial sales follow the configured lot policy, while a full exit may sell an entire remaining odd-lot balance once unlocked.
- **Explicit settlement policy:** `InstrumentSpec.settlement` (`SettlementPolicy`) explicitly selects `T+1` (standard A-share stocks, unlocked at the next session supplied by `TradingCalendar` across weekends and holidays) or `T+0` (configured ETFs). Settlement policies are never inferred implicitly from ticker suffixes. Settlement transitions only unlock held lots without altering share counts or cash.
- **Daily execution scope:** Eligible small daily orders fill in full at the configured event price plus slippage; intraday queue priority, volume participation caps, and market-impact curves are outside the daily execution model.

## Verification and Regression Record

Every trading constraint was paired with hand-calculated positive (tradable) and negative (rejected) test cases, culminating in a multi-session scenario exercising purchase, same-day T+1 sale rejection, next-session settlement unlock, and FIFO sale.

| Test Suite / Review Stage | Verified Behavior |
|---|---|
| Order validation & fees (`crates/tm-engine/tests/a_share_rules.rs`) | Verified exact commission, minimum commission, sell stamp duty, SSE transfer fee, and six rejection categories (board lot, suspension, upper limit, lower limit, insufficient cash, T+1 lock). |
| Execution model (`crates/tm-engine/tests/daily_execution.rs`) | Verified full fills, exact slippage and fee deduction, and auditable order expiry when market bars are absent. |
| Atomic FIFO ledger (`crates/tm-engine/tests/lot_ledger.rs`) | Verified lot creation on buy, atomic rollback on illegal same-day T+1 sale, calendar-driven next-session lot unlock, FIFO lot consumption, cash/NAV conservation, balanced postings, and SHA-256 state hashes. |
| Independent review & fixes | Resolved 3 High findings (0 Critical): `CnAshareOrderValidator` previously accepted stale bar timestamps, accepted mismatched `ValidationView` instruments, and omitted slippage from its pre-trade cash check. Validation and execution now enforce exact bar timestamp and instrument equality and share identical parts-per-million slippage arithmetic. |
| Final milestone checks | 34 Rust workspace tests and doc-tests, `cargo fmt --check`, `cargo clippy -D warnings`, `cargo package -p tm-core`, 122 Python regression tests (0 skipped), Ruff, strict `mypy`, and `git diff --check` all passed (isolated `tm-engine` crates.io packaging remained gated only on publishing `tm-core 0.1.0`). |
