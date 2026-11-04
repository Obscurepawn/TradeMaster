# M4 shared event loop

Status: completed
Owner: Codex

## Scope and public interfaces

Python can precompute signals in batches. Strategies that depend on events can decide during replay. M4 sends both through the same Rust `OrderValidator`, `ExecutionModel`, and `Ledger`. It keeps the complete run trace in memory. Parquet writing, the run manifest, and reporting were scheduled for M5/M6.

- `ScheduledSignalStrategy` consumes quantity signals ordered by `eligible_execution_time` and converts them to stable `OrderIntent` records at the eligible event. In M4, target weights require an explicit portfolio rebalancer to allocate cash.
- `EventLoop` orders settlement, session open, and bar close by event time and phase.
- `RuntimeInstrumentRegistry` supplies an immutable `InstrumentSpec` by instrument ID and rejects unknown instruments.
- `RunTrace` preserves signals, orders, acceptances, rejections, executions, ledger effects, and account snapshots in event sequence for the shared Arrow artifact tables.
- Both precomputed signals and online strategies implement `StrategyAdapter`. The event loop uses that interface for either source.

## Event order

1. Settlement unlocks lots whose holding period has ended.
2. Take the opening portfolio view at the time of the session-open market snapshot.
3. The strategy reads that snapshot and portfolio and produces orders that are eligible for submission.
4. Build a `ValidationView` for each order's instrument. Record acceptance or rejection.
5. Process accepted orders for the batch. Apply each execution to the ledger atomically.
6. At bar close, update the valuation and let the strategy observe it. A signal formed at the close is first eligible at the next session open.

Within an event, keep instrument, order, and fill ordering stable. Stop the run on any component error.

## Test and implementation sequence

1. Red. Test scheduled quantity signal timing, one-time consumption, stable order IDs, and unknown instruments.
2. Green. Implement the adapter for precomputed signals.
3. Red. Check the complete two-session trace from purchase and same-day sale rejection to settlement and sale.
4. Green. Implement `EventLoop` and `RunTrace`.
5. Red. Require matching executions and NAV for online and precomputed strategies given equivalent signals.
6. Green. Use the shared strategy interface and verify deterministic replay.
7. Run integration checks and conduct one independent review.

## Acceptance criteria

A run uses one validator, execution model, and ledger instance for either strategy type. Each scheduled signal is consumed once. A signal formed at the close waits until the next eligible open. Replaying the same data snapshot, configuration, and signals reproduces the trace and state hashes. Run Rust formatting, Clippy, workspace tests and doc-tests, and the Python/shared-schema regression suite. Schedule the final system review after M6 end-to-end testing.

## Implementation and verification record

- M0 through M3 were complete. Red 1 failed to compile `scheduled_strategy.rs` because `ScheduledSignalStrategy` was missing. The 2 passing tests checked waiting from close to next open, one-time consumption, integral quantities, and rejection of unsupported target weights.
- Red 2 failed to compile `unified_event_loop.rs` because `EventLoop` was missing. The subsequent test followed precomputed signals through validation, execution, T+1 settlement, ledger updates, and final NAV.
- The independent review found 0 Critical and 4 High issues involving missing settlement, quantity deltas for targets due at the same open, trace completeness and leftover signals, and cash computed only once before the batch. One round of regression tests and fixes addressed all four.
- `EventLoop` checks that settlement precedes open. For targets due together, the adapter keeps track of the share count each pending order would leave before it sizes the next one. `RunTrace` records signals and `event_seq`, and the strategy exposes its pending count. The loop revalues the portfolio for each order, executes the order, and posts it before processing the next one.
- Final Rust checks passed for 39 workspace tests, doc-tests, formatting, and Clippy `-D warnings`. Python passed 122 tests with 0 skipped. Ruff, strict mypy, and `git diff --check` passed. No second milestone review was held.
