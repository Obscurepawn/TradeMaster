# tm-engine Guide

## Scope

`tm-engine` implements TradeMaster's simulation traits, configurable order validation and fee schedules, the deterministic `EventLoop`, the shared FIFO `LotLedger`, and the daily target-portfolio snapshot engine ([`src/snapshot/`](src/snapshot/)). External data providers (Tushare, IndexOne), DuckDB catalogs, and filesystem configuration remain outside this crate.

## Invariants

- **Deterministic event ordering**: Advance `EventLoop` strictly in timestamp order with explicit intra-timestamp phase precedence (settlement precedes `SessionOpen`; pending orders submit only when eligible; every accepted execution applies to the single run ledger; any component error aborts the run immediately). Both `ScheduledSignalStrategy` and online strategies implement `StrategyAdapter`.
- **Atomic `LotLedger` and `LedgerEffect` discipline**: `LotLedger` tracks cash, marks, and FIFO share lots in exact scaled integers under each instrument's `SettlementPolicy` (`t0` or `t1` resale availability). Stage every transition against copies of only the lots it opens or reduces and install it with an infallible commit, so any error leaves cash, lots, marks, and state hashes untouched. The audited `Ledger::apply`/`settle` path used by `EventLoop` additionally builds the before and after `AccountSnapshot`s and validates the `LedgerEffect` before committing, because event-run audit tables persist those state hashes. The daily snapshot engine publishes no state hashes, so it posts through the crate-private `apply_unaudited`/`settle_unaudited` and values opens and closes with `valuation`, which share the staging code and must reach the same state and errors without materializing lots or hashing them (see [Ledger State Hashing Cost in Daily Snapshot Runs](../../specs/20261126-ledger-state-hash-cost.md)). No-op updates may emit zero posting groups, multi-group effects must preserve each group's source fill, settlements that only unlock lots record lot changes without cash postings, and expiries or empty settlements never emit postings.
- **Order validation and daily bar execution**: `CnAshareOrderValidator` checks event timing, matching instrument bars, lot sizes (allowing odd-lot full-position exits on sells), suspensions, optional daily price limits (`with_price_limits`), available cash including fees and slippage, and sellable share balances. `DailyBarExecutionModel` models a non-impacting account that fills accepted orders in full at the session open and records an `Expiry` when an accepted order lacks a matching bar.
- **Daily snapshot loop (`src/snapshot/`)**: Process one session at a time—settle unlocked lots, accrue/pay cash dividends (`record_session` close entitlement -> `ex_session` open receivable -> `pay_session` open cash), fix whole-lot order quantities from prior closing marks while reserving cash separately, check opening permissions (honoring `Policy.enforce_price_limits` only on marked price-limit blocks), credit sale proceeds into the same open's buy budget, and value closing marks. Halt with `status="insufficient_data"` and populate `first_issue` on missing required marks or `corporate_action_gap`.
- **Target-weight rebalance ordering**: Treat each target-weight batch as a single desired portfolio. Emit sell orders before buy orders and sort each side deterministically by `instrument_id`; signal and order IDs may break final ties without ever altering allocations.

## Verification

```bash
cargo test --locked -p tm-engine
```
