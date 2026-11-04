# Historical data sources

## Prepare the TuShare demo

TuShare Pro is the built-in historical provider. Supply `TUSHARE_TOKEN` through the shell environment for planning and downloading. Keep the token out of source files, requests, reports and logs.

From the repository root, create a plan for the five-stock example. The backtest ends on May 31, 2024. The download includes June 3 as the next trading session for scheduling and settlement.

```bash
uv run tm-research-full-a --data-root data/research/demo plan \
  --start 2024-01-02 --end 2024-06-03 --statement-start 2023-01-01
```

Set `PLAN` to the `plan_sha256` printed by that command. Reuse the same plan and data root to resume downloads or run either engine.

```bash
PLAN='<plan_sha256 from the previous command>'
uv run tm-research-full-a --data-root data/research/demo download \
  --plan-sha256 "$PLAN" --phase market --phase status
uv run tm-research-full-a --data-root data/research/demo status --plan-sha256 "$PLAN"
uv run tm-research-full-a --data-root data/research/demo verify --plan-sha256 "$PLAN"
```

Check coverage of the required sessions, price limits, adjustments and suspensions. Once the market and status inputs for the sample are present, choose the output you need. Both commands below reuse the local cache offline.

```bash
# Daily snapshot for Python strategies, plus the three example ledgers
uv run python -m trademaster.e2e.demo_strategies --engine snapshot \
  --data-root data/research/demo --plan "$PLAN"

# Event-runner HTML reports for the three example strategies
uv run python -m trademaster.e2e.demo_strategies --engine events \
  --data-root data/research/demo --plan "$PLAN"
```

The shared market is `artifacts/demo-snapshot/snapshot`. Each event strategy has its own `report.html` under `artifacts/demo-strategies/`. For the long-history ETF benchmark, use its dedicated preparer, which also collects cash distributions. [Five-stock inputs and outputs](../../../docs/examples/tushare-strategies.md) · [ETF benchmark](../../../docs/examples/broad-etf.md)

## Import a CSV, Parquet file or another API into a backtest

Write a Python loader that translates the vendor’s records into `trademaster.backtest` types, then calls `Snapshot.create` once. The strategy can open the resulting directory just like a TuShare snapshot. The [full-A Parquet importer](../../../specs/examples/prepare_all_a_snapshot.py) is a working example.

| Snapshot input | What to map |
|---|---|
| `sessions`, `next_session` | Supply unique trading dates in order and the actual next trading date after the sample. The calendar includes market sessions on which an individual stock was suspended. |
| `Instrument` | Use a stable ID across all tables and the SDK’s lowercase venue code, as in `Instrument("600036.SH", venue="sse")`. Map the lot size, price tick, settlement and historical listing interval. `delisted_on` is the first date outside the investable universe. |
| `DailyPrice` | Supply unadjusted open and close for fills and valuation. Put the adjusted signal price in `adjusted_close`. Set `buy_at_open` and `sell_at_open` independently to `"yes"`, `"no"` or `"unknown"`. Record the evidence for known permissions and a reason for a block. A missing quote alone leaves trading status unknown. |
| `Feature` and `CashDividend` | A feature needs its actual timezone-aware `available_at` and version. For each known cash dividend supply its record, ex- and payment sessions and amount per share. Flag an unexplained corporate action in `DailyPrice.corporate_action_gap`. |
| `source_evidence` | Record the source identifier and file hashes or API extract version so the same input can be recovered. |

Once the vendor records are mapped to these types, publish them with the public SDK.

```python
from trademaster.backtest import Snapshot

market = Snapshot.create(
    output_directory, sessions=trading_dates, next_session=following_trading_date,
    instruments=instruments, daily=daily_prices, features=features,
    cash_dividends=distributions, source_evidence=source_ids,
)
```

Test the loader with a small vendor fixture. Reopen the snapshot, check when a feature first becomes visible, and compare a blocked order and the next eligible fill with the source rows. Use a confirmed suspension and a missing status as separate cases. [Snapshot contract](../../../docs/reference/snapshot-backtest.md#market-inputs-and-decision-timing) · [Importer tests](../../../tests/test_prepare_all_a_snapshot.py)

## Add a provider for cached point-in-time queries

Use this path when researchers also need `ensure(request)` and `query(request, as_of=...)` across datasets. Implement `CanonicalDataProvider.fetch(request, *, coverage_keys, page_token) -> ProviderPage` and pass the instance as `provider=` to `CacheFirstDataPortal`. The bundled `TushareProvider.from_environment` and the test provider show the two sides of this interface. [Provider contract](../../../python/trademaster/data/coverage.py) · [TuShare implementation](../../../python/trademaster/data/tushare_provider.py) · [Portal tests](../../../tests/test_coverage_portal.py)

Map vendor rows to the dataset’s registered Arrow schema. Return the partition key, exact covered keys and next page token with each page. For trusted independent imports, use a separate data root and set `canonical_source_policy="trusted_imports"` on `DataConfig`. The default stock and ETF policy verifies TuShare raw and staging records. Test pagination, cached reads and publication cutoffs with a local provider fixture. [Data configuration and provenance](../../../docs/reference/data-and-research.md) · [Data contribution guide](../../../python/trademaster/data/AGENTS.md)
