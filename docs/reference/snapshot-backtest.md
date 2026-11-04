# Daily portfolio snapshot API

Use a snapshot backtest to ask what a long-only portfolio could hold after a sequence of target weights. The same saved market can drive many strategies. A close-time strategy reads prices and published features, a pre-open plan fixes quantities and reserves cash, and the opening price determines whether the planned orders can fill. The [event runner](execution.md) accepts an explicit sequence of market events and scheduled signals. Its open-based sizing differs from the snapshot calculation.

## Market inputs and decision timing

| Interface | Meaning |
|---|---|
| `Snapshot.create(directory, *, sessions, next_session, instruments, daily, features=..., cash_dividends=...)` | Publishes a manifest, a market JSON object and a feature Parquet object. The manifest binds both object hashes, the decision cutoffs, listing intervals and source evidence. Supply timezone-aware `close_times` and decision `cutoffs` for other session calendars. The default is 15:00 Shanghai time. A cutoff can be later than the close on the same local trading day. |
| `Snapshot.open(directory)` or `Snapshot.load(directory)` | Recomputes the manifest identity and checks the saved object hashes. Use the same directory to rerun strategies against the same market. |
| `Snapshot.from_demo_market(market, directory)` | Adapts the verified local TuShare sample to the snapshot format, including directional opening permissions and adjusted closes for research. |
| `Instrument`, `DailyPrice`, `Feature` | The instrument sets venue, lot, price tick, settlement and listing dates. One daily row supplies raw prices, separate buy/sell permissions, supporting evidence and optional reasons. A blocked order without a supplied reason is recorded as `blocked_at_open`. A feature carries its name, version, value and publication time. |
| `snapshot.day(session)` → `DecisionDay` | Exposes the instruments listed that day. `closes(symbol, adjusted=False, limit=None)` returns prices through the decision close. A positive `limit` reads just the last N sessions, keeping missing values in place. `scores(name)` selects the latest value published by the cutoff for each eligible instrument. |

Supply raw and adjusted closes that were available at the listed close time. For observations released later, supply a feature with its actual publication time. The cutoff applies to what Python can use to make a decision. Rust separately reads that snapshot's raw execution and valuation rows. A feature published after Friday's cutoff first becomes usable at a later decision. Set `DailyPrice.corporate_action_gap` for an unexplained change in raw price, such as a split without a share adjustment. A run holding that security stops at the first affected session. Each strategy callback receives a `BacktestDay` with the decision-day market view, the closing account and fills since the previous callback. [Snapshot implementation](../../python/trademaster/backtest/snapshot.py) · [Cutoff and snapshot integrity tests](../../tests/test_snapshot_backtest.py)

### Cash dividends

Pass a `CashDividend(instrument_id, record_session, ex_session, pay_session, amount_per_share)` for each distribution. The snapshot's `sessions` must include `record_session`, and the dates must satisfy `record_session < ex_session <= pay_session`. Rust counts the units held at the record close, including purchases that day. Before sizing on the ex-date, it subtracts the dividend per unit from the previous valuation price and adds the entitlement to equity. Selling at the ex-date open preserves the entitlement.

Closing equity equals cash plus the market value of holdings plus outstanding dividend receivables. On the pay date, Rust credits cash at the close. The next open can spend it. `day.account.dividend_receivable` exposes the unpaid amount in CNY. A daily row in `result.equity` omits `dividend_receivable` when it is zero. `result.dividends` lists the entitlements and payment status in Rust wire units. A receivable remains in equity if payment falls beyond the sample. [Dividend ledger](../../crates/tm-engine/src/snapshot/dividends.rs)

To start a study on an ex-date with an existing entitlement, extend the snapshot back to the record session. `InitialAccount` accepts only cash and dated lots, with no field for opening receivables. If the record session is the first day, initialize it with the actual cash and lots held at that close, preserving each lot's sellable date. A target submitted at that close first trades at the following open.

In the ETF benchmark, the buy-and-hold account held 55,600 sellable units of `510050.SH` and CNY 31,863.55 at the December 16, 2025 record close. The CNY 0.080 distribution per unit earned it CNY 4,448.00. At the December 17 close, CNY 31,863.55 in cash + CNY 170,024.80 in ETF units + CNY 4,448.00 receivable gave it CNY 206,336.35 in equity. The December 22 payment brought cash to CNY 36,311.55, available for the December 23 open. [ETF benchmark and distribution dates](../examples/broad-etf.md#orders-distributions-and-valuation)

## Targets and the daily account

Pass a strategy with `on_close(day)` or a dated target map to `backtest`. At each scheduled close Rust values the account before calling the strategy. The SDK calls `on_close` once per scheduled close. A map such as `{"B": Decimal("0.80")}` targets 80% of account value in B, keeps the balance as cash and targets zero shares in omitted holdings. Each weight must be a nonnegative multiple of `0.00000001`, and the weights must sum to at most one. The SDK rejects excess precision. An empty map asks to exit to cash. A `None` decision leaves the existing instruction in force. A target returned by the callback is first eligible at the next open. A decision from the final close is recorded with its next eligible date.

For a concrete example, a portfolio holds 8,000 shares of A and CNY 20,000 cash on Friday. A closes at CNY 10. A target of 80% B leaves CNY 20,000 as the intended cash reserve. When A cannot be sold on Monday because it opens at its lower limit, those shares stay in the account. A later successful sale increases the cash available for the following attempt. The four-session fixture checks the individual cash balances, commissions, buy reservations and T+1 sellability. [Rust account fixture](../../crates/tm-engine/tests/snapshot_backtest.rs)

| Boundary | Implementation |
|---|---|
| Run settings and wire records | [wire.rs](../../crates/tm-engine/src/snapshot/wire.rs) defines `SnapshotData`, `DailyRow`, `Permission`, `SnapshotRun`, `InitialLot`, `TargetBatch`, `Policy` and `SnapshotResult`. Wire prices, money and weights use integers scaled by 100,000,000. Quantities are whole shares. |
| Market index | [market.rs](../../crates/tm-engine/src/snapshot/market.rs) prepares the market rows and trading calendar once per open snapshot. Each backtest starts a separate Rust session. |
| Pre-open sizing | [sizing.rs](../../crates/tm-engine/src/snapshot/sizing.rs) divides each target's share of opening equity by its previous session's valuation price, adjusted for any ex-date cash dividend, and rounds down to whole lots. It reserves the uninvested portion of equity and budgets buys at the cap including fees from cash already available. Stable instrument ordering resolves equal allocation choices. |
| Native extension | [tm-python](../../crates/tm-python/src/lib.rs) loads a verified market and calls the strategy after Rust values the account. The same market can serve several runs, each with its own ledger. [Saved-request reader](../../crates/tm-runner/src/snapshot.rs) · [Replay tests](../../tests/test_replay.py) |
| Opening execution | [execution.rs](../../crates/tm-engine/src/snapshot/execution.rs) checks side-specific evidence for the planned orders, tests the cap and cash reserve, and posts fills. If an opening is unknown for any planned order, the other orders in that attempt remain unexecuted. |
| Daily loop and ledger | [mod.rs](../../crates/tm-engine/src/snapshot/mod.rs) implements `run_snapshot(&SnapshotData, &SnapshotRun)`. It settles the ledger, sizes an active batch using previous closes, checks each side against opening evidence, applies fills and values positions at the close. Insufficient evidence identifies the first affected session and instrument. |
| Python API | [backtest package](../../python/trademaster/backtest/__init__.py) validates targets and initial holdings, dispatches the snapshot request and returns `BacktestResult`. `Policy` supplies fees, slippage, a buy price cap and the maximum number of attempts. `InitialAccount` and `InitialLot` let a run start with shares already held. |

## Run and inspect a target

Install the package with `uv sync --locked` as shown in the [command reference](commands.md). Run this Python example from the repository root. The market is synthetic, one lot is 100 shares, and the Friday decision can first trade on Monday.

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

The example prints the status, fill date and share count.

```text
completed 2024-01-08 400
```

The 50% target initially calls for 500 shares. The remaining CNY 5,000 stays in cash, so the buy budget is CNY 5,000. At the default buy cap of CNY 10.20 plus fees, that budget covers four lots. The order quantity is already set when the observed opening price of CNY 10 becomes available.

At a scheduled close, `day.account.cash` and `day.account.equity` are currency amounts. `day.account.quantity(symbol)` and `sellable_quantity(symbol)` count shares. `day.fills` contains actual fills since the previous callback in execution order. These values are available with either detail setting. Saved day views keep their original balances. A failed valuation ends the run before that close’s callback. A strategy error propagates to the caller.

For recurring decisions, define `on_close(day)` and choose `schedule="daily_close"` or `"week_end"`. For precomputed decisions, pass a mapping by decision date, a sequence of `TargetBatch` records, or an Arrow table with `decision_session` and `weights`. The table can carry `eligible_session`, `batch_id` and `snapshot_id`. Each Arrow row represents a decision. An empty table submits no decisions. A dated row with an empty `weights` map targets cash. If two batches become eligible at the same open, Rust applies the newer decision and marks the older one `replaced`. An older decision that arrives after a newer one took effect is also marked `replaced`.

Cash-only runs take `initial_cash`. For an opening portfolio, supply `initial_account` with cash and dated lots. A future sellable date within the supplied calendar must be a trading session. A lot that unlocks beyond the calendar contributes to daily equity and remains locked for the run.

`BacktestResult.equity` and the summary monetary values are in CNY. `result.raw` preserves the Rust units. The summary includes valued versus requested sessions, total costs and batch outcome counts. `detail=True` also retains attempts, orders, fills and positions. Inspect the associated attempt record when a target expires. The [Python SDK tests](../../tests/test_snapshot_backtest.py) compare callback and precomputed inputs. The [same-market comparison](../../python/trademaster/e2e/compare_rebalance.py) matches decision dates and weights before comparing fills, holdings and timings. [Comparison regression](../../tests/test_rebalance_comparison.py)

### Missing prices and opening evidence

Each session supplies a new set of valuation prices. An instrument normally uses its raw close. For a row with `suspended=True` and a nonempty `evidence` reference, Rust carries the previous session's valuation price if one exists and sets `carried_mark=True` on the detailed position. An unexplained missing row or close leaves the instrument without a valuation price for that session.

If the run stops with `insufficient_data`, inspect `result.raw["first_issue"]` for the session, instrument, phase and reason.

| Phase | What stopped the run |
|---|---|
| `sizing` | A target needs a price for an instrument missing from the previous session's valuations. Rust reports `missing_previous_close` before sending orders, even if the instrument traded at an earlier close. |
| `open` | A planned order has an unknown side permission, lacks supporting evidence or needs a missing opening price. No orders fill in that attempt. A flagged corporate-action gap on an existing holding also stops the run before trading. |
| `close` | A holding has no valid valuation (`missing_close`) or has a flagged corporate-action gap. Rust keeps that day's completed fills and stops before publishing closing equity or calling the strategy. |

## Save and replay

`result.save(directory)` writes `result.json` and a `request.json` that refers to the market file relative to the request directory. Keep the snapshot and request directories in the same relative positions when moving them. From the TradeMaster repository root, run `uv run tm-replay path/to/request.json`. From another directory, pass absolute paths for the project and the request.

```bash
uv run --project /path/to/TradeMaster tm-replay /path/to/run/request.json
```

The engine checks the market hash and the request's snapshot identity against the manifest. Reopening the snapshot in Python also checks the feature Parquet before running the strategy again. [File protocol](../../crates/tm-runner/src/snapshot.rs) · [Side-by-side run guide](../examples/rebalance-comparison.md)
