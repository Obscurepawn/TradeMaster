# Weekly momentum across the A-share market

How does a simple cross-sectional momentum screen behave across a 5,551-stock A-share universe once opening price limits, trading suspensions, and buy-price caps constrain execution? Starting with CNY 1 million, the [README strategy](../../README.md#weekly-stock-selection-across-the-a-share-market) rebalances weekly across active Shanghai, Shenzhen, and Beijing listings—finishing two weekly rotations at CNY 1,151,755.88 after CNY 897.12 in trading costs, even as 14 of 63 orders are rejected at the open.

At each week's final exchange close (the data snapshot's as-of cutoff for that decision), the strategy observes all A-shares then listed and ranks them by 20-session back-adjusted return (`adjusted_close[t] / adjusted_close[t - 20] - 1`), targeting 10% portfolio weight in each of the top ten stocks for execution starting at the next trading session's open. Because the trading calendar retains suspended sessions, the 20-session lookback spans the exact same market window for every stock; any stock missing either endpoint is excluded from that week's ranking.

## Run on the historical universe

From the repository root, build the [Tushare research panel](../full-a-factor-research.md) and point the snapshot builder at its published manifest (or pass `--panel-manifest` to use a separately materialized panel covering July 3 through August 14, 2026). The resulting data snapshot ends at the August 13, 2026 close and records August 14 as the next eligible trading session.

```bash
uv run python specs/examples/prepare_all_a_snapshot.py
uv run python specs/examples/all_a_momentum.py artifacts/all-a-momentum/snapshot --output artifacts/all-a-momentum/run
uv run tm-replay artifacts/all-a-momentum/run/request.json
```

The [preparation script](../../specs/examples/prepare_all_a_snapshot.py) verifies the upstream panel manifest and file hashes before writing a data snapshot of the 5,551 stocks listed for at least one session in the window, with `day.instruments` tracking point-in-time listing dates at every close. In the [strategy](../../specs/examples/all_a_momentum.py), `schedule="week_end"` restricts Python callbacks to week-end closes while the Rust engine settles lots, evaluates active orders, and marks the account on every trading session. You can also pass a smaller CNY stock data snapshot to `all_a_momentum.py` to test the same selection rule on a reduced universe.

## From selection to fills

| Decision close | Eligible stocks with both prices / listed | Stocks selected | First eligible open |
|---|---:|---:|---|
| July 31, 2026 | 5,506 / 5,534 | 10 | August 3 |
| August 7, 2026 | 5,513 / 5,539 | 10 | August 10 |

Execution opened on August 3 with an 8,000-share purchase of `001258.SZ` at CNY 12.25. Across the two weekly rebalances, the Rust engine recorded 49 fills and 14 rejected orders: eight buys opened above the 2% buy-price cap, five orders hit trading suspensions, and one buy opened locked at the upper price limit. Both target batches expired after their three allotted opening attempts before reaching exact 10% weights, leaving closing equity at CNY 1,151,755.88 on August 13 with CNY 897.12 in total fees. Replaying `request.json` reproduces the full FIFO ledger bit-for-bit, and an independent cross-check directly from the stock master, raw closes, and adjustment factors matched every selected ticker and target weight on both rebalance dates.

Adjusted closes are used strictly for cross-sectional ranking; order sizing, fills, and daily valuation run on raw unadjusted prices under the SDK's default A-share fee schedule, zero slippage, a 2% buy-price cap, and three opening attempts per target. Two data boundaries guard against unmodeled market states in this sample:

- **Board-specific opening rules:** All A-share boards participate in ranking, but the snapshot marks opening permissions as `unknown` for STAR Market and Beijing Stock Exchange (BSE) listings because this example does not model their board-specific order-size rules. Any planned order that encounters `unknown` opening evidence halts the backtest (`insufficient_data`); in this window, all selected top-decile winners traded on boards with complete opening evidence.
- **Corporate-action continuity:** An unexplained jump in the raw previous-close reference or a missing or changed adjustment factor flags an unmodeled corporate action, halting the run before publishing closing equity for any affected holding.
