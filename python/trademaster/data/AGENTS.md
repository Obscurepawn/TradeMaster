# Data Foundation Guide

This module owns configuration, provider isolation, immutable Parquet objects,
DuckDB coverage metadata, PIT queries, and snapshot publication.

Parquet is authoritative. DuckDB may index and query canonical objects but must
not become a second source of facts. Read provider credentials only at the
adapter call boundary; never persist or include token values in model reprs,
logs, catalog rows, manifests, or tests.

Every dataset-specific primary key, partition scheme, coverage-key rule, and
availability field belongs in the registry. Do not add global assumptions that
all datasets have an instrument or trade date.

`CacheFirstDataPortal` accepts a `RequestResolver` supplied by configuration or
universe management. A resolver may fill only instruments and coverage keys; it
must not alter dataset, interval, or requested fields. Without an authoritative
resolver, instrument datasets fail closed rather than treating an empty tuple as
"all instruments".

Provider ingestion time describes evidence collection, not historical market
availability. Canonical `known_at` must follow the dataset policy and must never
precede `event_time`. Staging evidence names every raw content hash used to build
it so provenance can be reconstructed without trusting filenames or a mutable DB.
Canonical publication defaults to `tushare_only`: at least one upstream object is
required and the complete raw/staging chain is replayed. Synthetic fixtures or
intentional offline imports must opt into `trusted_imports`; a strict store must
reject those objects when they are later recovered or queried without provenance.

Coverage shape is dataset-owned. `daily_limits_status` requires the complete
instrument-by-session matrix; `daily_bars` is equally strict until an explicit
suspension-backed absence proof exists. Each matrix request is venue-scoped; missing a single
pair fails publication. For sparse datasets, absence semantics must be modeled
explicitly before upgrading them to matrix coverage—never infer a Cartesian
proof from separate instrument and date sets.

DuckDB coverage is accepted only when it equals the proof reconstructed from
the immutable Parquet object's embedded normalized request. Do not validate a
proof by comparing only selected columns or by trusting a caller-supplied hash.

ETF identity is explicit configuration, never inferred from a `.SH`/`.SZ`
suffix shared with stocks. Configured ETFs route through `etf_basic` and
`fund_daily`; an unsupported ETF dataset fails instead of querying a stock
endpoint. Raw pagination recovery must re-hash and revalidate every cached page
before resuming at the first missing offset. Endpoints whose empty/full response is
mutable must refresh every cached page from offset zero on each ingestion, not only
the terminal short page, because corrections may occur on an earlier full page.
Every raw request chain must start at page zero, use contiguous endpoint-specific
cursors, contain only full intermediate pages, and end in a short terminal page.
A full last page is proof that another request was required, not proof of completion.

PIT eligibility is row-level. An immutable object that contains both cutoff-eligible
and future `known_at` rows must fail closed during object selection; an object-level
`known_at_max` must never hide an eligible correction. Persisted snapshots independently
replay status evidence at their own cutoff so later catalog additions cannot preserve
a superseded suspension proof.
`load_snapshot()` also compares its bound object set with all currently catalogued
eligible revisions at the manifest cutoff; persisted identity does not waive a newly
discovered historical correction.

PIT snapshots retain every eligible exact-scope revision object and queries rank rows
per business primary key; never select one whole object by its maximum `known_at`.
Before publication, compare those selected rows with every immutable object containing
request-scoped rows. If a newer eligible revision exists only in a wider coverage,
universe, interval, or field scope, fail closed instead of returning the stale row.

Composite datasets require atomic staging provenance. In particular, each
`daily_limits_status` staging object must independently reference and replay all of
`stk_limit`, `suspend_d`, and `stock_st`; their presence only in the union of multiple
staging objects is insufficient.

Snapshot requests for the same dataset and resolved universe are unioned when their
coverage-key sets overlap, including transitive overlap. This produces one coverage
proof and prevents the shared immutable object from appearing twice in the manifest.
