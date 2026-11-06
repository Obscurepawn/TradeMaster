# Data layer

This module configures providers, stores immutable Parquet objects, tracks
coverage in DuckDB, runs point-in-time (PIT) queries and publishes snapshots.
Parquet holds the source records. DuckDB indexes and queries them. Read
credentials only when calling the provider adapter. Keep token values out of
model representations, logs, catalog rows, manifests and tests.

## IndexOne research observations

`IndexOneDataSource` implements `ResearchDataSource` with a separate DuckDB
request index and immutable Parquet responses. It does not publish into the
TuShare canonical store or change its source policy. Read `INDEXONE_API_KEY`
only on an uncached request. The key ID is not sent for authentication.

Keep the HTTP client restricted to the documented read-only core operations.
Validate inputs against the live operation catalog. The execute endpoint has no
pagination; reject oversized or truncated responses rather than caching a prefix.
On cache reads, verify request metadata, content hash, schema and row count.
Fetched time records collection and must not become historical availability.
Run `tests/test_indexone_client.py` and `tests/test_indexone_source.py` when changing
this path. See [IndexOne usage](../../../docs/reference/indexone.md) for imports.

## Requests and availability

Define each dataset's primary key, partitions, coverage keys and availability
fields in the registry. Some datasets have no instrument or trade date.
Configuration or the universe manager can supply the `RequestResolver` for
`CacheFirstDataPortal`. It may fill instruments and coverage keys only. For a
dataset that requires instruments, use the list in the request or one supplied
by this resolver. Reject the request if both are empty.

Ingestion time records when evidence was collected. Derive `known_at` from the
dataset's availability policy. It must be at or after `event_time`. Each staging
record lists the hashes of all raw inputs. The default `tushare_only` mode
requires at least one upstream object and replays the full raw and staging
chain. Use `trusted_imports` explicitly for synthetic fixtures and offline
imports. In strict mode, reject those objects on recovery or query if their
provenance cannot be verified.

## Coverage and provider pages

Check every required instrument/session pair for each venue before publishing
`daily_limits_status` and `daily_bars`. A documented suspension accounts
for a missing bar. For sparse datasets, define what an absent row means
before requiring full coverage.

Rebuild coverage from the normalized request stored in the Parquet object.
Compare the full result with DuckDB's coverage record.

Configure ETFs explicitly because stocks share the `.SH` and `.SZ` suffixes.
Route ETFs through `etf_basic` and `fund_daily`. Reject unsupported ETF datasets
without querying stock endpoints.

A raw request starts at page zero, follows contiguous endpoint-specific
cursors, and ends with a short page. All intermediate pages must be full.
A full last page requires another request. When resuming, rehash and validate
every cached page before fetching the first missing offset. For endpoints that
can revise empty or full responses, refetch every page from offset zero on
each ingestion. Earlier full pages can contain corrections.

## Revisions and snapshots

Check each row's `known_at` against the as-of time. Reject an object that
mixes eligible and future rows. Its `known_at_max` can conceal an eligible
correction. When loading a saved snapshot, replay status evidence at its cutoff
and reject superseded suspension records. `load_snapshot()` also checks its
objects against every currently catalogued revision eligible at that cutoff.

Keep every eligible revision object with the exact requested scope. Rank rows
by revision within each business primary key. Before publishing, compare the
selected rows with every immutable object containing rows in scope. Reject
publication if a newer eligible revision is found only in an object with wider
coverage, universe, interval or field scope.

Validate the inputs to each composite staging object separately. To reproduce
one `daily_limits_status` object, replay its own `stk_limit`, `suspend_d` and
`stock_st` inputs.

Merge snapshot requests for the same dataset and resolved universe when their
coverage keys overlap, including through another request. Produce one coverage
proof and list each shared object once in the manifest.
