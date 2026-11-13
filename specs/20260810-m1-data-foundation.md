# M1: Immutable Parquet Data Store and Point-in-Time Catalog

**Status:** Completed | **Date:** 2026-08-11 | **Scope:** Tushare Pro ingestion, raw/staging/canonical Parquet object store, DuckDB coverage catalog, and point-in-time data snapshots (`trademaster.data`)

## Motivation and Scope

Milestone M1 provides the storage, provenance, and point-in-time (PIT) query foundation for TradeMaster. Parquet files hold the immutable data of record across three tiers—raw provider responses, normalized staging tables, and verified canonical datasets—while DuckDB indexes object metadata, coverage proofs, and cutoff-filtered views without duplicating business facts. Factor computation and trading simulation belong to subsequent milestones ([M2](20260811-m2-factor-signal-layer.md)–[M4](20260811-m4-unified-event-runtime.md)).

| Storage Location | Responsibility |
|---|---|
| `data/raw/<endpoint>/ingest_date=YYYY-MM-DD/*.parquet` | Original provider payloads, normalized request parameters, pagination cursors, and ingestion timestamps (excluding credentials). |
| `data/staging/<dataset>/<registry_partition>/*.parquet` | Typed, deduplicated rows with standardized instrument codes and SHA-256 links to upstream raw Parquet files. |
| `data/canonical/<dataset>/<registry_partition>/*.parquet` | Content-addressed facts verified for schema, primary-key uniqueness, availability (`known_at`), and upstream raw/staging replay. |
| `data/catalog.duckdb` | Transactional index of immutable `objects` and verified `coverage` records plus PIT query views. |
| `data/snapshots/<snapshot_id>/manifest.json` | Immutable **data snapshot** manifest binding normalized requests, cutoff policy (`as_of`), object SHA-256 digests, and coverage proofs. |

## Catalog Schema and Canonical Datasets

### DuckDB Metadata Tables

```text
objects(dataset, partition_key, uri, sha256, schema_sha256, schema_version, row_count,
        event_time_start, event_time_end, known_at_max, fields_json, coverage_keys_json,
        coverage_keys_sha256, instrument_set_sha256, instrument_count, created_at)
coverage(dataset, request_sha256, covered_start, covered_end, fields_json,
         coverage_keys_json, coverage_keys_sha256, instrument_set_sha256, instrument_count,
         object_sha256, verified_at)
```

Both tables enforce idempotent writes via content hashes—keyed uniquely on `objects(dataset, partition_key, sha256)` and `coverage(dataset, request_sha256, object_sha256)`—and roll back on conflicting metadata. Schema migrations execute inside transactions with monotonically increasing version numbers recorded in a single-row `catalog_meta` table; startup checks column names, types, `NOT NULL` constraints, primary keys, and unique constraints, rejecting unknown newer schema versions. All catalog timestamps are stored as UTC integer microseconds independent of host timezone.

### Canonical Dataset Registry

| Dataset | Business Primary Key | Required Business Fields | Availability Policy (`known_at`) |
|---|---|---|---|
| `trade_calendar` | `venue, session_date` | `is_open, open_at, close_at` | Provider ingestion time |
| `instrument_master` | `instrument_id, effective_from` | `venue, asset_class, list/delist` | Separate listing and delisting effective-date policy (`L/D/P/G`) |
| `daily_bars` | `instrument_id, trade_date` | `OHLCV, amount, pre_close` | Session close plus provider publication lag |
| `daily_limits_status` | `instrument_id, trade_date` | `up/down_limit, suspended, ST` | Status observable at the session open (`stk_limit` + `suspend_d` + `stock_st`) |
| `adj_factors` | `instrument_id, trade_date` | `adj_factor` | Provider publication time |
| `daily_basic` | `instrument_id, trade_date` | `float/total_mv, turnover` | Provider publication time |
| `index_bars` | `instrument_id, trade_date` | `OHLCV` | Session close plus provider publication lag |
| `index_membership` | `index_id, instrument_id, effective_from` | `effective_to, weight` | Dated `index_weight` observation (`effective_to = effective_from`, `known_at = ingested_at`) |
| `industry_membership` | `taxonomy, instrument_id, effective_from` | `industry_id, effective_to` | Explicit `SW2021` `in_date` / effective-date policy |
| `financial_indicators` | `instrument_id, report_period, announcement_id` | `report values, revision` | Actual announcement timestamp |

Every canonical table includes `event_time`, `source_revision`, and `known_at`, enforcing `known_at >= event_time` and uniqueness over `(business_primary_key, source_revision)`. A PIT query at cutoff `as_of` filters to `known_at <= as_of` and selects the latest revision per business key, breaking equal-`known_at` ties deterministically using the dataset's business `revision` field and rejecting conflicting payloads that share identical revision metadata.

## Coverage Proofs, Atomic Publication, and Snapshot Lifecycle

### Coverage Planning and Provenance

`trade_calendar` expands date intervals `[start, end]` into sorted venue-scoped `coverage_keys` (such as `SSE:2025-01-02`); session-independent datasets use registry-defined business-key prefixes. Keys, normalized instrument universes, and field projections are hashed separately using SHA-256 over compact UTF-8 JSON.

`DataPortal.ensure()` normalizes each request, resolves empty universe or key fields additively via `RequestResolver`, and verifies existing DuckDB coverage against the on-disk Parquet files before requesting only missing keys from Tushare Pro. By default (`tushare_only` policy), canonical publication requires verifiable raw and staging Parquet ancestry and replays raw endpoint normalization to confirm business-field equality (`trusted_imports` is reserved for explicit test fixtures and offline imports). Configured ETF identifiers are normalized to uppercase inside `DataPaths`, routing `instrument_master` and `daily_bars` to `etf_basic` and `fund_daily` while rejecting stock-endpoint evidence for ETFs.

### Atomic Publication and Crash Recovery

New Parquet objects are written to a temporary directory on the target filesystem, validated for exact Arrow column order, types, nullability, coverage bounds, and SHA-256 digest, synced via `fsync` on both file and directory, and published atomically via a same-filesystem hard link without overwriting existing content addresses. Object and coverage rows commit in a single DuckDB transaction. On startup, the store deletes leftover temporary files, recovers published-but-unindexed Parquet files from their embedded request metadata, and resumes interrupted multi-page downloads from hash-verified raw page cursors.

### Point-in-Time Data Snapshots

`DataPortal.snapshot()` selects cutoff-eligible objects (`known_at <= as_of`), merges compatible field projections and transitively overlapping same-universe requests, binds content-addressed suspension evidence for any missing suspended `daily_bars`, and writes an immutable `manifest.json` whose `snapshot_id` hashes the normalized request, policy, coverage proofs, and object digests. `load_snapshot()` revalidates manifest hashes, Parquet bytes, absence-proof freshness, and catalog-wide revision consistency at the snapshot's original `as_of` cutoff before any backtest reads the data.

## Milestone Subtasks

| Subtask | Status | Verification Summary |
|---|---|---|
| M1.1: `DataConfig`, `DataPaths`, 10-dataset `DatasetRegistry`, deterministic `normalize_request()` | Completed | 5 focused / 20 total Python tests, Ruff, strict `mypy` |
| M1.2: Versioned DuckDB catalog, transactional migrations, UTC microsecond metadata | Completed | 4 real DuckDB migration/rollback tests / 24 total tests |
| M1.3: Content-addressed Parquet store, `fsync` + hard-link publication, crash recovery | Completed | 4 publish/tamper/recovery tests / 28 total tests |
| M1.4: Cache-first coverage planner, gap fetching, pagination uniqueness checks | Completed | 4 cache/gap/pagination suites / 32 total tests |
| M1.5: Credential-isolated `TushareClient` and normalizers for all 10 canonical datasets | Completed | Parameterized Parquet publication tests across all 10 datasets |
| M1.6: Cutoff-aware `snapshot()`, `load_snapshot()`, and DuckDB PIT query views | Completed | Cutoff isolation, determinism, and tamper tests (47 initial tests) |
| M1.7: Multi-round adversarial PIT, provenance, and pagination hardening | Completed | 107 Python tests (0 skipped with live Tushare), 26 Rust tests |

## Limitations

- **Index membership intervals:** Because Tushare `index_weight` provides periodic constituent snapshots rather than historical announcement timestamps, v1 records `index_membership` only on observed dates (`effective_to = effective_from`, `known_at = ingested_at`) rather than interpolating open-ended membership intervals.
- **ETF dataset scope:** Configured ETFs in M1 support `instrument_master` (`etf_basic`) and `daily_bars` (`fund_daily`); stock-specific endpoints such as `daily_basic` and stock `daily_limits_status` fail closed when requested for ETFs.
- **Standalone Rust crate packaging:** `cargo package` verified `tm-core` directly; packaging `tm-engine` against an external registry requires publishing `tm-core` first or patching local workspace paths.

## Verification and Adversarial Hardening Record

Initial TDD red/green suites verified five baseline properties: (1) complete local cache coverage makes zero provider calls; (2) partial gaps, missing fields, or schema mismatches either fetch the exact missing keys or fail closed; (3) rows with `known_at > as_of` and post-close releases on session `T` never leak into earlier PIT queries; (4) byte tampering in Parquet files, row counts, catalog entries, or snapshot manifests raises validation errors; and (5) identical normalized requests produce deterministic snapshot IDs.

Across thirteen independent adversarial reviews (Cycles 2–14), test coverage expanded from 47 to 107 Python tests to close subtle PIT, provenance, and pagination edge cases:

| Category | Adversarial Failure Modes Caught in Review | Implemented Resolution |
|---|---|---|
| Look-ahead & timestamp integrity (Reviews 1–2, 7; Greens 2–3, 8) | `instrument_master` assigned `delist_date` the listing date's `known_at` or made delistings unreachable; `industry_membership` accepted unverified `in_date` values; staging `normalized_at` could precede raw `ingested_at` to backdate `known_at`. | Emitted separate listing and delisting events across `stock_basic` statuses `L/D/P/G`; restricted `industry_membership` to explicit `SW2021` with verified `in_date`; enforced `known_at >= event_time` and `staging.normalized_at >= max(raw.ingested_at)`. |
| Relational bar/status coverage & suspension proofs (Reviews 2–6, 8–9; Greens 3–7, 9–10) | Marginal instrument and session sets hid missing `(instrument, session)` pairs; strict matrices rejected legitimate trading suspensions or zero-row fully suspended days; stale `suspended=True` rows remained valid after newer status revisions. | Enforced full venue-scoped `(instrument, session)` matrices; permitted missing stock bars (including empty fully suspended tables) only when backed by a canonical `daily_limits_status` SHA-256 whose latest unique `(instrument, venue:session)` revision is `suspended=True`, automatically included in snapshots and invalidated by any newer cutoff-eligible status revision. |
| Raw-to-canonical replay & pagination proofs (Reviews 1, 3–7, 10–13; Greens 2, 4–8, 11–14) | Missing upstream tuples bypassed replay; fabricated staging metadata disguised altered canonical values; split `daily_limits_status` staging washed out suspensions; unterminated 5,000-row `suspend_d` pages or spliced mutable pages hid corrections. | Defaulted to `tushare_only` provenance; added `tushare_replay` to rebuild all 10 datasets from raw Parquet and compare against canonical outputs; required each status staging file to replay `stk_limit`, `suspend_d`, and `stock_st` together; verified contiguous page-0-to-short-terminal chains per endpoint limit; refreshed mutable/allow-empty endpoints from offset 0 within one latest ingestion and rejected duplicate non-empty page fingerprints. |
| ETF classification & endpoint isolation (Reviews 3–6, 8–9; Greens 4–7, 9–10) | Lowercase ETF codes or `DataConfig.paths` omissions allowed stock `daily`, `stock_basic`, or `daily_basic` data into ETF stores; ETF `SH/SZ` suffixes could disagree with venues; non-`L` ETFs lacked delisting dates. | Canonicalized ETF IDs to uppercase inside `DataPaths`; verified `SH/SZ` suffixes against `SSE/SZSE`; rejected non-`L` `etf_basic` rows missing delisting dates; rejected all stock-endpoint raw/staging chains for configured ETFs. |
| Per-key PIT ranking & persisted manifest audits (Reviews 2–5, 8–13; Greens 3–6, 9–14) | Object-level `known_at_max` hid row-level corrections in mixed or wider-universe objects; lexical hash ordering hid equal-`known_at` conflicts; `load_snapshot()` did not recheck subsequently cataloged corrections. | Rejected objects straddling `as_of` while retaining all eligible exact-scope objects for per-key DuckDB ranking; scanned all canonical objects for request-scoped rows so newer facts in wider objects fail closed; enforced identical equal-`known_at` conflict detection in `snapshot()` and `load_snapshot()`. |

### Final M1 Checks

- **Python:** 107 passed, 0 skipped (including live Tushare `trade_cal` ingestion producing verified raw, staging, and canonical Parquet files with `TUSHARE_TOKEN` isolated to a mode-`0600` file outside the repository); Ruff and parameterless strict `mypy` passed across all 20 source files; wheel and sdist contents verified.
- **Rust & cross-language:** `cargo fmt --check`, `cargo clippy -D warnings`, 26 workspace tests, doc-tests, `tm-core` package verification, canonical JSON checks, literal-credential scans (0 matches), and `git diff --check` all passed.
