# M3 A-share execution rules and lot accounting in Rust

Status: completed
Owner: Codex

## Scope and public interfaces

M3 implements daily A-share order validation, execution, fees, and lot accounting using the Rust interfaces established in M0. M4 handles strategy scheduling and the event loop. M5 supplies metrics and reporting.

- `CnAshareOrderValidator` implements `OrderValidator` and reads only `SubmittedOrder + ValidationView`. It rejects early submissions, missing price data, suspended instruments, buys at the upper limit, sells at the lower limit, invalid buy lots, insufficient cash, and insufficient sellable quantity.
- `DailyBarExecutionModel` implements `ExecutionModel` using the configured price field at a specified market event. Fills cannot exceed the order quantity. Record an expiry or rejection when execution is impossible.
- `CnFeeSchedule` implements `FeeSchedule`. It calculates commission, minimum commission, sell-side stamp duty, and the Shanghai transfer fee exactly. The configuration identifies the applicable rate schedule and version.
- `LotLedger` implements `Ledger` with `Fixed8/i128` amounts. Each update changes cash, positions, and FIFO lots atomically. `InstrumentSpec.settlement` determines T+0 or T+1 availability. Settlement only unlocks lots.
- Every fill produces a balanced group of postings linked to that fill. A failed update preserves the previous state hash.

## Trading rules

Reject buys at or above `up_limit` and sells at or below the lower limit. Suspension blocks both sides. Buys must be multiples of `buy_lot_size`. An order may sell the entire remaining odd lot. Other sells follow the configured lot policy.

The daily model fills small eligible orders in full and charges the configured slippage. It does not limit a fill by traded volume or model price impact. The instrument's `SettlementPolicy` specifies T+1 for stock or T+0 for a configured ETF. Set it explicitly rather than inferring it from the ticker suffix.

## Test and implementation sequence

1. Red. Hand-check validation cases for 100-share lots, suspension, both price limits, cash, and sellable quantity.
2. Green. Implement `CnAshareOrderValidator` with exact rejection codes and times.
3. Red. Hand-check execution price, minimum commission, stamp duty, transfer fee, slippage, and expiry.
4. Green. Implement `CnFeeSchedule` and `DailyBarExecutionModel`.
5. Red. Cover buy-lot creation, same-day selling restrictions, next-session release, FIFO sales, and the resulting cash, holdings, NAV, and posting balances.
6. Green. Implement atomic `LotLedger` updates and settlement.
7. Exercise purchase, same-day sale rejection, T+1 release, and successful sale in one scenario. Check every ledger effect and obtain one independent review.

## Acceptance criteria

Each A-share rule needs a test for an order that can trade and one for an order that the rule blocks. Fees and account balances must match the hand calculations. For T+1, the external `TradingCalendar` supplies the next session, including across weekends and holidays. Trading costs, cash, holdings, and ledger entries all use exact arithmetic. Required checks include `cargo fmt --check`, Clippy with `-D warnings`, workspace tests, doc-tests, packaging, and the Python/shared-schema regression suite. The full system review was scheduled after the M6 end-to-end run.

## Implementation and verification record

M0's Rust records and public traits had passed tests using external implementations. M1 and M2 were complete.

- Red 1. `a_share_rules.rs` could not compile without `CnAshareOrderValidator` and `CnFeeSchedule`. After implementation, 3 tests covered fees and six rejection categories.
- Red 2. `daily_execution.rs` could not compile without `DailyBarExecutionModel`. After implementation, 2 tests covered full fills, exact fees and slippage, and expiry when a bar is missing.
- Red 3. `lot_ledger.rs` could not compile without `LotLedger`. The passing tests covered adding a bought lot, an atomic failure on a same-day sale, the next-session unlock, FIFO sales, cash, NAV, postings, and the state hash.
- Independent review found 0 Critical and 3 High issues. The validator accepted stale bars and mismatched instruments, and its cash check omitted slippage. One round of failing tests and fixes addressed all three. Validation and execution now require the correct bar timestamp and instrument and use the same parts-per-million slippage calculation.
- Final checks passed for the Rust workspace's 34 tests, doc-tests, formatting, and Clippy `-D warnings`. Python passed 122 tests with 0 skipped. Ruff, strict mypy, and `git diff --check` passed. The team did not run a second milestone review.
- Packaging succeeded for `tm-core` with `cargo package`. Packaging `tm-engine` independently remained subject to publication order because crates.io did not yet have the local `tm-core 0.1.0`. Local workspace builds and tests succeeded.
