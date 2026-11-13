# Daily portfolio snapshot API

The Daily Snapshot SDK simulates long-only target-weight portfolios against an immutable, reusable **data snapshot**. Unlike an event-driven strategy that reacts to individual intraday or opening bars, a snapshot run separates portfolio selection, order sizing, and market execution across three strict phases:

1. **Close-time decision.** At each scheduled close, Python observes raw/adjusted closing prices and point-in-time features published by that session's `as_of` decision cutoff, inspects actual account holdings and recent fills, and returns desired portfolio weights.
2. **Pre-open sizing and cash reservation.** Before the next trading session opens, Rust converts target weights into whole-lot order quantities using the previous session's closing valuation marks and separately sets aside uninvested target cash plus per-order buy reserves priced at the configured buy cap.
3. **Opening execution.** Only after order quantities and reserved cash are finalized does Rust inspect opening prices and directional trading permissions to fill or reject each planned order.

Because a single verified data snapshot can serve many independent runs without reparsing market history, this path is the primary interface for target-weight backtesting and parameter sweeps. For explicit event streams or open-price order sizing where same-morning sale proceeds fund purchases immediately, see the [event runner](execution.md).

## Market inputs and decision timing

A data snapshot bundles trading calendars, instrument metadata, raw execution bars, opening tradability evidence, and timestamped research features under a content-addressed manifest:

| Interface | Architectural role and contract |
|---|---|
| `Snapshot.create(directory, *, sessions, next_session, instruments, daily, features=..., cash_dividends=...)` | Publishes a content-addressed manifest alongside a market JSON object and a feature Parquet file. The manifest binds both file digests, session decision `cutoffs`, instrument listing intervals, and source provenance. Supply timezone-aware `close_times` and `cutoffs` for non-A-share calendars; both default to `15:00` Shanghai time (`Asia/Shanghai`), and a cutoff may fall later than the close on the same local trading day. |
| `Snapshot.open(directory)` or `Snapshot.load(directory)` | Recomputes the manifest identity and verifies both stored object hashes before exposing the snapshot for repeated strategy runs. |
| `Snapshot.from_demo_market(market, directory)` | Converts a verified local Tushare sample into the snapshot layout, preserving directional opening permissions and adjusted closes for signal research. |
| `Instrument`, `DailyPrice`, `Feature` | `Instrument` defines venue, currency, buy lot size, price tick, share-settlement lag, and listing bounds. Each `DailyPrice` row provides raw open/close prices, directional `buy_at_open` / `sell_at_open` permissions, supporting `evidence`, and optional block reasons (defaulting to `blocked_at_open` when rejected without custom text). Each `Feature` pairs a named, versioned floating-point observation with its publication timestamp (`available_at`). |
| `snapshot.day(session)` → `DecisionDay` | Exposes the point-in-time market view as of that session's decision cutoff, listing active instruments, `closes(symbol, adjusted=False, limit=None)` through the decision close (where positive `limit` slices the trailing `N` sessions with missing bars preserved in place), and `scores(name)` containing the latest feature version published on or before the cutoff. |

Decision visibility and execution valuation are strictly isolated. Supply raw and adjusted closes that were observable at the session's listed close time; for fundamentals or vendor datasets released after the close, publish `Feature` rows stamped with their true `available_at` timestamp. Python's `DecisionDay` enforces the session `cutoff` so features released after Friday's cutoff remain invisible until a later decision day, while Rust independently reads raw execution prices and permissions from the market table. If a raw price series contains an unmodeled discontinuity—such as a stock split without a corresponding share adjustment—set `DailyPrice.corporate_action_gap=True`; any run holding that security or attempting to trade it halts before the affected open rather than recording a distorted return. At each scheduled close, the strategy callback receives a `BacktestDay` combining the `DecisionDay` view, the valued closing `account`, and `fills` executed since the previous callback. [Snapshot implementation](../../python/trademaster/backtest/snapshot.py) · [Cutoff and snapshot integrity tests](../../tests/test_snapshot_backtest.py)

### Configurable trading rules

Although the SDK defaults to mainland A-share conventions (100-share buy lots, T+1 share availability, enforced daily price limits, Shanghai transfer fees, and sell-side stamp duty), venue mechanics are parameterized across `Instrument` and `Policy` so strategies can run on ETFs, US equities, or zero-cost baselines:

- **Lot granularity and share settlement.** `Instrument` controls `buy_lot_size`, `tick_size`, `currency`, and purchased-share resale timing. Setting `buy_lot_size=1` enables single-share orders; `settlement="t0"` makes newly purchased shares sellable immediately, whereas `"t1"` locks purchases until the next supplied trading session. Pre-existing `InitialLot` holdings always honor their explicit `sellable_session`, and this share-availability setting models trading eligibility rather than clearinghouse cash settlement.
- **Optional daily price-limit enforcement.** `Policy.enforce_price_limits` defaults to `True`. Setting `enforce_price_limits=False` lifts an opening `"no"` permission **only** when the snapshot producer explicitly marked a daily price limit as the sole cause of that directional block:

| Market evidence | `enforce_price_limits=True` (default) | `enforce_price_limits=False` |
|---|---|---|
| `buy_at_open="no"`, `buy_blocked_by_price_limit=True` | Reject the buy with its supplied reason. | Bypass the price-limit block; continue checking suspension, opening price, buy cap, and reserved cash. |
| `sell_at_open="no"`, `sell_blocked_by_price_limit=True` | Reject the sell with its supplied reason. | Bypass the price-limit block; continue checking suspension, opening price, and sellable lot quantity. |
| Unmarked `"no"` (`*_blocked_by_price_limit=False`) | Reject. | Reject. |
| `"unknown"`, missing evidence, or missing opening price | Halt with `insufficient_data` if needed by a planned order. | Halt with `insufficient_data` if needed by a planned order. |

Both `buy_blocked_by_price_limit` and `sell_blocked_by_price_limit` default to `False`, must be booleans, and are valid only alongside a `"no"` permission on the matching side. Producers set a marker only when verified source evidence establishes a daily price limit as the sole reason trading was blocked; human-readable rejection text is purely diagnostic and never triggers a rule bypass, and `suspended=True` or `corporate_action_gap=True` rows still prevent fills regardless of markers. While the Tushare demo, long-history, broad-ETF, and full-A snapshot publishers emit these markers when source limits prove a block, previously saved snapshots retain their original hashes and unmarked permissions—republish a new data snapshot from verified source evidence to make older limit blocks configurable. Saved run requests explicitly write `enforce_price_limits=False` when disabled and omit the field when `True` to preserve existing replay digests.

- **Fee schedules, price caps, and model scope.** Transaction charges are numeric parameters on `Policy`: set `sell_stamp_duty_ppm=0` and `sse_transfer_fee_ppm=0` to remove A-share statutory levies, and set both `commission_ppm=0` and `minimum_commission=0` for zero-commission runs. When modeling non-A-share markets, configure costs, `currency`, trading `sessions`, and timezone-aware `close_times` explicitly so A-share defaults do not carry over. `Policy.buy_cap_ppm` defines an order-level protection cap above the prior valuation mark and remains active even when `enforce_price_limits=False`, leaving pre-open quantity sizing and cash reservation unchanged (see [Targets and the daily account](#targets-and-the-daily-account)). All prices, cash balances, and outputs share the snapshot's single currency without FX conversion, and positions are long-only integral shares across one open and one close per session; intraday execution, short selling, margin, fractional shares, tiered US regulatory fee schedules, and broker-specific account rules are outside the current model.

The runnable [configuration example](../../specs/examples/trading_rules.py) executes the same Friday-close target on a synthetic USD stock with single-share lots, `t0` resale eligibility, and zero fees—first with price-limit enforcement active and then with enforcement lifted:

```bash
uv run python specs/examples/trading_rules.py
```

```text
enforce_price_limits=True fills=0 quantity=0 cash=100.00 USD
enforce_price_limits=False fills=1 quantity=7 cash=30.00 USD
```

### Cash dividends

Because the Rust lot ledger tracks unadjusted share counts and raw execution prices, cash distributions must be supplied as explicit `CashDividend(instrument_id, record_session, ex_session, pay_session, amount_per_share)` records rather than folded into adjusted research prices. Every distribution's `record_session` must belong to the snapshot's in-sample `sessions`, and its lifecycle dates must satisfy `record_session < ex_session <= pay_session`. Distributions that violate this date ordering are unsupported—preserve vendor dates as published and mark affected sessions with `DailyPrice.corporate_action_gap=True` instead of shifting dates to pass validation.

Rust advances each distribution across three accounting transitions:

1. **Record-date close (`record_session`).** At the close of the record session, Rust counts total held units in the ledger, including shares purchased at that morning's open.
2. **Ex-date pre-open (`ex_session`).** Prior to order sizing on the ex-date, Rust subtracts `amount_per_share` from the instrument's previous closing valuation mark (including carried marks on suspended shares) and accrues the total distribution into `dividend_receivable`. Selling shares at the ex-date open preserves this entitlement, and total equity (`cash + market_value + dividend_receivable`) remains continuous across the ex-dividend price drop.
3. **Pay-date close (`pay_session`).** Because the opening auction precedes cash disbursement on the payment date, Rust credits `cash` and clears `dividend_receivable` at the close of `pay_session`, making the proceeds spendable starting at the next trading session's open.

During a run, `day.account.dividend_receivable` exposes unpaid entitlements in the snapshot currency; `result.equity` omits `dividend_receivable` on rows where the balance is zero, while `result.dividends` reports per-distribution share counts, scaled amounts, and payment status (an unpaid entitlement whose `pay_session` falls after the sample end remains in closing equity). Because `InitialAccount` accepts only opening cash and dated `InitialLot` entries—with no opening receivable field—studies that begin during an unpaid distribution window must extend the snapshot back to `record_session`, initializing the account with the cash and lots held on that date while preserving each lot's `sellable_session`. [Dividend ledger](../../crates/tm-engine/src/snapshot/dividends.rs)

For a concrete trace from the fifteen-year ETF benchmark, the buy-and-hold account held `55,600` sellable units of `510050.SH` and `CNY 31,863.55` in cash at the December 16, 2025 record close, earning `CNY 4,448.00` from a `CNY 0.080` per-unit distribution. At the December 17 ex-date close, `CNY 31,863.55` cash plus `CNY 170,024.80` in ETF holdings plus `CNY 4,448.00` receivable totaled `CNY 206,336.35` in equity; payment at the December 22 close raised cash to `CNY 36,311.55` for the December 23 open. [ETF benchmark and distribution dates](../examples/broad-etf.md#orders-distributions-and-valuation)

## Targets and the daily account

Call `backtest(...)` with either an interactive strategy implementing `on_close(day)` or a precomputed schedule of dated target maps. At each scheduled close, Rust values the portfolio before invoking `on_close` once for that session. A returned target dictionary such as `{"B": Decimal("0.80")}` allocates 80% of account equity to `B`, retains the uninvested 20% in cash, and targets zero shares in any currently held instrument omitted from the map. Every weight must be a nonnegative exact multiple of `0.00000001` (`1e-8`) with total weights summing to at most `1` (the SDK rejects excess decimal precision); returning `{}` requests a full exit to cash, whereas returning `None` leaves the previously active target batch in force. Decisions made at a session's close first become eligible at the following trading session's open, and a target returned on the final sample session is recorded against `next_session`.

Before inspecting the next morning's opening prices, the Rust planner executes **order-quantity determination** and **cash reservation** as two distinct steps:

1. **Setting aside uninvested cash and establishing the buy budget.** Using opening account equity valued at the previous session's closing marks (adjusted for any ex-date dividend), Rust sets aside the uninvested target fraction `(1 - sum(weights)) * equity`—rounded up to the nearest `0.01` currency unit—as a protected cash reserve. Only settled cash already on hand above that reserve enters today's buy budget; proceeds from sales executed at today's open enter the buy budget at the **next** session's open.
2. **Finalizing whole-lot order quantities and per-order cash reserves.** Rust divides each target's equity allocation by its prior valuation mark and rounds down to whole lots. Planned sell quantities are capped by currently sellable shares (allowing an odd-lot remainder only when exiting an entire settled position to zero). Planned buy quantities are capped by the whole lots affordable from the available buy budget when priced worst-case at the protection cap (`buy_cap_ppm` above the prior mark, rounded down to `tick_size`) plus full fees and slippage; any residual budget that can fund extra whole lots is allocated greedily to the largest remaining mark-value deficit, breaking ties deterministically by `instrument_id`. Consequently, **every order's share quantity and per-order reserved cash become final before Rust reads the session's opening prices or trading permissions.**

To see how a blocked sale interacts with this budget across sessions, suppose a portfolio holds `8,000` shares of `A` (closing at `CNY 10`) and `CNY 20,000` cash on Friday (`CNY 100,000` total equity) and targets 80% `B`. Rust sets aside `CNY 20,000` as the uninvested 20% cash reserve, leaving zero upfront cash budget for `B` until `A` is sold. If `A` opens at its lower price limit on Monday and cannot be sold, those `8,000` shares remain in the account and the active batch retries at subsequent opens (up to `Policy.max_attempts`); once `A` sells on Tuesday, its net proceeds fund `B`'s purchase on Wednesday morning. The four-session Rust fixture verifies exact cash balances, commissions, buy reservations, and T+1 lot unlocking across this sequence. [Rust account fixture](../../crates/tm-engine/tests/snapshot_backtest.rs)

| Boundary | Implementation |
|---|---|
| Run settings and wire records | [wire.rs](../../crates/tm-engine/src/snapshot/wire.rs) defines `SnapshotData`, `DailyRow`, `Permission`, `SnapshotRun`, `InitialLot`, `TargetBatch`, `Policy`, and `SnapshotResult`. Wire prices, money, and weights are exact fixed-point integers scaled by `100,000,000` (`10^8`), while share quantities are exact integers. |
| Market index | [market.rs](../../crates/tm-engine/src/snapshot/market.rs) indexes daily rows, dividend schedules, and the venue calendar once when a snapshot opens; each subsequent backtest allocates an isolated `SnapshotSession`. |
| Pre-open sizing | [sizing.rs](../../crates/tm-engine/src/snapshot/sizing.rs) computes ex-dividend-adjusted target lots from prior marks, sets aside uninvested target equity, budgets buys at `buy_cap_ppm` including fees and slippage from cash already on hand, and resolves equal-deficit ties by `instrument_id`. |
| Native extension | [tm-python](../../crates/tm-python/src/lib.rs) hosts the verified `PreparedMarket` across runs and invokes Python callbacks after each closing valuation. [Saved-request reader](../../crates/tm-runner/src/snapshot.rs) · [Replay tests](../../tests/test_replay.py) |
| Opening execution | [execution.rs](../../crates/tm-engine/src/snapshot/execution.rs) validates side-specific opening permissions, buy caps, and reserved cash across all planned orders before posting fills to the ledger; if any planned order lacks valid opening evidence, no orders in that attempt execute. |
| Daily loop and ledger | [mod.rs](../../crates/tm-engine/src/snapshot/mod.rs) implements `run_snapshot(&SnapshotData, &SnapshotRun)`, driving T+1 lot settlement, dividend accrual, pre-open sizing, opening execution, and closing valuation while pinpointing the earliest session and instrument on `insufficient_data`. |
| Python API | [backtest package](../../python/trademaster/backtest/__init__.py) validates target precision and initial holdings (`InitialAccount` / `InitialLot`), configures `Policy` (fees, slippage, `buy_cap_ppm`, `max_attempts`, and `enforce_price_limits`), dispatches native execution, and formats `BacktestResult`. |

## Run and inspect a target

After installing the workspace with `uv sync --locked` (see the [command reference](commands.md)), run this self-contained example from the repository root. The synthetic data snapshot uses 100-share A-share lots, and Friday's target decision first becomes eligible at Monday's open:

```python
from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory

from trademaster.backtest import DailyPrice, Instrument, Snapshot, backtest

friday, monday = date(2024, 1, 5), date(2024, 1, 8)
with TemporaryDirectory() as market_dir:
    market = Snapshot.create(
        market_dir,
        sessions=(friday, monday),
        next_session=date(2024, 1, 9),
        instruments=[Instrument("A")],
        daily=[DailyPrice(day, "A", 10, 10, evidence="synthetic") for day in (friday, monday)],
    )
    result = backtest(
        market,
        targets={friday: {"A": Decimal("0.50")}},
        initial_cash=10_000,
        detail=True,
    )
    print(result.summary["status"], result.fills[0]["session"], result.fills[0]["quantity"])
```

Running the snippet prints the completion status, execution date, and filled share quantity:

```text
completed 2024-01-08 400
```

Although 50% of `CNY 10,000` at Friday's `CNY 10` close corresponds to `500` desired shares, Rust first sets aside the uninvested `CNY 5,000` cash reserve, leaving a `CNY 5,000` buy budget. Priced worst-case at the default 2% buy cap (`CNY 10.20` per share) plus commission, `CNY 5,000` covers four 100-share lots (`400` shares)—and that final quantity is already locked in before Monday's `CNY 10.00` opening price is observed.

Inside an `on_close(day)` callback, `day.account.cash` and `day.account.equity` expose `Decimal` amounts in the snapshot currency, `day.account.quantity(symbol)` and `sellable_quantity(symbol)` report integral shares, and `day.fills` lists fills executed since the preceding callback in execution order (populated regardless of `detail`). Retaining a reference to `day` preserves that close's point-in-time balances; if closing valuation fails, the run halts before `on_close` is invoked, and uncaught Python exceptions propagate directly to the caller.

Strategies may run interactively with `schedule="daily_close"` or `"week_end"`, or supply precomputed targets as a date-keyed mapping, a sequence of `TargetBatch` objects, or an Arrow table containing `decision_session` and `weights` (plus optional `eligible_session`, `batch_id`, and `snapshot_id` columns). An empty Arrow table submits zero decisions, whereas a dated row with an empty `weights` map exits to cash; when multiple batches become eligible at the same open—or an older decision arrives after a newer batch has already taken effect—the newer `decision_session` wins and superseded batches are marked `replaced`. Use `initial_cash` for cash-only starts or `initial_account` to seed cash alongside dated `InitialLot` positions (any future `sellable_session` within the snapshot range must be a valid trading session, while lots unlocking after `next_session` contribute to equity but remain locked throughout the run).

`BacktestResult.equity` and `result.summary` convert monetary figures to `Decimal` in the snapshot currency, while `result.raw` retains exact scaled Rust integers. Summary output reports valued versus requested session counts, cumulative transaction costs, and batch outcome tallies; passing `detail=True` additionally populates `attempts`, `orders`, `fills`, and daily `positions` (inspect the corresponding `attempts` entry and its `shortfalls` list whenever a batch ends as `expired`). See the [Python SDK tests](../../tests/test_snapshot_backtest.py) for callback-versus-table equivalence and the [same-market comparison](../../python/trademaster/e2e/compare_rebalance.py) ([regression suite](../../tests/test_rebalance_comparison.py)) for cross-engine trade and balance alignment.

### Missing prices and opening evidence

An OHLCV bar or vendor identifier never suffices on its own to prove that an instrument could trade through a halt or auction block. While `DailyPrice` defaults `buy_at_open` and `sell_at_open` to `"yes"` for concise synthetic fixtures, production snapshot loaders must set any unverified side to `"unknown"`, set confirmed halts or limit blocks to `"no"` with an explicit block reason, and emit `"yes"` only alongside non-empty supporting `evidence`.

At the close of each session, Rust builds a fresh valuation map from raw closing prices. When a security is halted (`suspended=True`) and carries a non-empty `evidence` reference, Rust carries forward its previous session's valuation mark (if one exists) and flags `carried_mark=True` on the detailed `DailyPosition`; an unexplained missing row or missing close leaves the instrument without a valuation price for that session.

Whenever missing prices, unverified opening status, or unmodeled corporate actions prevent safe accounting, the engine halts with `status == "insufficient_data"` and records the exact session, `instrument_id`, `phase`, and diagnostic `reason` in `result.raw["first_issue"]`:

| Phase | Halting condition |
|---|---|
| `sizing` | An active target or existing holding requires a valuation mark for an instrument absent from the previous session's closing marks; Rust reports `missing_previous_close` before emitting orders, even if the security traded on an earlier date. |
| `open` | A planned order encounters `"unknown"` side permission, lacks non-empty `evidence`, or lacks an opening price—or any held lot or planned order carries `corporate_action_gap=True`. All orders in that attempt remain unexecuted so the ledger never records a partially filled rebalance with unverified exposure. |
| `close` | A held position lacks a valid closing or carried suspension mark (`missing_close`) or carries `corporate_action_gap=True`. Rust preserves fills already completed at that morning's open and stops before publishing closing equity or invoking `on_close`. |

## Save and replay

Calling `result.save(directory)` writes `result.json` alongside a canonical `request.json` referencing the snapshot's market file via a relative path—keep the snapshot and saved-run directories in the same relative layout if relocating artifacts. Replay any saved snapshot request directly through the native Rust runner from the repository root with `uv run tm-replay path/to/request.json`, or from another working directory using explicit paths:

```bash
uv run --project /path/to/TradeMaster tm-replay /path/to/run/request.json
```

Before executing a single session, `tm-replay` recomputes the market JSON digest and validates the request's `snapshot_id` against the snapshot manifest; reopening the snapshot in Python additionally verifies the feature Parquet digest before rerunning strategy callbacks. [File protocol](../../crates/tm-runner/src/snapshot.rs) · [Side-by-side run guide](../examples/rebalance-comparison.md)
