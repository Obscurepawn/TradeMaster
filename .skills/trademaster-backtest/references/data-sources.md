# Historical data sources

## Prepare the Tushare demo

Follow the five-stock walkthrough to populate a local Tushare Pro cache or reuse an existing one before running the snapshot and event demos. Creating a new cache requires `TUSHARE_TOKEN` in the shell environment—never hard-code tokens in source files or write them into saved outputs. The fifteen-year broad ETF benchmark provides its own preparation command that also ingests cash distributions. [Five-stock setup and offline runs](../../../docs/examples/tushare-strategies.md#run-the-examples) · [ETF benchmark](../../../docs/examples/broad-etf.md)

## Prepare a US-stock data snapshot

Acquire historical prices, company financials, security reference records, or FX rates via the bundled [IndexOne data source](../../../docs/reference/indexone.md), or load the user's CSV, Parquet, or vendor API extracts, and publish them through `Snapshot.create` using the field mapping below. While IndexOne caches immutable Parquet responses and `ResearchObjectEvidence`, the snapshot loader remains responsible for exchange calendars, opening trading permissions, and corporate-action continuity. Persist raw extracts in Parquet and read API keys exclusively from environment variables. See the [US-stock walkthrough](../../../README.md#backtest-a-us-stock-strategy) to execute and replay the published snapshot.

Configure each US equity instrument explicitly with `currency="USD"`, `venue="nyse"` or `"nasdaq"`, `buy_lot_size=1`, and its applicable `tick_size`. Select the instrument `settlement` rule (`"t0"` for immediate share resale availability) and pass an explicit `Policy` (such as `enforce_price_limits=False` and zero A-share stamp/transfer taxes) per the [trading-rule contract](../../../docs/reference/snapshot-backtest.md#configurable-trading-rules), which defines fee schedules, price-limit markers, and model boundaries.

Supply the actual US exchange session calendar—including market holidays and early closes—and pass timezone-aware `close_times` using `ZoneInfo("America/New_York")` at each session's true local close so daylight-saving transitions are represented accurately. Provide separate decision `cutoffs` only when strategy observation times occur after the market close.

## Import a CSV, Parquet file or another API into a backtest

Write a Python loader that maps vendor records into `trademaster.backtest` domain records and calls `Snapshot.create` once; strategies then open the published directory identically to a Tushare snapshot. Refer to the [full-A Parquet importer](../../../specs/examples/prepare_all_a_snapshot.py) for a complete production loader.

| Snapshot input | What to map |
|---|---|
| `sessions`, `next_session` | Unique trading dates in ascending order plus the first actual trading session after the sample window (including market sessions on which individual instruments were halted). |
| `close_times`, `cutoffs` | Each session's timezone-aware market close and point-in-time decision cutoff. |
| `Instrument` | A stable `instrument_id` across all tables paired with the SDK's lowercase venue code (e.g., `Instrument("600036.SH", venue="sse")`), lot size, tick size, settlement policy, and listing window (`delisted_on` marks the first date outside the investable universe). |
| `DailyPrice` | Unadjusted `open` and `close` prices for execution and ledger valuation, plus `adjusted_close` for split/dividend-adjusted signals. Explicitly set `buy_at_open` or `sell_at_open` to `"unknown"` whenever the source does not establish opening tradability, attach `evidence` for confirmed permissions, and supply a block reason per the [opening-evidence contract](../../../docs/reference/snapshot-backtest.md#missing-prices-and-opening-evidence). |
| `Feature` and `CashDividend` | Each feature's timezone-aware `available_at` timestamp and version string; supported cash distributions mapped under the [cash-dividend contract](../../../docs/reference/snapshot-backtest.md#cash-dividends) with unmodified source dates; and `DailyPrice.corporate_action_gap=True` on sessions affected by unsupported splits or corporate actions. |
| `source_evidence` | Source identifiers alongside input file SHA-256 digests or API extract versions so the exact upstream inputs remain auditable. |

Once vendor rows are converted into typed records, publish the verified data snapshot:

```python
from trademaster.backtest import Snapshot

market = Snapshot.create(
    output_directory, sessions=trading_dates, next_session=following_trading_date,
    instruments=instruments, daily=daily_prices, features=features,
    cash_dividends=distributions, source_evidence=source_ids,
    close_times=session_closes, cutoffs=decision_times,
)
```

Validate the loader against a compact vendor fixture: reopen the published snapshot, check the earliest session on which a feature becomes observable, and verify a blocked opening order alongside its subsequent eligible fill against raw vendor rows—testing a confirmed suspension separately from an `"unknown"` missing status. [Snapshot contract](../../../docs/reference/snapshot-backtest.md#market-inputs-and-decision-timing) · [Importer tests](../../../tests/test_prepare_all_a_snapshot.py)

## Add a provider for cached point-in-time queries

When research workflows require `ensure(request)` and `query(request, as_of=...)` across catalogued datasets, implement `CanonicalDataProvider.fetch(request, *, coverage_keys, page_token) -> ProviderPage` and pass it as `provider=` to `CacheFirstDataPortal`. Compare `TushareProvider.from_environment` and the offline test provider for concrete reference implementations. [Provider contract](../../../python/trademaster/data/coverage.py) · [Tushare implementation](../../../python/trademaster/data/tushare_provider.py) · [Portal tests](../../../tests/test_coverage_portal.py)

Normalize vendor rows to each dataset's registered Arrow schema and return the partition key, exact covered keys, and next page token on every `ProviderPage`. When importing trusted non-Tushare datasets, use a dedicated data root with `canonical_source_policy="trusted_imports"` on `DataConfig` (the default `tushare_only` policy verifies full Tushare raw-to-staging lineage). Exercise multi-page cursors, cached re-reads, and as-of publication cutoffs with a deterministic local fixture. [Data configuration and provenance](../../../docs/reference/data-and-research.md) · [Data contribution guide](../../../python/trademaster/data/AGENTS.md)
