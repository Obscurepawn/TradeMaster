# tm-engine Guide

## Scope

`tm-engine` implements TradeMaster's simulation traits, configurable order validation and fee schedules, the deterministic `EventLoop`, the shared FIFO `LotLedger`, and the daily target-portfolio snapshot engine ([`src/snapshot/`](src/snapshot/)). External data providers (Tushare, IndexOne), DuckDB catalogs, and filesystem configuration remain outside this crate.

## Invariants

- **Deterministic event ordering**: Advance `EventLoop` strictly in timestamp order with explicit intra-timestamp phase precedence (settlement precedes `SessionOpen`; pending orders submit only when eligible; every accepted execution applies to the single run ledger; any component error aborts the run immediately). Both `ScheduledSignalStrategy` and online strategies implement `StrategyAdapter`.
- **Atomic `LotLedger` and `LedgerEffect` discipline**: `LotLedger` tracks cash, marks, and FIFO share lots in exact scaled integers under each instrument's `SettlementPolicy` (`t0` or `t1` resale availability). Construct the candidate account state and validate its `LedgerEffect` before committing either; any error must leave cash, lots, marks, and state hashes untouched. No-op updates may emit zero posting groups, multi-group effects must preserve each group's source fill, settlements that only unlock lots record lot changes without cash postings, and expiries or empty settlements never emit postings.
- **Order validation and daily bar execution**: `CnAshareOrderValidator` checks event timing, matching instrument bars, lot sizes (allowing odd-lot full-position exits on sells), suspensions, optional daily price limits (`with_price_limits`), available cash including fees and slippage, and sellable share balances. `DailyBarExecutionModel` models a non-impacting account that fills accepted orders in full at the session open and records an `Expiry` when an accepted order lacks a matching bar.
- **Compact prepared market (`src/snapshot/rows.rs`, `market.rs`)**: Decode the wire `rows` array one `DailyRow` at a time (keeping serde validation and error text) into columnar storage: interned session/instrument/reason identifiers, `i64` prices where `0` means absent (valid prices are positive), and one flag word. `PreparedMarket` owns its data, validates rows in input order with the first failing row reported, accepts any row order, and keeps rows per session sorted by instrument index (CSR); read rows through `RowView` and keep per-session state such as closing marks in dense per-instrument vectors rather than string-keyed maps.
- **Daily snapshot loop (`src/snapshot/`)**: Process one session at a time—settle unlocked lots, accrue/pay cash dividends (`record_session` close entitlement -> `ex_session` open receivable -> `pay_session` open cash), fix whole-lot order quantities from prior closing marks while reserving cash separately, check opening permissions (honoring `Policy.enforce_price_limits` only on marked price-limit blocks), credit sale proceeds into the same open's buy budget, and value closing marks. Halt with `status="insufficient_data"` and populate `first_issue` on missing required marks or `corporate_action_gap`.
- **Target-weight rebalance ordering**: Treat each target-weight batch as a single desired portfolio. Emit sell orders before buy orders and sort each side deterministically by `instrument_id`; signal and order IDs may break final ties without ever altering allocations.

## Verification

```bash
cargo test --locked -p tm-engine
```
