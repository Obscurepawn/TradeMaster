# IndexOne data source

Use `IndexOneDataSource` to download end-of-day prices, company financials,
security reference data and FX observations. Each successful query returns an
Arrow table and immutable Parquet evidence. Repeating the same request reads
the local cache, so research can continue without a network connection.

## Download a small price sample

Work from the TradeMaster repository root with the [project dependencies](../../README.md#run-the-example)
installed. Set `INDEXONE_API_KEY` in your shell to the secret value of an
IndexOne team API key. The key ID is not used for authentication. The source
reads the variable only when a request needs the network; cached requests work
without it. Keep credentials out of request files and saved output.

Create a request file for four sessions of Apple's raw and adjusted prices:

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

The command prints JSON with `rows`, `path`, `request_sha256` and
`content_sha256`. `path` identifies the saved Parquet file; the count is the
number of observations returned, not a claim that every requested session was
covered. Once the request has succeeded, read it without using credentials or
the network:

```bash
uv run tm-indexone --data-root data/indexone --request artifacts/indexone/eod-query.json --offline
```

An offline cache miss fails. Use `--refresh` instead to fetch a new observation
for the same request. The two flags cannot be combined. Python callers select
the same modes with `IndexOneDataSource(root=..., offline=True)` or
`IndexOneDataSource(root=..., refresh=True)`.

## Query from Python

The same request works through the `ResearchDataSource` interface. `fields` is
the column projection; filter field names and operators follow IndexOne's
[live operation catalog](https://api.indexone.io/schema).

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

For company financials, select a storage view and request the publication fields
alongside the measures. This example reads quarterly reports for a stated period:

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

The source supports these datasets:

| Dataset | IndexOne operation | Observations |
|---|---|---|
| `eod` | `i1_core_eod` | Raw OHLCV, provider-adjusted prices, split-adjusted prices and market capitalization. |
| `fundamentals` | `i1_core_fundamentals` | Income statement, balance sheet and cash-flow fields; `quarterly`, `annual` or `ttm` storage. |
| `securities` | `core_securities_reference` | FIGIs, symbols, currency, exchange MIC, listing dates and classifications. |
| `fx` | `i1_core_fx` | Currency-pair dates, opening and closing rates. |

Requests run one allowlisted data operation through `POST /execute`; the client
checks parameters, projections and output types against `GET /schema`. It does
not submit arbitrary workflows or change an IndexOne index. This endpoint uses
the secret value in `x-api-key`; other IndexOne endpoints can require additional
credentials. [Provider authentication](https://indexone.io/docs/start/authentication.md)

Use composite FIGIs for price queries. For a security-reference symbol filter,
use the canonical suffixed form such as `AAPL.US`. Check mapping results before
using them: the reference's default deduplication can combine share classes, and
listing filters depend on the query context. Select those parameters explicitly
when building a historical universe. [Provider identifiers](https://indexone.io/docs/start/symbols.md)

## Cache, failures and history

The dataset, parameters, selected fields and `page_limit` identify a cached
request. DuckDB indexes successful requests and their hash-named Parquet files.
Each read verifies the evidence file and its recorded metadata. Refresh writes
a new immutable object and keeps the earlier evidence available to existing
data snapshots. `fetched_at` records collection time.

`page_limit` is a local safety bound, not a page size: `/execute` has no documented
pagination. An oversized response fails without publishing a partial result.
Split large requests by identifier or date range. The client omits streaming
options, which would truncate the inline response. Credential, plan, permission,
validation and malformed-response failures are surfaced at the query that needs
the data. [Provider execution and limits](https://indexone.io/docs/reference/execution-engine.md)

Historical prices can be revised or adjusted after the fact. Financial `date`
describes a report period; it is not the moment a strategy could observe the
report. Retain `filingDate` and `acceptedDate`, verify their meaning and revision
coverage before publishing features, and never substitute `fetched_at` for an
original publication time. The cache preserves the returned observation; it does
not establish point-in-time financial correctness. IndexOne's bare timestamps
are UTC. [Provider date convention](https://indexone.io/docs/start/dates.md)

To run a strategy, map the observations into a
[data snapshot](../../.skills/trademaster-backtest/references/data-sources.md#import-a-csv-parquet-file-or-another-api-into-a-backtest).
The loader still supplies exchange sessions, close times, opening-status
evidence and corporate-action coverage. This source does not provide a generic
cash-dividend or split feed, and a price row alone does not prove opening
tradability. Use the existing [US backtest walkthrough](../../README.md#backtest-a-us-stock-strategy)
after preparing those inputs. The separate TuShare canonical portal keeps its
existing provenance checks.
