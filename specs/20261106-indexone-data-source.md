# IndexOne Research Data Source Integration

**Status:** Completed | **Date:** 2026-11-07 | **Scope:** Schema-validated IndexOne HTTP client, immutable Parquet/DuckDB evidence cache, and `tm-indexone` CLI (`trademaster.data.indexone`, `trademaster.data.indexone_client`)

## Motivation and Scope

This specification integrates IndexOne as a read-only `ResearchDataSource` (`IndexOneDataSource`) capable of downloading end-of-day (EOD) prices, company fundamentals, security reference metadata, and foreign-exchange (FX) observations into content-addressed Parquet files indexed by DuckDB for deterministic offline reuse (`tm-indexone`).

### Provenance Boundary and Limitations

IndexOne complements rather than replaces the canonical Tushare Pro store, and it never implicitly enables `trusted_imports`:

- **Observations vs. tradability evidence:** Cached IndexOne Parquet files prove what the provider returned at collection time; they do *not* establish complete exchange calendars, opening buy/sell permissions, generic split/cash-dividend feeds (IndexOne's index-level applied-action ledger is not a stock corporate-action feed), or point-in-time restatement histories. Snapshot loaders must still supply explicit session calendars and trading-status evidence before running a daily backtest, and financial `report_period` dates must never be substituted for publication (`available_at`) timestamps.
- **Read-only scope:** Index creation, backtest simulation, account mutations, and delivery operations on the IndexOne platform are outside this integration.

## Provider Contract and Cache Architecture

Request validation and transport follow the official [authentication guide](https://indexone.io/docs/start/authentication) and [execution reference](https://indexone.io/docs/reference/execution-engine), validated dynamically against the public [operation catalog](https://api.indexone.io/schema):

- **Whitelisted read operations:** Restricts queries to `i1_core_eod` (`eod`), `i1_core_fundamentals` (`fundamentals`), `core_securities_reference` (`securities`), and `i1_core_fx` (`fx`), dispatched as single-node `POST /execute` payloads without `websocket_id` or `team_id`.
- **Credential isolation:** Reads the secret API key exclusively from `INDEXONE_API_KEY` (creating the default HTTP client lazily only on a cache miss) and sends it via `x-api-key` (treating key IDs as non-secret management metadata). Bounded retries apply only to transient HTTP errors; authentication, permission, and invalid-request errors fail immediately while scrubbing keys, raw error bodies, and secret-bearing exception chains from logs and artifacts.
- **Schema & dual-envelope validation:** Validates `field`/`operator`/`value` filters and `attributes` projections against `GET /schema`. Parses both the documented `operations` row-array envelope and the live operation-ID-keyed columnar-array envelope, requiring matching node IDs, equal column lengths, and completed execution status while rejecting unfinished, failed, suspended, or truncated responses.
- **Local row-bound guard (`page_limit`):** Because `POST /execute` provides no pagination cursor, `page_limit` enforces a strict client-side row ceiling (never enabling inline-truncating streaming modes); responses reaching the ceiling fail closed without publishing partial Parquet objects so callers split large queries explicitly by instrument or date range.
- **Content-addressed DuckDB/Parquet cache:** `IndexOneDataSource.query_with_evidence()` keys requests by normalized operation, parameters, projected attributes, and `page_limit`. Exact cache hits make zero network calls and require no `INDEXONE_API_KEY`; `offline=True` fails closed on cache misses; `refresh=True` records a new immutable Parquet observation while preserving older Parquet objects referenced by existing **data snapshots**. Reads reverify SHA-256 digests, embedded request/fetch metadata, Arrow schemas, and row counts.

## Changed Files

- **Client & cache store:** `python/trademaster/data/indexone_client.py`, `python/trademaster/data/indexone.py`, `python/trademaster/data/__init__.py`, `pyproject.toml` (`tm-indexone` console entry point).
- **Test suites:** `tests/test_indexone_client.py`, `tests/test_indexone_source.py`.
- **Documentation & guides:** `README.md`, `docs/reference/indexone.md`, `docs/reference/commands.md`, `docs/reference/data-and-research.md`, `docs/README.md`, `.skills/trademaster-backtest/`, `AGENTS.md`, `python/trademaster/data/AGENTS.md`.

## Verification and Publication Record

| Verification Stage | Scope and Outcome |
|---|---|
| Transport, dual-envelope & cache suites | **71 client tests** (`test_indexone_client.py`, covering non-ASCII key rejection, transport-error scrubbing, schema checks, and both row-array and live keyed/columnar envelopes) and **13 real Parquet/DuckDB cache tests** (`test_indexone_source.py`, covering offline reads, `refresh=True` immutability, byte/catalog tamper rejection, and CLI output) all passed. |
| Live credentialed smoke & offline replay | With `INDEXONE_API_KEY` supplied only to the test process environment, live queries succeeded across all four operations: 4 Apple EOD rows (`2024-01-02..2024-01-05`), 4 2023 quarterly fundamental records (`filingDate`, `acceptedDate`, revenue, net income), 1 security-reference row, and 138 FX rows (`2024-01-02`). Re-running the guide's JSON request via `tm-indexone ... --offline` without credentials returned the identical 4-row Parquet object and SHA-256 digests. |
| Full workspace checks (`2026-11-06`) | `uv run --no-sync pytest -q` passed **445 tests** in `49.46s` (3 live Tushare tests skipped without `TUSHARE_TOKEN`), alongside Ruff lint/format checks, strict `mypy` (107 files), skill validation, `git diff --check`, all 180 local Markdown links/anchors, and two independent reviews of credential, parser, and cache boundaries. |
| Clean-checkout publication audit (`2026-11-07`) | An isolated checkout of the staged configurable-rules and IndexOne changes installed cleanly from `uv.lock` and `Cargo.lock`, passing **442 Python tests** (6 tests skipped in the clean checkout without external Tushare caches, demo bundles, or tokens), **67 Rust workspace tests**, `cargo fmt --check`, `cargo clippy -D warnings`, Ruff, strict `mypy`, and `tm-indexone` CLI checks while excluding unrelated workspace research files. |
