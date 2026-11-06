# Historical data sources

## Prepare the TuShare demo

Use the five-stock walkthrough to prepare a new TuShare Pro cache or select an existing one, then run the snapshot and event examples. A new cache requires `TUSHARE_TOKEN` in the shell environment. Keep the token out of source files and saved output. The long-history ETF example has a separate preparer that also collects cash distributions. [Five-stock setup and offline runs](../../../docs/examples/tushare-strategies.md#run-the-examples) · [ETF benchmark](../../../docs/examples/broad-etf.md)

## Prepare a US-stock data snapshot

Use the bundled [IndexOne data source](../../../docs/reference/indexone.md) to acquire prices, company financials, security reference data or FX, or use the user's chosen CSV, Parquet file or API. Map the observations through `Snapshot.create` using the table below. IndexOne acquisition preserves source evidence; the snapshot loader still supplies calendar, opening-status and corporate-action coverage. Store source extracts in Parquet and keep API credentials in environment variables. The [US-stock walkthrough](../../../README.md#backtest-a-us-stock-strategy) shows how to run and replay the resulting snapshot.

Configure each US stock explicitly: `currency="USD"`, `venue="nyse"` or `"nasdaq"`, `buy_lot_size=1`, and the applicable `tick_size`. Select `settlement` and pass an explicit `Policy` using the [trading-rule contract](../../../docs/reference/snapshot-backtest.md#configurable-trading-rules), which defines fees, price-limit enforcement and the model's supported scope.

Use the actual exchange calendar, including holidays and early closes. Pass timezone-aware `close_times` for each session using `ZoneInfo("America/New_York")`, with actual local closing times so daylight-saving changes are represented. Supply later decision `cutoffs` only when the strategy needs them.

## Import a CSV, Parquet file or another API into a backtest

Write a Python loader that translates the vendor’s records into `trademaster.backtest` types, then calls `Snapshot.create` once. The strategy can open the resulting directory just like a TuShare snapshot. The [full-A Parquet importer](../../../specs/examples/prepare_all_a_snapshot.py) is a working example.

| Snapshot input | What to map |
|---|---|
| `sessions`, `next_session` | Supply unique trading dates in order and the actual next trading date after the sample. The calendar includes market sessions on which an individual stock was suspended. |
| `close_times`, `cutoffs` | Map each session to its actual timezone-aware close and decision time. |
| `Instrument` | Use a stable ID across all tables and the SDK’s lowercase venue code, as in `Instrument("600036.SH", venue="sse")`. Map the lot size, price tick, settlement and historical listing interval. `delisted_on` is the first date outside the investable universe. |
| `DailyPrice` | Supply unadjusted open and close for fills and valuation; put the adjusted signal price in `adjusted_close`. Explicitly set `buy_at_open` or `sell_at_open` to `"unknown"` when the source does not establish that side's status. Record evidence for known permissions and a reason for each block. Follow the [opening-evidence contract](../../../docs/reference/snapshot-backtest.md#missing-prices-and-opening-evidence). |
| `Feature` and `CashDividend` | Give each feature its actual timezone-aware `available_at` and version. Map supported distributions under the [cash-dividend contract](../../../docs/reference/snapshot-backtest.md#cash-dividends). Preserve source dates and flag unsupported corporate actions with `DailyPrice.corporate_action_gap`. |
| `source_evidence` | Record the source identifier and file hashes or API extract version so the same input can be recovered. |

Once the vendor records are mapped to these types, publish them with the public SDK.

```python
from trademaster.backtest import Snapshot

market = Snapshot.create(
    output_directory, sessions=trading_dates, next_session=following_trading_date,
    instruments=instruments, daily=daily_prices, features=features,
    cash_dividends=distributions, source_evidence=source_ids,
    close_times=session_closes, cutoffs=decision_times,
)
```

Test the loader with a small vendor fixture. Reopen the snapshot, check when a feature first becomes visible, and compare a blocked order and the next eligible fill with the source rows. Use a confirmed suspension and a missing status as separate cases. [Snapshot contract](../../../docs/reference/snapshot-backtest.md#market-inputs-and-decision-timing) · [Importer tests](../../../tests/test_prepare_all_a_snapshot.py)

## Add a provider for cached point-in-time queries

Use this path when researchers also need `ensure(request)` and `query(request, as_of=...)` across datasets. Implement `CanonicalDataProvider.fetch(request, *, coverage_keys, page_token) -> ProviderPage` and pass the instance as `provider=` to `CacheFirstDataPortal`. The bundled `TushareProvider.from_environment` and the test provider show the two sides of this interface. [Provider contract](../../../python/trademaster/data/coverage.py) · [TuShare implementation](../../../python/trademaster/data/tushare_provider.py) · [Portal tests](../../../tests/test_coverage_portal.py)

Map vendor rows to the dataset’s registered Arrow schema. Return the partition key, exact covered keys and next page token with each page. For trusted independent imports, use a separate data root and set `canonical_source_policy="trusted_imports"` on `DataConfig`. The default stock and ETF policy verifies TuShare raw and staging records. Test pagination, cached reads and publication cutoffs with a local provider fixture. [Data configuration and provenance](../../../docs/reference/data-and-research.md) · [Data contribution guide](../../../python/trademaster/data/AGENTS.md)
