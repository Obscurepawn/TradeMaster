# M1 data storage and point-in-time queries

Status: completed
Owner: Codex

## Scope and files

M1 covers Tushare ingestion, three layers of Parquet files, the DuckDB catalog and its coverage records, point-in-time (PIT) queries, and immutable data snapshots. Factors and trading belong to later milestones. Parquet holds the data of record.

| Location | Contents |
|---|---|
| `data/raw/<endpoint>/ingest_date=YYYY-MM-DD/*.parquet` | Original provider fields and request metadata. |
| `data/staging/<dataset>/<registry_partition>/*.parquet` | Typed, deduplicated rows with standardized instrument codes. |
| `data/canonical/<dataset>/<registry_partition>/*.parquet` | Verified facts. Each dataset declares its partition, event identity, primary key, and `known_at/source_revision` in the registry. Some datasets have no instrument key. |
| `data/catalog.duckdb` | Object indexes, coverage records, and views. The catalog does not duplicate the canonical facts. |
| `data/snapshots/<snapshot_id>/manifest.json` | Request, policy, object content hashes, and evidence of coverage. |

## Catalog

```text
objects(dataset, partition_key, uri, sha256, schema_sha256, schema_version, row_count,
        event_time_start, event_time_end, known_at_max, fields_json, coverage_keys_json,
        coverage_keys_sha256, instrument_set_sha256, instrument_count, created_at)
coverage(dataset, request_sha256, covered_start, covered_end, fields_json,
         coverage_keys_json, coverage_keys_sha256, instrument_set_sha256, instrument_count,
         object_sha256, verified_at)
```

Both tables use content hashes for idempotent writes and reject conflicting metadata. Their unique keys are `objects(dataset, partition_key, sha256)` and `coverage(dataset, request_sha256, object_sha256)`. Migrations use transactions and monotonically increasing versions. Startup rejects an unknown newer version.

## V1 canonical datasets

| Dataset | Primary key | Required business fields | Availability (`known_at`) |
|---|---|---|---|
| `trade_calendar` | venue, session_date | is_open, open_at, close_at | Provider ingestion time |
| `instrument_master` | instrument_id, effective_from | venue, asset_class, list/delist | Announcement/effective-date policy |
| `daily_bars` | instrument_id, trade_date | OHLCV, amount, pre_close | Session close plus provider lag |
| `daily_limits_status` | instrument_id, trade_date | up/down_limit, suspended, ST | Status observable at the session open |
| `adj_factors` | instrument_id, trade_date | adj_factor | Provider publication time |
| `daily_basic` | instrument_id, trade_date | float/total_mv, turnover | Provider publication time |
| `index_bars` | instrument_id, trade_date | OHLCV | Session close plus provider lag |
| `index_membership` | index_id, instrument_id, effective_from | effective_to, weight | Original announcement/effective-date policy. Green 9 specifies ingestion time for dated `index_weight` observations. |
| `industry_membership` | taxonomy, instrument_id, effective_from | industry_id, effective_to | Announcement/effective-date policy |
| `financial_indicators` | instrument_id, report_period, announcement_id | report values, revision | Actual announcement timestamp |

Every canonical table also includes `event_time/source_revision/known_at`. The business primary key plus `source_revision` must be unique. A PIT query returns the latest revision per business key available by its cutoff.

## Coverage and verification

Use `trade_calendar` to expand `[start,end]` to sorted `coverage_keys`, such as `SSE:2025-01-02`. Hash the keys, normalized universe, and field list separately with SHA-256 over compact UTF-8 JSON. Each object lists its actual keys. The union of distinct keys must equal the request, including intervening sessions. For datasets whose records are independent of trading sessions, the registry defines the appropriate business-key prefix.

`DataPortal.ensure()` normalizes the request and resolves the universe. It uses DuckDB to establish coverage of dates/sessions, fields, and instruments before asking Tushare for a gap. After downloading, verify pagination, primary-key uniqueness, request bounds, schema, `known_at`, and content hashes. Publish the canonical object and register it in the catalog. Only this verification process can create `DatasetCoverage`.

`snapshot()` selects objects known by the requested time (`known_at <= as_of`). Its ID is calculated from the request, policy, coverage, and selected objects. Each Arrow result identifies the snapshot, the request, and the digest of those objects.

Before running a backtest on a snapshot, reread the Parquet files and check their SHA, row counts, fields, instruments, and time ranges. Before accepting `status=complete`, the report calls `verify_run_manifest()` to read the eleven result files, check `run_id`, hashes, and row counts, and verify that the orders, fills, and account records agree across tables.

## Publication and recovery

The initial design called for writing under a temporary directory on the same filesystem, validating the schema, coverage, and hashes, calling `fsync` on the file and directory, and publishing the immutable file by rename. M1.3 implemented publication with a same-filesystem hard link. Register the object and its coverage in one DuckDB transaction. On startup, remove leftover temporary files and register published but unindexed objects with valid hashes. Never overwrite an existing content address. Raw metadata records provider page cursors and request hashes so a repeated run can resume from verified pages.

## Milestone tasks

| Task | Owner | Status | Verification |
|---|---|---|---|
| M1.1 config/paths/dataset registry | Codex | Done | 5 tests, Ruff, mypy, full pytest |
| M1.2 DuckDB migrations/catalog | Codex | Done | 4 real DuckDB tests and full checks |
| M1.3 Parquet atomic object store | Codex | Done | 4 real Parquet publish/tamper/recovery tests |
| M1.4 coverage planner + fake provider | Codex | Done | 4 cache/gap/pagination cases and full checks |
| M1.5 Tushare adapters | Codex | Done | Published fixture data for all 10 registry datasets |
| M1.6 PIT query/snapshot | Codex | Done | 3 cutoff/provenance/tamper tests |
| M1.7 independent review | Codex + independent reviewer | Done | All three High findings from review 13 fixed, full checks passed, overall review scheduled after M6 |

## Initial red/green cases

1. A complete local cache still triggered the provider. The passing case made 0 calls.
2. Partial coverage, missing pages or fields, wrong universes, and schema changes were accepted as complete. The passing cases fetched exactly the gap or rejected the request.
3. Rows with `known_at > as_of`, later revisions, and values released after the close on T could enter earlier results. PIT tests excluded them.
4. Tampering with Parquet, row count, catalog coverage, or the manifest still allowed reading. Digest validation rejected each change.
5. Repeating a normalized request could produce different snapshots. Identical content and ordering had to produce the same snapshot ID.

## Implementation record

- **M1.1.** Red: `tests/test_data_config_registry.py` failed collection because `trademaster.data` was missing. Green: `DataConfig` stores only the token environment variable's name. `DataPaths` restricts directories, `DatasetSpec/DatasetRegistry` defines individual datasets, and `normalize_request()` is deterministic. Results: 5 focused and 20 total Python tests passed, as did Ruff, strict mypy, and `git diff --check`.
- **M1.2.** Red: missing public catalog types prevented DuckDB integration test collection. Green: the version 1 migration created the specified `objects/coverage` columns. UTC times are integer microseconds independent of host timezone. Object and coverage records are idempotent in one transaction, metadata conflicts roll back, and unknown newer schema versions are rejected. Four focused and 24 total tests, Ruff, strict mypy, and `git diff --check` passed.
- **M1.3.** Red: the Parquet object-store interface could not be imported. Green: Parquet metadata contains the normalized request, partition, and creation time. The bytes determine the SHA filename. Fsync and a hard link publish the file atomically. Reads verify the hash, schema, and row count. Startup deletes leftover temporary files and recovers unindexed objects from their own metadata. Four focused and 28 total tests, Ruff, strict mypy, and `git diff --check` passed.
- **M1.4.** Red: the cache-first portal, page, and provider interfaces could not be imported. Green: planning accepts a catalog object only after checking fields, universe, request-key subset, and the actual Parquet. Complete coverage makes no provider calls. Gaps are requested by key. Page keys must be unique and cover the gap exactly. After publication, the planner verifies coverage again through DuckDB. Four focused cases (including parameterized failures) and 32 total tests passed, along with Ruff, strict mypy, and `git diff --check`.
- **M1.5, first slice.** Red: fixture tests could not import the Tushare credential/provider interfaces. Green: an injectable `TushareClient` wraps the SDK. Only the client factory reads the token. Raw request metadata omits it. `trade_cal` and `daily` responses go to raw, then staging, then pages by coverage key. Four fixture tests passed. The process lacked `TUSHARE_TOKEN`, so the real smoke test was skipped. The full result was 36 passed/1 skipped, with Ruff, strict mypy, and diff checks passing.
- **M1.5, remaining datasets.** The other eight datasets first failed with unsupported normalizers. `daily_limits_status` then combined `stk_limit/suspend_d/stock_st`. Session, instrument, index and industry membership, and financial revision endpoints were normalized to their registry schemas. Parameterized tests checked required fields, partitions, coverage keys, and publication to real Parquet for all ten datasets.
- **M1.6.** Red: the portal's missing `snapshot/query` methods caused three PIT test failures. Green: for replacements with the same coverage keys, the first implementation selected the latest eligible object before the cutoff. It wrote the snapshot atomically with request, policy, and object hashes. DuckDB `read_parquet()` selected the last eligible revision per business key at `known_at <= as_of` and attached provenance. Determinism, future-revision isolation, and tampering tests passed. Initial total: 47 passed/1 credential-dependent skip. Ruff, strict mypy, and `git diff --check` passed.

## Independent reviews and corrections

These are the checks and open findings at each review. M1 remained open until the final recorded fixes.

### Review 1 and Green 2

Review 1 failed. The Critical issue was historical look-ahead: `instrument_master` assigned the current `delist_date` the listing date's `known_at`. Five High findings covered unspecified canonical Arrow types, unused catalog coverage records and hashes, missing resolution for empty universes/keys, incorrect parameters or fields on four Tushare endpoints, and unverified pagination or row limits within a key. The two Medium findings concerned raw/staging page provenance and migrations that checked column names alone. M1 stayed open.

Green 2 specified exact Arrow field types for all ten datasets and verified them at publication. Session-based instrument data requires a resolved universe. The catalog recomputes the coverage-key digest. `_plan()` checks recorded fields, keys, and universe against the object. A configurable `RequestResolver` fills the universe and keys, or rejects unresolved requests. Trade-calendar date keys expand deterministically.

The changes separated listing and delisting in `stock_basic`, corrected official `index_member_all` fields and the `index_daily` `ts_code`, requested `suspend_type=S` and three `list_status` values, and implemented independent limit/offset pagination and duplicate-page detection per endpoint. Raw metadata records the request hash, cursor, page number, and row revision. Staging records the actual raw Parquet hashes. DuckDB startup checks names, types, NOT NULL, primary keys, and unique constraints. Publication rejects `known_at < event_time`. Daily stock and index bars, daily basics, adjustment factors, and open status use UTC availability from the registry policy. Historical download time no longer determines their visibility.

Verification: 56 passed/1 credential-dependent skip. Ruff, strict mypy, `git diff --check`, Rust formatting, Clippy, 26 Rust tests, doc-tests, and wheel/sdist builds passed. The process had no `TUSHARE_TOKEN`, so network behavior remained unverified.

### Review 2 and Green 3

Review 2 failed with 3 Critical findings. Matching `list_date` first made the delisting event unreachable. Industry membership could acquire invented history because the normalizer did not check `in_date` or taxonomy. Separate instrument and session sets could hide missing instrument/session pairs. Five High findings concerned unspecified column order/nullability, unchecked request digest/range/time in coverage records, a resolver that could replace caller-supplied universes and accept empty business-key universes, suspend/ST rows from other dates, and revisions selected by lexical source hash when `known_at` tied. Three Medium findings concerned multiple `catalog_meta` rows, the missing G status in `stock_basic`, and index-membership availability/removal intervals.

Green 3 matched listing and delisting as separate effective events in `instrument_master` and covered statuses L/D/P/G. `industry_membership` supported only explicit `SW2021`, queried the requested instrument, and required an exact provider `in_date`. Suspension and ST rows had to match the requested `trade_date`. Publication checked ordered, non-null canonical fields, type, and nullability. Each `daily_limits_status` request covered one venue and required every instrument/session pair.

The planner rebuilt coverage from the actual Parquet request and compared it with DuckDB for request digest, range, fields, keys, universe, object, and verification time. Partial objects could still combine to satisfy a larger request. Resolvers could fill empty fields only. All instrument-bearing datasets rejected empty universes. Calendar keys and venues no longer assumed SSE. Financial PIT rows with equal `known_at` used the registry's business `revision`, and `catalog_meta` had to contain exactly one row. Results: 65 passed/1 credential-dependent skip. Ruff, strict mypy, and the diff check passed. Cross-language and packaging checks and review 3 were pending at this point.

### Review 3 and Green 4

Review 3 failed with 0 Critical, 6 High, and 1 Medium. High findings included sparse daily bars incorrectly passing separate instrument/session checks, full-universe requirements conflicting with natural instrument-bucket or venue partitions, hash order resolving tied business revisions, missing raw/staging references in canonical data, stock-only adapters despite advertised ETF support, and raw cursors that could not resume a run in a new process. The Medium concerned index-membership intervals and availability.

Green 4 required a complete matrix for `daily_bars` and `daily_limits_status` within a venue. At this stage a missing suspended bar caused rejection until a complete status matrix could support an explicit absence record. OHLC values were never invented. `instrument_master` used an `asset_class` partition and financial data a single `dataset_scope` partition. Publication rejected duplicate primary-key, knowledge-time, and business-revision combinations as ambiguous. `ProviderPage` carried staging/raw hashes into canonical metadata. Raw metadata included fields so each object could reproduce its provider request hash independently.

ETF instruments were configured explicitly. Master and daily data used `etf_basic` and `fund_daily`, while stocks used `stock_basic/daily`. The canonical schema remained the same. Unsupported ETF factor or status requests failed. At startup, `_query_raw()` recomputed raw file hashes and checked request, fields, params, cursor, returned rows, and row revisions before resuming a contiguous page sequence. A test crashed at offset=6000, then a separate provider instance requested only offset=6000. Python reached 68 passed/1 credential-dependent skip. Other checks and review 4 were pending.

### Review 4 and Green 5

Review 4 failed with 0 Critical, 6 High, and 2 Medium. High findings: the strict bar matrix could not represent suspension, mixed stock/ETF master requests still spanned incompatible partitions, business revisions remained ambiguous across files, upstream hashes were checked for syntax without locating their files, cached empty terminal pages obscured delayed corrections, and ETF venue/delisting handling was wrong. Medium findings: unchecked raw `ingested_at` and the open index-membership interval/availability issue.

Green 5 verified `daily_limits_status` for the same universe/session before fetching missing daily bars. A missing bar required a `suspended=True` row for that instrument and session. The bar file recorded the SHA of the status file. Reads and recovery rehashed that file and checked the suspension row again. Bar snapshots included the referenced status file and rejected unexplained gaps.

Stock and ETF master data shared one `dataset_scope` object. Financial data also used one logical scope. The planner compared primary key, knowledge time, and business revision across replacement objects and rejected different source contents for the same fact. Neither `created_at` nor a hash decided the result. Canonical publication and verification located and rehashed every upstream raw/staging file, required at least one staging object, and replayed referenced raw hashes. Cached raw `ingested_at` had to be UTC. Endpoints that permit empty results rechecked empty terminal pages on the next request.

ETF `SH/SZ` normalized to `SSE/SZSE`. Non-L `etf_basic` rows lacking a reliable delisting date were rejected. Results were 70 passed/1 credential-dependent skip, with Ruff, mypy, and diff checks passing. Full Rust/package checks and review 5 were pending.

### Review 5 and Green 6

Review 5 failed with 0 Critical, 8 High, and 2 Medium. The wrong Parquet dataset could stand in for suspension evidence, snapshots did not require the exact status SHA named by bars, and a fully suspended universe could not publish zero bars. Conflicting payloads carrying the same source revision could pass across objects. Fabricated raw/staging metadata could make altered canonical data appear valid. ETF bar requests through the portal required stock status, ETF venues were not checked against ticker suffixes, and revisions between empty and populated terminal pages could cause permanent conflicts. The Medium findings concerned index membership and a stale status summary in this spec.

Green 6 allowed absence evidence only from a `daily_limits_status` object that passed canonical metadata, schema, and request replay. The snapshot had to contain the exact evidence SHA used by the bars. Zero-row canonical bars were accepted only with a content-addressed suspension record for every instrument/session pair, including a fully suspended session that DuckDB could return as an empty table.

The revision comparison hashed the complete canonical payload across objects. Raw replay checked endpoint, request, time, cursor, and row revisions. Staging replay checked the dataset, partial request, coverage, schema, source revisions, and raw references. The normalized union of staging rows had to equal the canonical data. ETF `SH/SZ` had to agree with the ticker suffix. Complete `fund_daily` bars no longer required stock status. Mutable endpoints allowing empty results used their most recent ingestion, refreshed the terminal page, and rejected conflicting contents at the same timestamp.

Verification: 76 passed/1 credential-dependent skip, plus Ruff, strict mypy, Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist, and `tm-core` package verification. Registry packaging of `tm-engine` could not run because that version of `tm-core` had not been published. Workspace builds and tests passed. Separate crate verification required publishing `tm-core` first or using a local registry for both crates. Without `TUSHARE_TOKEN`, the skipped network smoke test still provided no network evidence. Review 6 was pending.

### Review 6 and Green 7

Review 6 failed with 0 Critical, 6 High, and 3 Medium. Self-consistent raw metadata did not verify the transformation into staging/canonical business fields. Cross-object payload conflict checks covered only datasets with an explicit business revision. A live empty-to-populated change at the same `ingested_at` was not rejected on that call. Sparse mixed stock/ETF bars erroneously requested ETF status. Wide explicit status requests and narrower automatic dependencies could add the same object twice. Strict mypy found two test list-type errors. Medium findings concerned a public file verifier for saved snapshots, index-membership intervals/availability, and documentation of package verification limits.

Green 7 added an independent `tushare_replay` verifier. It rebuilt all ten canonical datasets from the actual raw Parquet referenced by staging and compared the result with the normalized staging/canonical union. Agreement on provider metadata, source revisions, or staging labels could no longer disguise altered business values. Conflict checks applied to every dataset and rejected different payloads with the same primary key/knowledge-time revision identity.

Refreshed terminal pages were compared with cached observations at the same ingestion timestamp, so ambiguous empty/nonempty content was rejected immediately. Bars requested status only for missing instruments and coverage keys. A complete ETF bar could coexist with a suspended stock. Snapshots derived minimal status dependencies from absence records and deduplicated an exact evidence SHA already selected explicitly. A later status correction from a different universe also invalidated an older suspension observation. Checks: 80 passed/1 credential-dependent skip, strict mypy, Ruff, Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist, `tm-core` package verification, and the diff check. Review 7 was pending.

### Review 7 and Green 8

Review 7 failed with 1 Critical, 2 High, and 3 Medium. March raw `ingested_at` did not constrain staging `normalized_at`, allowing ingestion-based `known_at` to be moved back to January and exposed in an earlier PIT query. High findings involved conflicting revisions across resolved universes and endpoint params/fields hashed without verification against the partial canonical request. The open Medium items were a public snapshot loader, index-membership intervals/availability, and a stale next step in the root spec.

Green 8 required staging `normalized_at` to be at least as late as each referenced raw object's `ingested_at`. Independent replay checked official fields and parameters against the partial request's venue, date, instrument, index, period, and list status. Before selecting exact-universe coverage, the planner checked revision consistency across eligible canonical objects whose coverage intersected the request. The tests added three persisted counterexamples. Results: 83 passed/1 credential-dependent skip, Ruff, strict mypy without arguments, Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist, `tm-core` package verification, and diff checks passed. Review 8 was pending.

### Review 8 and Green 9

Review 8 failed with 0 Critical, 2 High, and 3 Medium. An older exact suspension record stayed valid after a later correction that still said `suspended=True`. `DataConfig.paths` lost the configured ETF set, allowing the provider and store to publish ETF data from stock `daily/stock_basic` endpoints. Medium findings covered overlapping/subsumed snapshot requests that duplicated objects, the public persisted-snapshot loader/verifier, and index-membership intervals/availability.

Failing tests reproduced both High findings. Without an explicit provider parameter the configured ETF was fetched through `stock_basic`. Other failures showed that two valid projections with different fields but identical scope duplicated a snapshot object, and there was no public loader.

Green 9 invalidated an exact absence record on any newer eligible status revision. `DataConfig` passed a sorted ETF set through immutable `DataPaths`. The provider used this set, and independent validation of raw requests and staging replay checked `daily/fund_daily` and `stock_basic/etf_basic` per instrument. The ETF store rejected even a complete stock-endpoint chain produced using paths without ETF classifications.

Before publication, snapshots merged field projections by dataset, time, universe, and coverage, referencing each object once. Public `load_snapshot()` rechecked identity, found the exact catalog objects, recomputed Parquet hashes/metadata, and verified that absence evidence was still current. `index_weight` became a dated composition snapshot with `effective_to=effective_from` and `known_at=ingested_at`. This avoids inventing historical announcement dates or open-ended index membership. Results: 87 passed/1 credential-dependent skip. Ruff, strict mypy without arguments, Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist contents, `tm-core` package verification, JSON, and diff checks passed. No `TUSHARE_TOKEN` was available, so live behavior remained unverified. Review 9 was pending.

### Review 9 and Green 10

Review 9 failed with 0 Critical, 3 High, and 1 Medium. An old True row in a status object still supported an absence record even though a newer row in the same object said False. Lowercase ETF IDs could route through stock endpoints. ETF `daily_basic` raw/staging produced with unclassified paths could still enter the ETF store. The Medium finding concerned overlapping narrow and wide coverage objects for one dataset. Green 9 only merged different field projections over identical scope. Independent reproducers were recorded at `/tmp/trademaster_m1_green9_review_repros.py`. M1 stayed open.

Four focused tests using real Parquet/DuckDB first failed for the lowercase ETF, latest False status, unsupported ETF `daily_basic` from an unclassified producer, and a subsumed request with overlapping coverage objects. Green 10 added one latest-status resolver for `(instrument, venue:session)` requiring a unique maximum `known_at`. Different content at the same time is an error. Only the latest True row can support absence. The store checks the evidence object's latest row, and snapshot verification replays its exact SHA. Config and paths normalize ETF identifiers to uppercase. Raw replay rejects all configured ETF datasets except `instrument_master/daily_bars`. Revision objects may overlap. The manifest proves the union of their distinct keys and removes a narrow request fully contained by a wider one.

Results: Python 92 passed. A mode-`0600` credential file outside the repository supplied a token only to the test process, and the real Tushare `trade_cal` smoke test ran and passed. Ruff, strict mypy without arguments (20 source files), Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist contents, `tm-core` package verification, canonical JSON, and diff checks passed. The token was absent from code, specs, logs, catalogs, manifests, and packages. Review 10 was pending.

### Review 10 and Green 11

Review 10 failed with 0 Critical, 3 High, and 0 Medium. Object-level `known_at_max` could skip an eligible correction stored alongside a post-cutoff row. PIT could then return a stale value and snapshot creation, loading, and queries could accept a stale suspension record. An empty upstream tuple bypassed raw/staging replay and ETF classification. Mutable endpoints refreshed only the short terminal page, hiding provider corrections on earlier full pages. The reviewer independently confirmed the previous 3 High/1 Medium repairs and that the real `trade_cal` request produced 1 raw + 1 staging Parquet file. M1 stayed open.

New Parquet/DuckDB tests showed all three errors. Tests for default provenance and refresh of every cached page first failed. Green 11 introduced `tushare_only` as the default canonical source policy and rejected missing upstream references. Only explicit fixtures or offline imports could use `trusted_imports`. The planner inspected each object's `known_at` rows, rejecting an object for the PIT set if it straddled the cutoff. The global revision audit evaluated each eligible row. The saved-snapshot loader replayed latest status at the original cutoff so an eligible nonsuspended correction in a mixed object invalidated old evidence. Mutable/allow-empty endpoints refreshed all pages from offset 0 and rejected contradictory page content within the same ingestion timestamp.

Results: 96 passed, 0 skipped, with real `trade_cal` using the external `0600` credential in the test process. Ruff, strict mypy without arguments (20 source files), Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist contents, `tm-core` package verification, canonical JSON, and diff checks passed. Review 11 remained required.

### Review 11 and Green 12

Review 11 failed with 0 Critical, 3 High, and 0 Medium. Checking mixed cutoffs after filtering for exact scope hid corrections in wider coverage or universe objects. Selecting one exact-scope object by maximum `known_at` discarded newer corrections for other keys in another object. Composite status could distribute three endpoints among multiple staging objects, allowing the combined rows to hide a real suspension. The reviewer confirmed all three preceding fixes and the Tushare smoke result. Reproducers were recorded at `/tmp/trademaster_m1_green11_review_repros.py`.

Four cases using actual Parquet and DuckDB first yielded 4 failed: wider coverage, wider universe, per-key merging within exact scope, and split status staging. Green 12 retained all eligible exact-scope objects and let DuckDB choose the latest row per primary key, `known_at`, and business revision. The planner then scanned all canonical objects for eligible rows in the request. If the newest fact existed only in a wider object that could not prove this request's coverage, snapshot and query failed. Each `daily_limits_status` staging object had to contain and replay all three endpoints itself. The staging union could establish equality with canonical rows but could not supply missing source evidence for an individual object.

All four focused cases then passed. The original external script rejected both wider-scope attacks and split staging, and returned `day2-corrected/day3-new` for the exact-scope case. Results: Python 100 passed/0 skipped with the real token. Ruff, strict mypy, Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist contents, `tm-core` package verification, canonical JSON, the credential scan, and diff checks passed. Review 12 was pending.

### Review 12 and Green 13

Review 12 failed with 0 Critical, 2 High, 2 Medium, and 1 Low. The persisted loader did not recheck for newer eligible revisions across the catalog, so a correction registered later failed to invalidate an old manifest. Raw evidence did not prove a short terminal page had actually been reached. Omitting the second `suspend_d` page could therefore produce `suspended=False`. The Medium findings concerned conflicts from rows outside the requested universe and duplicate shared objects in partially overlapping requests. The Low finding was the root spec's stale current-state section. All three previous High findings were independently confirmed closed.

Four real-file regressions first yielded 4 failed: a correction after manifest publication, an unterminated 5000-row status page, a disjoint-universe conflict, and a partially overlapping snapshot request. Green 13 made `load_snapshot()` rerun the global comparison of latest rows for each exact coverage object set at the manifest's original cutoff. A subsequently cataloged eligible correction invalidates the manifest. The object store groups raw pages by provider request digest. For each endpoint's fixed limit, it verifies a contiguous index and cursor from page 0, full intermediate pages, and a final page strictly shorter than the limit. Revision-identity and latest-row checks now use the same row-level event, instrument, coverage, and cutoff filters. The planner transitively merges requests with the same dataset and universe whose coverage keys overlap, producing one coverage record and a manifest of distinct objects.

All four focused cases then passed. The reviewer's script rejected strict/wider persisted manifests and incomplete status pagination, passed the partial-overlap case, and returned `[10.5]` for the disjoint query. Results: Python 104 passed/0 skipped with the real token. Ruff, strict mypy, Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist contents, `tm-core` package verification, canonical JSON, the credential scan, and diff checks passed. Review 13 was pending.

### Review 13, Green 14, and completion

Review 13 failed with 0 Critical, 3 High, and 0 Medium. Lexical source ordering in the loader could hide conflicting payloads or sources sharing a `known_at` timestamp. Mutable status pages could splice an old ingestion's page 0 to a newer ingestion's terminal page. The provider rejected repeated full-page fingerprints, but strict store replay accepted them. The reviewer confirmed the previous 2 High and 2 Medium repairs.

Green 14 checked both global revision identity and hidden newer rows for every saved coverage record. Equal-`known_at` conflicts are treated the same on fresh and saved snapshots. Mutable `suspend_d/stock_st` page chains must come from the latest single on-disk ingestion for the request. The supplied page-index-to-fingerprint map must equal that ingestion, and every nonempty page fingerprint must be unique. The three regression tests went from 3 failed to 3 passed. The original `/tmp/trademaster_m1_green13_review_repros.py` rejected persisted conflicts, mixed-ingestion chains, and duplicate pages. Controls for same facts/future observations and transitive request overlap passed.

Final checks: 107 Python tests passed, 0 skipped, with the real token supplied only to the test process. Ruff, strict mypy, Rust formatting, Clippy, 26 tests and doc-tests, wheel/sdist contents, `tm-core` package verification, canonical JSON, the credential scan, and diff checks passed. On 2026-08-11 the user ended the repeated M1 review cycle and chose one focused review for each of M2–M5 and one comprehensive review after the M6 end-to-end run.

## Completion criteria

- Unit and property tests cover request normalization, sets of coverage intervals, and content addressing.
- DuckDB/Parquet integration tests with a fake provider cover a complete cache, partial gaps, failure recovery, and point-in-time queries.
- Without `TUSHARE_TOKEN`, report the real end-to-end test as skipped. With credentials, run a minimal daily data request without recording the token.
- Require passing Ruff, strict mypy, pytest, Parquet schema checks, and DuckDB migration checks.
- Close known review findings and reserve the cross-system review for M6.
