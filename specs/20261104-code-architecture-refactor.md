# Code Architecture Refactoring and Optimization

**Implemented November 4, 2026.** The Python strategy API, Rust snapshot runner and three real-data examples are available. The long-history industry strategy still runs on the event engine. [Runnable walkthrough and measured comparison](../docs/examples/rebalance-comparison.md)

The [Python and Rust interface](20261105-python-rust-backtest-interface.md) updates the integration described below. It runs the daily loop in process and gives scheduled strategies the closing account and recent fills.

Run several strategies on the same history and compare their returns after costs, maximum drawdowns, and how often they miss a target. Each run takes a data snapshot, a strategy or table of target weights, a starting account and trading assumptions.

![One data snapshot supplies strategy inputs to Python and daily prices to the Rust portfolio calculation.](assets/code-architecture-refactor.svg)

## How a strategy sets its target

In the event runner at `80834872`, a custom backtest supplies dated signals, instrument settings, session opens and market events to the Rust runner. Python can already send target weights, but the scheduled strategy accepts a batch only when its weights sum to zero or one. A single 80% target for B is rejected. The industry strategy also finds stocks that dropped out of its selection and schedules zero-weight exit signals for up to 20 opens. [Runner request](../python/trademaster/e2e/__init__.py#L119-L151) · [Weight validation](https://github.com/Obscurepawn/TradeMaster/blob/80834872a29bbbd083bd5edfecbdf3fec0b19099/crates/tm-engine/src/lib.rs#L154-L181) · [Exit scheduling](../python/trademaster/strategies/industry_fundamental_top5/real.py#L1010-L1035)

With the snapshot interface, the strategy returns the portfolio it wants to hold. Rust works out what the account can trade each day and retries missed trades while the target is active. Qlib's `WeightStrategyBase` gives the order generator a set of portfolio weights. RQAlpha's `order_target_portfolio_smart` takes a whole-portfolio target and accepts separate prices for sizing and trading. TradeMaster uses both ideas to run directly on daily snapshot rows. [Qlib strategy implementation](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/qlib/contrib/strategy/signal_strategy.py#L344-L371) · [RQAlpha portfolio API](https://rqalpha.readthedocs.io/zh-cn/latest/api/base_api.html#rqalpha.api.order_target_portfolio_smart)

## Writing a strategy

The [four-session example](../README.md#run-the-example) creates the snapshot used below. From the repository root, inspect the strategy's Friday decision.

```python
from datetime import date

from specs.examples.weekly_rotation import WeeklyRotation
from trademaster.backtest import Snapshot

snapshot = Snapshot.open("artifacts/blocked-rotation/snapshot")
weights = WeeklyRotation().on_decision(snapshot.day(date(2024, 1, 5)))
print(weights)  # {'B': Decimal('0.80')}
```

Pass one strategy or one target table per run. A strategy implements `on_decision(day) -> dict[str, Decimal] | None`. The `schedule` selects its decision dates. A target table already includes those dates. Use `initial_cash` for a cash start, or `initial_account` for a portfolio with cash and existing holdings. Each starting lot specifies an instrument, quantity and sellable date. At each opening, Rust unlocks lots due that session before sizing orders. A lot with a sellable date beyond the supplied calendar remains in holdings and contributes to equity throughout the run. Its sellable quantity stays at zero.

The summary includes net return, maximum drawdown, turnover, total costs, the share of targets that expired with a shortfall, and data coverage. The daily equity series contains cash, the market value of holdings and their sum. With `detail=True`, the same calculation also returns attempts, orders, fills and daily positions. Both modes record the snapshot ID, targets and assumptions. Use `result.save(directory)` to save a replayable request and its Rust result alongside the snapshot. The event command produces an HTML report.

The [strategy demo](examples/weekly_rotation.py) runs with `python3 specs/examples/weekly_rotation.py`. Its `on_decision(day)` function sees only data available at the decision time. It reads `day.scores("rotation_score")`, ignores missing and nonfinite scores, and breaks ties by instrument ID. If A scores 0.35 and B scores 0.72, it returns `{"B": Decimal("0.80")}`.

| Strategy output | Meaning |
|---|---|
| `{"B": Decimal("0.80")}` | Replace the entire target portfolio. Assign 80% to B, reserve the other 20% as cash and try to exit existing positions omitted from the target. |
| `{}` | Aim for an all-cash account. |
| `None` | No new decision. Continue any active target until it is reached, replaced or expires. |

Weights are long-only and sum to at most one. For precomputed weights, pass `targets=weight_batches` to `backtest`. In an Arrow table, one row is one decision. Its `weights` map holds all the instrument weights for that date. The request identifies the shared snapshot.

| Target input | Expected result |
|---|---|
| A table with zero rows | The strategy submits no decisions. Starting shares stay in the account. |
| One row with a decision date and `weights=[]` | The strategy targets cash. Rust tries to sell existing holdings at the eligible open. |
| January 8 and January 9 decisions both eligible on January 10 | The January 9 decision sets the opening target. The January 8 batch is marked `replaced` with zero attempts. |

Each batch has a unique ID and decision session. A callback's decision is eligible at the next open. A precomputed batch can specify a later eligible session from the calendar, including the first session after the sample. When a delayed decision becomes eligible after a newer one has taken effect, the older batch is marked `replaced`.

## Reading the snapshot

A **data snapshot** is a named version of the tables for a study. Its manifest lists the files, their hashes, the period covered and how the tables were prepared. The backtest opens these files directly. It reads the rows in trading-day order and identifies each stock by a stable ID. Trading units, settlement rules and the opening buy and sell status all come from the snapshot.

| Snapshot data | Fields | Reader and permitted time |
|---|---|---|
| Sessions and instruments | Ordered trading days, decision cutoff, listing dates, lot size, price increment, and number of sessions until purchased shares become sellable. Shared values can be stored once in the manifest. | Python finds the last trading day of each week. Rust uses the same sessions to count retries and unlock shares. |
| Strategy inputs | Instrument ID, feature name, value, and `available_at`, with the historical stock universe and factor version. | The strategy sees rows with `available_at <= decision_at`. A statement released after Friday’s decision cutoff first enters a later decision. |
| Daily prices and trading status | At most one row per session and instrument. Raw open and close, `buy_at_open` and `sell_at_open` (yes, no or unknown), an evidence reference for a known permission, and an optional reason for each blocked side. | Sizing sees the most recent known closing prices. After quantities are set, the execution function reads today's open and side-specific status. Today's close enters the account valuation last. |
| Valuation and cost assumptions | Price adjustment and corporate action coverage, plus the run's fees, slippage, price cap and number of attempts. | The result records the actual choices. Strategies can share assumptions, or a researcher can rerun one strategy with higher costs. Initial cash and holdings are supplied separately. |

For example, Monday’s row may record `sell_at_open="no"` with reason `lower_limit`. Rust records the supplied reason on a rejected sell order, or `blocked_at_open` when the reason is omitted. Opening permissions use facts known at the open. The current research panel's `tradable` field checks whether the day's close is valid and does not distinguish buys from sells. Prepare the two opening fields once from prices, limits and status evidence, then reuse the snapshot. [Existing panel fields](../python/trademaster/research/full_a_panel.py#L119-L157) · [Current tradability calculation](../python/trademaster/research/full_a_panel.py#L926-L932)

During a suspension with supporting evidence, the ledger carries forward the last known price and sets `carried_mark` on the detailed position. If a planned trade has unknown opening status, a holding has no valid valuation or the snapshot flags an unaccounted dividend or split, the run reports `insufficient_data` and the first affected session, instrument and phase. An unknown opening blocks all orders for that attempt. If valuation fails after the open, the result keeps completed fills and stops before publishing that close's equity. The snapshot producer must flag corporate actions the ledger cannot account for. The Tushare demo loader rejects periods with a change in adjustment factor or a break in previous-close continuity.

## Rust sizing, execution and accounting

```text
account.settle(session)
plan = size(active_target, account, previous_marks, sizing_policy)
fills, reasons = estimate_open(plan, today.open_columns, cost_policy)
account.apply(fills)
equity = account.mark(today.close_columns)
```

The runner reads the market file once. Sizing uses prior closing marks, execution uses opening prices and side permissions, and valuation uses that day's closing marks.

| From → to | Data and responsibility |
|---|---|
| Python → Rust | Send references to snapshot tables, `TargetBatch[]`, the initial account and assumptions once per run. At each eligible session, unlock shares due for settlement before planning. |
| Sizing → execution | A plan contains the side and quantity for each order, a buy price cap, cash reserved, and any unfilled target left without an order. With account value E and total target stock weight W, reserve `E × (1 − W)` rounded up to the nearest cent. Spend only cash already available above that reserve. Expected proceeds from sales count as zero. |
| Execution → account | Check the buy or sell status, opening price, buy cap and reserved amount. Return a fill (instrument, side, quantity, price and costs) or the reason the order did not fill. The ledger posts each fill and its fees to cash and holdings. |
| Account → results and next session | Value the holdings at the close and carry the actual cash and shares into the next session. Save daily equity in both result modes and positions in detailed mode. Connect each shortfall to its target batch, attempt, instrument and order or fill, if any. |

Size a buy target as `E × weight / last_known_close`, round down to a whole lot, then subtract shares already held. Reduce that difference until the shares are affordable at the price cap including fees. When several stocks compete for cash, allocate in proportion to each remaining shortfall in portfolio value. Use remaining cash for whole lots in descending order of outstanding value, breaking ties by instrument ID. Size each sell against shares that can actually be sold. A complete exit can sell an odd lot when all remaining shares are sellable. Other trades round down to the instrument's lot size.

Fix the order quantities before reading opening prices. A successful sale increases account cash but cannot fund another order at the same open. If a price limit, T+1 settlement or a cash constraint prevents a trade, record the shortfall. Each eligible opening uses one attempt. End the target when it is reached after lot rounding, replaced, out of attempts, or at the end of the sample. Recalculate the reserve on each attempt. A 20% reserve is measured before trading, so actual closing cash can exceed or fall below 20% of closing equity.

For small orders, estimate a full fill once the opening checks pass and charge the configured costs. Modelling queues or the effect of order size on price would require finer data. The expired-target rate divides targets still short at expiry by all reached or expired targets. Count replacements, unfinished samples and data gaps separately. Show “no observations” when the denominator is zero.

## Example: sell A, buy B and keep 20% cash

At Friday's close the account holds 8,000 sellable shares of A at CNY 10 and CNY 20,000 cash. B closes at 20. The strategy ranks B first and submits a single 80% target for B, valid for the next three opens. Both stocks trade in lots of 100. For this example each fill costs CNY 5, slippage is zero, and buys are capped at 102% of the previous close.

| Session | Plan and outcome | Account at the close |
|---|---|---|
| Friday | Value the starting position and register the decision for Monday. | A 8,000 shares, cash 20,000, **equity 100,000**. |
| Monday | Reserve 20,000, leaving no cash for B. Try to sell 8,000 A. A opens and closes at 9, and its snapshot row says selling was blocked at the lower limit. No fill or fee. B stays at 20. | A 8,000 shares, cash 20,000, **equity 92,000**. |
| Tuesday | Reserve 18,400, leaving 1,600. One lot of B requires up to 2,045. Sell A at 9 for net proceeds of 71,995. These proceeds become available for new orders at the next open. B stays at 20. | No shares, **cash and equity 91,995**. |
| Wednesday | Reserve 18,399, leaving 73,596. For 3,600 B shares at the 20.40 cap, reserve 73,445 including the fee. Buy at 20 for an actual cost of 72,005. B closes at 20. | B 3,600 shares, cash **19,990**, **equity 91,990**. |

Cash ends at **21.73%** because another lot would exceed the budget. The CNY 8,010 decline consists of 8,000 lost on A and 10 in fees. The run records three submitted orders, one rejection and two fills. As a test, change Monday's closing price and rerun. Monday's opening orders and fills should match exactly.

## Migration status

| Step | Implementation | Verification |
|---|---|---|
| Publish a shared daily snapshot | [Snapshot](../python/trademaster/backtest/snapshot.py) stores prices and side-specific opening status in fixed-point JSON, and timestamped features in Parquet. Its manifest binds the files, session cutoffs and source evidence to one ID. | [SDK tests](../tests/test_snapshot_backtest.py) cover decision cutoffs, listing dates, missing data and snapshot integrity. |
| Move the daily portfolio calculation into Rust | [Sizing](../crates/tm-engine/src/snapshot/sizing.rs) sets quantities and protects cash before seeing the open. [Execution](../crates/tm-engine/src/snapshot/execution.rs) checks actual opening conditions and applies fills through the existing FIFO lot ledger. The [new CLI command](../crates/tm-runner/src/snapshot.rs) verifies the input. | The [four-session fixture](../crates/tm-engine/tests/snapshot_backtest.rs) reconciles the A-to-B example. The event command still produces the archived results for the original requests. |
| Expose the Python API and migrate the three examples | [backtest](../python/trademaster/backtest/__init__.py) accepts close-time callbacks or precomputed targets. [Three strategies](../python/trademaster/e2e/demo_strategies.py) exercise both execution paths on the same Tushare observations. | [Comparison](../python/trademaster/e2e/compare_rebalance.py) checks the original bundles, target dates, instruments and weights before comparing fills, fees and all 98 closing valuations. Detailed and summary modes agree on daily accounts and totals. |
| Migrate the long-history industry strategy | Next, publish opening trade permissions and corporate-action coverage for its historical universe. Replace its precomputed share counts and exit retries with whole-portfolio targets. | Compare decisions, rejected orders, fees and daily equity against the saved industry run. Measure memory and time on the full history before changing its default command. |

The event loop originally copied price history into each strategy view. At D sessions and N instruments per session those copies can cover roughly `N × D × (D − 1) / 2` rows. The snapshot runner indexes the market rows once, visits sessions in order, and uses the previous closing marks when sizing. Summary mode omits detailed order and position rows. The runner still loads the JSON market into memory for each process. For repeated long runs, a columnar market reader or snapshot reuse across trials would need a separate benchmark. [Original history handling](https://github.com/Obscurepawn/TradeMaster/blob/80834872a29bbbd083bd5edfecbdf3fec0b19099/crates/tm-engine/src/lib.rs#L1403-L1412) · [Daily loop](../crates/tm-engine/src/snapshot/mod.rs)

## Real-data comparison and acceptance tests

The three examples cover weekly cross-sectional selection, a single-stock moving-average crossover and a MACD crossover. All use the same 98 sessions, five stocks and 392 verified Tushare source objects. The two runners receive the same decisions. Differences in fills arise from how they size orders, reserve cash and retry. [Original run](../docs/examples/tushare-strategies.md) · [Observed fills, equity and timing](../docs/examples/rebalance-comparison.md)

The original four-session test first failed without the snapshot runner. Its Python and Rust checks now agree on each fill and daily account. Follow-up regressions caught a difference between the Python and Rust handling of two decisions eligible at one open, and a rejected starting lot whose unlock date fell beyond the calendar. The SDK and CLI now agree on which target traded. The locked lot is valued at the close with zero shares available to sell. [Python API tests](../tests/test_snapshot_backtest.py) · [Rust account tests](../crates/tm-engine/tests/snapshot_backtest.rs)

In a fresh working copy, the README example created its own snapshot and ended with CNY 19,990 cash and CNY 91,990 equity. Replaying the three saved Tushare requests reproduced each complete ledger across 98 sessions. The offline verification run passed 294 Python tests and 56 Rust tests. Three Python tests require live Tushare access. [Four-session example](examples/blocked_rotation.py) · [Real-data comparison regression](../tests/test_rebalance_comparison.py)
