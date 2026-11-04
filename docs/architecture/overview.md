# How a TradeMaster run works

Python prepares historical prices, factors and decisions. Rust determines what can trade, posts fills and fees to the lot ledger, and values the account. There are two ways to run the same strategy decisions.

| Execution path | What happens at the open | Saved output |
|---|---|---|
| Daily portfolio snapshot | At each scheduled close the Rust extension passes the account and recent fills to Python. The strategy specifies the whole target portfolio. A dated table can supply the same targets. Rust sizes orders using previous closing marks, reserves the uninvested cash and fixes buy quantities before checking opening prices. Proceeds from today's sales enter the next session's buy budget. | `BacktestResult` contains daily equity and the status of each target. Detailed output includes attempts, orders, fills and positions. `result.save()` writes a replayable request and the Rust result. [Snapshot API](../reference/snapshot-backtest.md) · [Daily execution diagram](../../specs/assets/code-architecture-refactor.svg) |
| Saved market events | Python sends dated signals, market events and instrument settings. At an eligible open, the scheduled strategy converts target weights to shares using opening marks. It processes sales before buys and can use their proceeds that morning. | The Python orchestrator can build a bundle containing the request, result, source evidence, report inputs and HTML. [Event runner](../reference/execution.md) |

The [saved-event workflow](trademaster-overview.svg) follows a run from provider data to the report bundle. The diagram below shows what happens during a trading session.

![An event-runner session from T+1 settlement through opening orders and fills to the closing account value](trademaster-v1-architecture.svg)

## Data, factors and event-run artifacts

- The data portal reads TuShare observations, keeps them in Parquet and indexes their coverage in DuckDB. A data snapshot identifies its requests and source files by hash. A point-in-time query selects records available by its cutoff.
- Factor definitions identify code, parameters and dependencies. A strategy selects instruments from scores computed at its decision time. The evaluator joins these scores to returns realized over the following holding period. Parquet stores the values, and DuckDB indexes definitions, materializations and evaluations. [Factor and label data flow](trademaster-data-flow.svg)
- On the event path the Python runner submits scheduled signals. Rust callers can implement `StrategyAdapter` directly to emit order intents inside the same event loop.
- Reports compare the saved account values with the selected benchmark. Historical sector and size exposures use the classification effective at each observation.
- Records use run IDs, event sequence numbers and, where needed, a subsequence number. The standard run format validates eleven related Parquet tables, including the account-state hash chain. File hashes, row counts and run IDs go into the run manifest and are checked again on read.
- The v3 strategy bundle lists the inputs and outputs of one run. Starting from a report, a reader can find its stock selections, saved factor values, data snapshots, and the request and result from Rust.
- Calendars, trading rules, settlement, fees and exchange rates have separate interfaces. The settlement calendar supplies eligible trading dates for each venue.

## A weekly momentum signal through the event runner

Consider a strategy that ranks stocks by 60-day momentum on Friday and targets the top twenty for the next open.

1. `DataPortal.snapshot()` records the data request, coverage, availability rules and Parquet files for prices, trading status, suspensions and price limits.
2. `ManagedFactorRegistry` checks factor dependencies and rejects circular references. `FactorManager` builds a cache key from the definition, input snapshot and tables, universe, date range and any parent factors.
3. Read back the saved factor values and verify their hashes, columns, row counts and metadata before selection. Strategies can use the composite scores directly.
4. The signal records Friday's close as its decision time and the following open as its earliest execution time.
5. The runtime distinguishes the open, close and settlement. At the open it handles target-weight sales before buys and uses instrument ID to order each side.
6. A fill produces accounting entries. Rejected and expired orders keep their reasons. A valid order outcome with no account change produces an empty `LedgerEffect`.
7. Settlement unlocks eligible T+1 lots. Posting records and account snapshots preserve cash, holdings, lots and net asset value.
8. Reports derive performance, costs, historical exposures and benchmark comparisons from the saved records. Factor research separately produces IC, rank IC, returns by quantile, long-short spread and turnover.

## Data gaps and execution evidence

The downloader requests the missing dates and reports missing permissions, incomplete pages or unexpected columns. A suspension record explains a missing daily bar. The research panel counts a missing bar with no matching suspension record as a data gap. Execution also needs the day's trading status and price limits. For ETFs, an exchange rule recorded in the request can supply the price limits.

Changing a factor's code or parameters under an existing ID and version requires a new version. The factor manager rejects damaged cached values, conflicting definitions or missing parents. A signal that uses a day's closing price can first trade in a later session. When posting a fill fails, the ledger rolls back the entire change.

The strategy-specific industry workflow has documented exceptions for historical coverage and adjusted execution prices. See its [data limitations](../../python/trademaster/strategies/industry_fundamental_top5/README.md) when interpreting results. The [portfolio snapshot design](../../specs/20261104-code-architecture-refactor.md) records the reasons for deciding target weights at the close and reserving cash before the next open.
