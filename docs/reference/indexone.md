# IndexOne data source

`IndexOneDataSource` provides read-only research acquisition for IndexOne end-of-day prices, company financial statements, security reference metadata, and foreign-exchange rates. Every successful query returns both an in-memory Arrow table and an immutable, content-addressed Parquet evidence file indexed in DuckDB, allowing downstream research and snapshot preparation to rerun deterministically offline without re-querying the network.

## Download a small price sample

From the TradeMaster repository root with [project dependencies](../../README.md#run-the-example) installed, export `INDEXONE_API_KEY` in your shell set to the secret value of an IndexOne team API key (the key ID is not used for authentication). Because the adapter reads `INDEXONE_API_KEY` lazily only when a cache miss requires network access, cached reads succeed without credentials; keep secret keys strictly in environment variables rather than request JSON files or saved artifacts.

Write a query specification for four trading sessions of Apple's raw and adjusted prices and run `tm-indexone`:

```bash
mkdir -p artifacts/indexone
cat > artifacts/indexone/eod-query.json <<'JSON'
{
  "dataset": "eod",
  "params": {
    "filters": [
      {"field": "id", "operator": "eq", "value": "BBG000B9XRY4"},
      {"field": "date", "operator": "gte", "value": "2024-01-02"},
      {"field": "date", "operator": "lte", "value": "2024-01-05"}
    ]
  },
  "fields": ["id", "date", "open", "close", "volume", "adjClose"],
  "page_limit": 10000
}
JSON
uv run tm-indexone --data-root data/indexone --request artifacts/indexone/eod-query.json
```

On completion, `tm-indexone` prints a JSON summary containing `rows`, `path` (the content-addressed Parquet file), `request_sha256`, and `content_sha256`—note that `rows` reports the count of returned records rather than asserting complete session coverage across the filter range. Once cached, the identical query can be reopened offline without network access or credentials:

```bash
uv run tm-indexone --data-root data/indexone --request artifacts/indexone/eod-query.json --offline
```

Passing `--offline` raises an error immediately on a cache miss, whereas `--refresh` forces a live network fetch to record a fresh observation for the same request; the two flags are mutually exclusive. Python callers select the same modes via `IndexOneDataSource(root=..., offline=True)` or `IndexOneDataSource(root=..., refresh=True)`.

## Query from Python

In Python, `IndexOneDataSource` implements the `ResearchDataSource` protocol, accepting `fields` as the column projection and validating filter field names and operators against IndexOne's [live operation catalog](https://api.indexone.io/schema):

```python
from pathlib import Path
from trademaster.data import IndexOneDataSource

with IndexOneDataSource(root=Path("data/indexone")) as source:
    prices = source.query_with_evidence(
        "eod",
        params={
            "filters": [
                {"field": "id", "operator": "eq", "value": "BBG000B9XRY4"},
                {"field": "date", "operator": "gte", "value": "2024-01-02"},
                {"field": "date", "operator": "lte", "value": "2024-01-05"},
            ]
        },
        fields=("id", "date", "open", "close", "volume", "adjClose"),
        page_limit=10000,
    )
    print(prices.table.to_pylist())
    print(prices.evidence.path, prices.evidence.content_sha256)
    print(prices.evidence.fetched_at)
```

When querying company financials, specify `storage_type` explicitly and project disclosure timestamps (`filingDate`, `acceptedDate`) alongside accounting line items so downstream loaders can establish point-in-time visibility:

```python
with IndexOneDataSource(root=Path("data/indexone")) as source:
    financials = source.query_with_evidence(
        "fundamentals",
        params={
            "storage_type": "quarterly",
            "filters": [
                {"field": "id", "operator": "eq", "value": "BBG000B9XRY4"},
                {"field": "date", "operator": "gte", "value": "2023-01-01"},
                {"field": "date", "operator": "lte", "value": "2023-12-31"},
            ],
        },
        fields=("id", "date", "filingDate", "acceptedDate", "revenue", "netIncome"),
        page_limit=10000,
    )
    print(financials.table.to_pylist())
```

Four allowlisted core datasets are exposed:

| Dataset | IndexOne operation | Observations |
|---|---|---|
| `eod` | `i1_core_eod` | Raw OHLCV bars, provider-adjusted prices, split-adjusted prices, and market capitalization. |
| `fundamentals` | `i1_core_fundamentals` | Income statement, balance sheet, and cash-flow items across `quarterly`, `annual`, or `ttm` views. |
| `securities` | `core_securities_reference` | Composite and exchange FIGIs, ticker symbols, currencies, exchange MICs, listing intervals, and sector classifications. |
| `fx` | `i1_core_fx` | Currency-pair session dates alongside opening and closing exchange rates. |

Each query dispatches a single allowlisted read operation through `POST /execute` after validating parameters, column projections, and return types against `GET /schema`; the client never submits arbitrary workflows or mutates IndexOne index definitions. While `POST /execute` authenticates solely via the secret key in `x-api-key`, other IndexOne platform endpoints may require additional credentials. [Provider authentication](https://indexone.io/docs/start/authentication.md)

Filter price and fundamental queries by composite FIGI (such as `BBG000B9XRY4`), and use canonical exchange-suffixed tickers (such as `AAPL.US`) when filtering `securities` by symbol. Audit reference mappings carefully when assembling historical universes: IndexOne's default reference deduplication can collapse multiple share classes, and active-listing filters vary by query context, so specify deduplication and listing parameters explicitly. [Provider identifiers](https://indexone.io/docs/start/symbols.md)

## Cache, failures and history

Each cache entry is keyed by the canonical tuple `(dataset, params, fields, page_limit)` and backed by a DuckDB catalog pointing to hash-named Parquet files. Every read re-verifies both the Parquet digest and stored metadata; running with `refresh=True` publishes a new immutable Parquet object and updates latest-read resolution while leaving earlier evidence files intact for existing data snapshots, recording the collection timestamp in `fetched_at`.

Because IndexOne's `POST /execute` endpoint has no server-side pagination, `page_limit` acts as a local safety ceiling rather than a page size, and streaming modes are intentionally omitted so inline responses are never silently truncated. Any response that reaches the row ceiling or fails credential, plan, permission, schema, or payload validation aborts immediately without writing partial artifacts—partition wide universes or multi-decade histories across smaller identifier or date-range batches. [Provider execution and limits](https://indexone.io/docs/reference/execution-engine.md)

Cached evidence preserves vendor observations verbatim rather than guaranteeing point-in-time (PIT) correctness by itself: vendor price series may incorporate retroactive adjustments, and a financial row's `date` identifies the fiscal reporting period rather than when market participants could read the filing. Always retain and audit `filingDate` and `acceptedDate` (interpreting bare IndexOne timestamps as UTC) when computing `as_of` feature cutoffs, and never substitute `fetched_at` for an original disclosure timestamp. [Provider date convention](https://indexone.io/docs/start/dates.md)

Before running a backtest on IndexOne extracts, map the cached observations into a verified [data snapshot](../../.skills/trademaster-backtest/references/data-sources.md#import-a-csv-parquet-file-or-another-api-into-a-backtest): because IndexOne neither supplies a standalone cash-dividend/split corporate-action feed nor proves opening-auction tradability from an OHLCV bar alone, the snapshot loader must supply the venue session calendar, close times, explicit [opening-status evidence](snapshot-backtest.md#missing-prices-and-opening-evidence), and corporate-action coverage before running the [US backtest walkthrough](../../README.md#backtest-a-us-stock-strategy). Meanwhile, the canonical Tushare portal maintains its own staging and replay provenance checks independently.
