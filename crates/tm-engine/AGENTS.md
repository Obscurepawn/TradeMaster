# tm-engine Guide

This crate owns event orchestration and public execution traits. It must not
read Tushare, DuckDB, or configuration files directly. Time ordering and event
phase order are explicit and deterministic.

Ledger mutations return a checked `LedgerEffect`: zero groups is a legal no-op,
multiple groups preserve per-fill sources, and lot-only settlement is explicit.
Never fabricate balanced postings for an expiry or empty settlement.

`CnAshareOrderValidator` owns the fee schedule used for cash sufficiency and checks
the exact event bar, instrument lot size, suspension/limit state, and frozen
sellable quantity. `DailyBarExecutionModel` is a deterministic full-fill model for
the user's small-account/no-impact scope; missing bars expire orders audibly.

`LotLedger` uses exact scaled integers, FIFO lots, and the instrument's explicit
`SettlementPolicy`. Build the prospective account state and checked `LedgerEffect`
before committing it so any error leaves cash, lots, marks, and hashes unchanged.

`ScheduledSignalStrategy` and online strategies both implement `StrategyAdapter`.
`EventLoop` is the only formal orchestration path: settlement precedes session open,
pending orders submit only when eligible, every accepted execution goes through the
same ledger instance, and any component error terminates the run.

Target-weight batches are portfolio intents, not independent alpha-ranked orders. Emit
sells first and then use instrument ID as the economic ordering key; signal/order IDs
may be final tie-breaks only and must not change allocation for an identical portfolio.
