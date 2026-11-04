# tm-engine Guide

This crate runs the event loop and defines the public execution traits. Process
events in timestamp order. For events at the same time, specify which phase
comes first so that replay produces the same result. Tushare, DuckDB, and
configuration files are read outside the engine.

Each ledger update returns a checked `LedgerEffect`. A no-op may have zero
posting groups. If there are multiple groups, keep the source fill for each.
Record settlements that only unlock lots as lot changes. Never create
postings for an expiry or an empty settlement.

`CnAshareOrderValidator` uses its fee schedule to check available cash. It
checks the bar for the order's instrument and event time, the lot size,
suspension and price limits, and the quantity available to sell in the
account snapshot. `DailyBarExecutionModel` assumes a small account with no
market impact and fills eligible orders in full at the open. If an accepted
order reaches the model without a matching bar, record its expiry.

`LotLedger` uses exact scaled integers, FIFO lots, and each instrument's
`SettlementPolicy`. Build the new account state and validate its `LedgerEffect`
before committing either. An error must leave cash, lots, marks, and hashes
unchanged.

`ScheduledSignalStrategy` and online strategies both implement `StrategyAdapter`.
The event runner dispatches these strategies through `EventLoop`. Settlement precedes the
session open. Submit pending orders only when they are eligible, apply every
accepted execution to the same ledger instance, and stop the run on any
component error.

The daily snapshot loop in `src/snapshot/` reads one session at a time. Size
orders using prior closing marks, enforce opening permissions, and apply fills
through `LotLedger`. Count sale proceeds in the next open's buy budget.

Treat a batch of target weights as one desired portfolio. Emit sells before
buys and sort each side by instrument ID. Signal and order IDs may break a
final tie, but changing them must preserve the allocation.
