# TradeMaster v1 implementation

Status: completed
Owner: Codex
Updated: 2026-08-11

## Goal

Build a daily A-share research and backtesting framework. Python handles research,
data collection, and reporting. Rust handles execution and accounting. The inputs
are reproducible Parquet/DuckDB data snapshots, the applied trading rules can be
checked after a run, and each report can be read on its own.

## Context

The tracked repository started with only `.gitignore`. Nine ignored research
repositories and the comparison documents provided design references. The user
approved building from scratch with test-driven development (TDD) and an
independent review after each milestone.

## Scope

### In scope

- Daily A-share and ETF research/backtests.
- Tushare cache-first ingestion into raw/staging/canonical Parquet.
- DuckDB coverage catalog, PIT views, and immutable snapshots.
- Python factor DAG, cross-sectional transforms, and signal generation.
- Rust event loop, A-share restrictions, costs, lot-aware T+1 ledger.
- Multi-benchmark metrics, exposure diagnostics, and standalone HTML.
- Deterministic synthetic and credential-gated real-data E2E strategies.

### Out of scope

- Minute/tick/L2 simulation and market-impact modeling.
- A second vectorized cash/portfolio engine.
- Complete Hong Kong or US trading implementations.
- Covariance, specific-risk, or factor-risk-contribution models.

## Requirements and constraints

- Define the public interfaces and Arrow schemas before implementing their behavior.
- Signals computed after close execute no earlier than the next session open.
- Suspension blocks trading in both directions. The daily model rejects buys at the upper limit and sells at the lower limit.
- A-share purchases use 100-share lots. Stock bought today becomes sellable at T+1.
- Unfilled daily orders expire and remain auditable.
- A backtest stops if its required inputs are missing or fail validation. Record any provider fallback or substitute benchmark explicitly.
- Tushare credentials come only from `TUSHARE_TOKEN`.
- Use tests appropriate to each milestone. M2–M5 each receive one independent review.
  After M6 end-to-end testing succeeds, review the full workflow once in depth.

## Acceptance criteria

- [x] AC-1: The public Python/Rust interfaces compile and agree with the shared Arrow reference schemas.
- [x] AC-2: Cache-first data and PIT snapshot behavior pass integration tests.
- [x] AC-3: Factors/signals are cutoff-invariant and future labels are isolated.
- [x] AC-4: A-share rules, exact costs, T+1 lots, and atomic ledger pass tests.
- [x] AC-5: Cross-sectional and event strategies share the Rust event runtime.
- [x] AC-6: Reports reproduce hand-calculated metrics and multiple benchmarks.
- [x] AC-7: Synthetic E2E and permitted Tushare E2E complete with bundles that identify their inputs and results.
- [x] AC-8: Review findings for each milestone have been addressed.

## Plan

| Task | Owner | Status | Notes |
|---|---|---|---|
| M0 interfaces, specs, AGENTS, diagrams | Codex | done | Independent review PASS; no Critical/High/Medium |
| M1 data foundation | Codex | done | 107 Python/26 Rust; real Tushare; known review findings closed |
| M2 factor and signal layer | Codex | done | 122 Python; real Tushare; focused review closed |
| M3 Rust rules and ledger | Codex | done | 34 Rust tests; A-share constraints; focused review closed |
| M4 unified event runtime | Codex | done | 39 Rust tests; unified chain; focused review closed |
| M5 metrics and report | Codex | done | 128 Python; standalone HTML; focused review closed once |
| M6 E2E and performance | Codex | done | Real full-chain E2E/cache replay; one global review closed |

## Decisions

- 2026-08-10: Greenfield Python research layer plus Rust event kernel.
- 2026-08-10: Vectorize factors/signals only; all formal backtests use events.
- 2026-08-10: V1 is daily and A-share-first; overseas markets are interfaces.
- 2026-08-10: Unfilled orders expire daily; no automatic cross-day retries.
- 2026-08-10: Each milestone requires an independent review.
- 2026-08-11: Limit M2–M5 to one focused review per milestone. Conduct one
  comprehensive system review after the M6 end-to-end tests pass.
- 2026-08-10: The configured ETF set is part of the immutable data-path configuration.
  The provider, raw replay, object publication, and recovery must all use that same set.
- 2026-08-10: `index_weight` is a dated composition snapshot in V1. Its canonical interval is
  the observed date only and its availability is provider ingestion time; unobserved removal or
  announcement dates are never inferred.

## Current state

### Completed

- Planning approved the architecture and product scope. The team checked the indexes of
  nine candidate projects and used the research notes to locate relevant implementations.
- M0 Python/Rust public interfaces and the shared artifact schema are implemented.
- M0 architecture document and editable SVG passed structural and visual review.
- The first M0 review's 6 high and 4 medium findings have implementation fixes and
  regression tests. Its first re-review closed 5 and kept 5 open.
- A second independent review found 2 critical, 7 high, 3 medium, and 1 low interface gaps.
  External implementations could not validate orders, execute them, or update the ledger
  with the data those traits exposed. The team revised the interfaces and retested them.
- The next review identified backward timestamps and duplicate orders, unclear event
  phases, and errors in no-op ledger updates, data coverage checks, the shared run
  manifest, packaging, and overflow handling. Each issue got a failing test and a fix
  even though the existing unit tests passed.
- A subsequent review found one critical difference in how Python and Rust serialized
  `RunManifest`. The five high issues concerned proof of data coverage, what a strategy
  could read at a given time, which lots it could sell, whether ledger hashes matched
  accounts, and whether records across the run referred to the same orders and events.
  Green 6 used a shared test fixture and added checks for views and account changes in
  both languages.
- The Green 6 reviewer found that a manifest could claim files it had not checked,
  claimed coverage could exceed what a file contained, and tables and portfolios could
  disagree on time. Ledger effects supplied only hashes, and related rows in different
  tables could disagree. Green 7 verified the actual Parquet files and their coverage,
  checked portfolios as of each event, replayed cash and lot changes, and joined signals,
  orders, event sequences, ledger entries, and lots to verify their references.
- In the Green 7 review, external Rust validators and ledgers could not read all their
  inputs. The decoder accepted invalid representations, session coverage could have gaps,
  PIT tables did not enforce their cutoffs, and a run could finish without linking its
  account updates. Green 8 provided the missing accessors and added an eleventh stored
  table, `account_states`. It records the hashes before and after each executed order.
- The Green 8 review found that the verifier trusted the recorded hashes instead of
  recomputing them from the portfolio tables. Rust also accepted rows supplied separately
  from their serialized bytes. Green 9 defined a shared account-state hash and
  made Rust decode the actual Parquet bytes it hashes.
- The Green 10 review found that derived Rust deserialization bypassed `InstrumentSpec`
  validation and that set-based chronology checks hid time reversal inside one event. Green 11
  routes deserialization through checked construction and validates transition time in stored
  order.
- The final independent M0 review and focused follow-up passed with no remaining
  Critical/High/Medium findings. Its only two non-blocking findings were closed by a checked-in
  Rust backward-transition regression test and corrected eleven-table diagnostics.
- M6 ran real Tushare data through raw, staging, and canonical Parquet, a DuckDB/PIT
  data snapshot, adjusted-price factors and signals, Rust execution and account records,
  and standalone HTML. It also added a three-instrument synthetic end-to-end test for
  periodic rebalancing and exits. The single final system review raised 1 Critical,
  4 High, and 2 Medium findings. All were closed together without another review cycle.
- Final checks passed for 132 Python tests (including the real credential-dependent case),
  41 Rust tests, Ruff, strict mypy, formatting, Clippy, doc-tests, wheel/sdist, and
  isolated Rust package checks. Replaying the saved real run in a new process made
  0 provider calls on both passes (0 -> 0) and reproduced the request and result identities.

### In progress

- None.

### Blocked

- None. A mode-`0600` file outside the repository supplied the real Tushare token
  only to the individual test process.

## Changed files

- Root: `AGENTS.md`, `Cargo.toml`, `Cargo.lock`, `pyproject.toml`, `uv.lock`.
- Contracts: `crates/tm-core/`, `crates/tm-engine/`, `python/trademaster/contracts.py`.
- Schemas/tests: `schemas/`, `tests/test_public_interfaces.py`, `tests/test_schema_golden.py`.
- Guidance/docs: `crates/**/AGENTS.md`, `python/AGENTS.md`, `tests/AGENTS.md`,
  `docs/architecture/`, `specs/`.

## Historical verification record

The counts and outstanding checks below describe each stage when it was recorded.

- Red 1: `PYTHONPATH=python pytest -q tests/test_public_interfaces.py` failed because
  `trademaster`/public protocols did not exist.
- Red 1: `cargo test -p tm-core --test public_contract` and
  `cargo test -p tm-engine --test public_traits` failed on the missing public types/traits.
- Green 1: the same focused Python and Rust contract tests passed.
- Red 2: shared-schema tests failed because the versioned reference schema and embedded Rust
  constant did not exist.
- Green 2: Python and Rust shared-schema tests passed after adding
  `trademaster.run-artifacts/v1`.
- Red 3: the second Python cycle failed on missing typed snapshot objects and artifact
  validation; the Rust cycle failed on missing exact values, checked order construction,
  shared Arrow schemas, and strict accepted-order accounting.
- Green 3: added UTC/range/value validation, typed content-addressed snapshot objects,
  nine lifecycle schemas, exact `i128` fixed values, stable wire enums, immutable runtime
  views, strict `ExecutionBatch`, and Rust-to-Arrow `RecordBatch` materialization.
- Rust checks: format check, Clippy `-D warnings`, 9 workspace tests and doc-tests passed.
- Python checks: 6 tests passed; Ruff and strict mypy passed in the project uv environment;
  source distribution and wheel built successfully.
- Config/schema parsing and `git diff --check` passed.
- SVG checks: structure/rsvg validation passed; a 4000px full render was visually inspected.
  Data now enters the factor DAG and signals enter the event runtime before validation/ledger.
- Independent review 1: no critical; 6 high and 4 medium findings. M0 was correctly kept open.
- Re-review 1: 5 findings closed; lifecycle, shared enum, manifest identity, ledger
  semantics, and their behavior tests remained open, so M0 stayed open.
- Independent review 2: found public orders unreadable to external validator/execution
  implementations and ledger executions without a reliable instrument and side,
  plus deterministic replay, lot snapshot, PIT cutoff, value-domain and packaging gaps.
- Red 4: new tests failed to compile because `OrderIntent` lacked getters,
  `OrderExecution` lacked accepted-order context and checked outcomes, and ledger lacked
  account snapshots/posting groups. Python tests failed on missing canonical manifest,
  event/portfolio view, quantity bounds, enum semantics, and replay ordering.
- Green 4: introduced checked `SubmittedOrder`/`AcceptedOrder`, readable immutable order
  views, quantity-conserving ordered execution outcomes with auditable expiry, balanced
  ledger groups, `AccountSnapshot` with lots, canonical content-hashed/PIT-safe snapshots,
  aligned Python event views, eleven sequenced artifacts, shared enum vocabularies, schema
  metadata/hash binding, and typed Rust domain-to-Arrow adapters for every artifact.
- Red 5: tests failed on missing checked `Fill`, outcome chronology, duplicate accepted
  orders, observable Rust event phase, zero/multi-group ledger effects, request-bound
  snapshot coverage/provenance, coverage consistency, and a shared Python run manifest.
- Green 5: added checked positive fills and time-monotonic execution outcomes; unique
  accepted orders; aligned phase/history strategy views; state-hashed `AccountSnapshot`
  and no-op/multi-fill/lot-aware `LedgerEffect`; request, policy and per-dataset coverage
  inside the snapshot content hash; table provenance; shared artifact semantics and run
  manifest contracts packaged inside both Cargo and Python artifacts; checked overflow.
- Red 6: shared fixture and behavior tests initially failed on Rust/Python time encoding,
  missing artifact bindings, incomplete coverage identity, mutable/unbounded event views,
  arbitrary ledger hashes, zero-sized lot changes, and IDs unique only per sequence batch.
- Green 6: Rust and Python now serialize and parse the exact same canonical completed-run
  fixture; manifests bind all eleven artifact URIs/hashes/row counts; snapshot coverage binds
  the requested interval, fields and resolved universe; Arrow metadata binds request and
  object-set digests; checked event/portfolio views reject future data and invalid holdings;
  account sellable quantities derive from unlocked lots; ledger effects bind actual before
  and after snapshots; per-table global IDs and cross-table order chronology/quantity
  conservation are validated in both languages.
- Green 7: `verify_run_manifest()` now resolves only contained relative URIs, hashes and reads
  every Parquet, checks row counts/run IDs, then applies the eleven-table validator; Rust exposes
  the corresponding writer-bound rows/content-hash verifier. Wire parsing rejects unknown
  fields, nanosecond timestamps and row counts outside `u64`. Snapshot objects bind their
  fields, universe and request-spanning interval; Arrow views inspect real event-time values
  and portfolio event/state identity. Ledger effects replay cash and lot transitions against
  before/after snapshots. Completed-run checks preserve signal instrument/eligibility,
  referenced sequences, ledger sources, and per-event position/lot T+1 identities.
- M1 now has an executable data-foundation spec covering directories, DuckDB catalog columns,
  responsibility for coverage, failing/passing integration cases, and acceptance checks.
  The diagram traces snapshot provenance, verification of the eleven tables, and
  interfaces for adding overseas markets.
- Green 8: external Rust implementations can read every market/portfolio/fill input required
  by rules and ledger code; validation views bind portfolio and market time. Python manifest
  parsing is strict about lexical UTC, microseconds, JSON integer row counts and unknown fields.
  Coverage uses explicit session/business keys whose object union must exactly equal the
  request, and all factor/signal/market Arrow tables require a UTC `event_time` cutoff.
  Completed runs bind manifest strategy/snapshot identity, monotonic fill time, exact
  fill-posting event/source relations, NAV per fill event, one account transition per executed
  order and an unbroken state-hash chain. Rust writer verification hashes supplied serialized
  bytes rather than trusting a digest map. Calendar, fee and FX provider traits are object-safe
  interfaces for adding markets. M1 further specifies canonical datasets, keys, coverage
  hashing, atomic publication and recovery, and checks for each task.
- Green 9: account state identity is the SHA-256 of the same ordered microsecond/scaled-integer
  NAV/positions/lots payload in Rust and Python; every event's final transition hash must equal
  the recomputed artifact state, NAV must balance, and fill event time is globally monotonic.
  Rust `RunManifest::verify_artifacts` now accepts only Parquet bytes, verifies their byte hash,
  decodes them with the Parquet reader, reconstructs typed rows and then runs the lifecycle
  validator. Rust wire parsing enforces `Z` and at most six fractional digits. Snapshot models
  use strict/forbid-extra parsing, `CoverageResult` returns missing keys and covered object
  evidence, and the catalog spec persists coverage-key sets/digests. Venue, fee, currency and
  checked quantity inputs are explicit for SSE/SZSE/BSE/HKEX/NYSE/NASDAQ extension.
- Green 10: canonical state JSON uses raw UTF-8 in both languages; account artifacts enforce
  position-market-value/NAV equality and common valuation time. Fill and settlement transitions
  jointly enforce global event chronology. Complete keyed coverage requires object evidence and
  object field/key sets are canonical. Rust rejects year zero. `InstrumentSpec` is checked and
  immutable, while fee quotes derive currency from that instrument rather than a free string.
- Red 11: an external deserialization probe constructed an invalid `InstrumentSpec` without
  `try_new()`, while valid state hashes with transition time moving backward inside one event
  passed both Python and Rust completed-run validation.
- Green 11: `InstrumentSpec` has a custom strict deserializer delegating to `try_new()`; Python
  and Rust validate account transition time monotonically in persisted transition order. Both
  adversarial cases now have regression tests.
- Checks at that stage: Rust fmt/Clippy and 26 tests pass;
  Python Ruff/strict mypy (including tests) and 15 tests pass; wheel/sdist build and an
  isolated wheel import sees `py.typed` plus both schemas; `tm-core` package verifies and
  Cargo packages the whole workspace; JSON/SVG and `git diff --check` pass.
- M0 final review: independent reviewer reproduced the fixes of both Green 10
  counterexamples, then re-reviewed its two non-blocking findings after they were fixed. Final
  result: PASS with no Critical, High, or Medium finding.
- M1.1: configurable safe data/log roots store only the Tushare token variable name; the
  ten-dataset registry defines per-dataset identity, partition and coverage-key rules; request
  normalization is deterministic and rejects unknown fields. Focused and full Python checks pass.
- M1.2: the versioned DuckDB catalog stores only immutable-object and coverage indexes. Atomic
  idempotent publication rejects conflicting metadata and unknown schema versions; real DuckDB
  migration/rollback tests and all Python checks pass.
- M1.3: canonical Parquet is atomically content-addressed and self-describes the request needed
  for crash recovery. Real files pass publish/read/recompute/recovery tests; tampering and invalid
  primary-key revisions are rejected before formal use.
- M1.4: cache-first planning verifies actual Parquet before accepting DuckDB coverage, requests
  only missing keys, rejects incomplete/duplicate provider pages, and re-proves completeness after
  publication. Full cache, partial cache and pagination cases pass.
- M1.5 slice 1: credential-isolated Tushare `trade_cal`/`daily` adapters persist raw and staging
  evidence and emit canonical pages. Fixture tests pass; credential-gated network smoke is skipped
  because `TUSHARE_TOKEN` is absent. Remaining registry normalizers are still open.
- M1.5 complete: all ten registry datasets have fixture-backed Tushare normalization and must pass
  the same canonical object-store validation; composite limit/suspension/ST status remains explicit.
- M1.6: immutable snapshot replacement selection is cutoff-aware; DuckDB PIT queries choose the
  last revision by business key and bind request/object provenance. Determinism and tamper tests pass.
- M1 independent review 1: kept M1 open with one PIT Critical and five High findings covering schema
  drift, catalog-proof ownership, universe/key resolution, official Tushare contracts and endpoint
  pagination. Two provenance/migration Medium findings will be closed in the same Green cycle.
- M1 Green 2 closes the review findings with exact canonical Arrow types, object-bound DuckDB
  coverage proofs and digest checks, an explicit request resolver, corrected official
  endpoint fields/parameters, independent endpoint pagination, deterministic availability times,
  raw-to-staging content hashes, and full DuckDB type/nullability/identity validation. Verification:
  Python 56 passed/1 credential skip, Ruff/mypy/package Green; Rust fmt/Clippy/26 tests/docs Green.
  M1 remains open until a new independent reviewer finds no Critical or High issue.
- M1 independent review 2 found that the Green 2 proof was still only marginal rather than relational:
  it accepted incomplete instrument-by-session matrices, mutable resolver intent, proof rows not
  bound to the normalized request, and non-exact Arrow layout. It also found unreachable delisting
  events, fabricated industry effective dates, unfiltered auxiliary status dates and hash-ordered
  revision ties. M1 remains active for a third Red/Green closure cycle.
- M1 Green 3 makes required status coverage relational with a venue-scoped
  instrument-by-session matrix, reconstructs each catalog proof from immutable Parquet request
  metadata, specifies exact Arrow column order and nullability, and makes resolvers additive-only.
  Delisting and industry effective events, auxiliary status dates, business revisions and catalog
  metadata cardinality now have persisted adversarial regression tests.
- M1 independent review 3 found six remaining High gaps: sparse daily-bar proof, object/partition
  universe conflict, ambiguous equal business revisions, an incomplete canonical provenance
  chain, missing ETF adapters, and cursor evidence without crash resume. Green 4 responds with
  strict bar/status matrices, universe-compatible partitions, ambiguity rejection, upstream hash
  binding, configured `etf_basic/fund_daily` routing, and verified raw-page resume.
- M1 independent review 4 showed that strict rejection alone blocked legitimate suspended histories and
  that provenance hashes still needed existence/replay checks. Green 5 adds status-object-bound
  absence proofs and automatic snapshot dependencies, logical stock/ETF aggregation, cross-object
  business-revision conflict detection, filesystem-backed upstream verification, refreshable empty
  endpoint results, and ETF lifecycle validation that rejects unsupported statuses or missing required dates.
- M1 Green 6/7 closes content-addressed absence identity, all-suspended empty bars, ETF formal
  portal paths, mutable empty-page revisions, complete-payload revision conflicts, and independent
  ten-dataset raw normalization replay. Mixed stock/ETF sparse requests and explicit status
  snapshots now use proof-derived dependencies without duplicate objects.
- M1 Green 8 binds staging normalization time to referenced raw ingestion time, validates every
  raw endpoint fields/params against its partial canonical request, and audits revision identity
  across overlapping coverage regardless of resolved-universe boundaries. All 83 Python tests
  and the unchanged Rust contract suite passed. The next independent review was still pending.
- M1 independent review 8: FAIL with 0 Critical/2 High/3 Medium. A later status revision that remained
  suspended could leave an older exact absence proof valid, and configured ETF identity was lost
  by `DataConfig.paths`, allowing an unconfigured provider/store path to publish stock-endpoint
  evidence for an ETF. The non-blocking Medium findings cover subsumed snapshot requests, a public
  persisted-manifest loader/verifier, and index-membership interval availability.
- M1 Green 9 makes every later eligible status revision invalidate the exact absence proof it
  supersedes, even when the corrected row remains suspended. `DataPaths` now carries the canonical
  ETF set from `DataConfig`; provider routing and independent raw/staging replay both reject stock
  endpoint evidence for configured ETFs. A test used a provider with no ETF classification
  to fetch stock `daily` data for an ETF and confirmed that the store rejected it.
- Green 9 also merges compatible snapshot projections into one request/object proof and exposes a
  persisted snapshot loader that revalidates manifest identity, catalog metadata, Parquet bytes,
  absence dependencies, and cutoff semantics. Index membership no longer invents an infinite
  interval: V1 persists the exact dated `index_weight` snapshot and ingest-time availability.
- Green 9 checks: Python 87 passed/1 credential-gated skip; Ruff and unparameterized strict
  mypy pass; Rust fmt/Clippy, 26 tests and doc-tests pass; wheel/sdist content and `tm-core` package
  verify pass; JSON and `git diff --check` pass. The skip remains unverified network behavior.
- M1 independent review 9: FAIL with 0 Critical/3 High/1 Medium. It reproduced an exact absence proof
  built from an older suspended row even though the same status object had a newer non-suspended
  revision; lowercase ETF identifiers routed and published through stock endpoints; and a stock
  `daily_basic` chain produced under unclassified paths was accepted by the store configured for ETFs. The remaining Medium is overlapping/subsumed coverage-object selection, beyond the exact
  same-scope field projection merge closed in Green 9.
- M1 Green 10 introduces one shared latest-status revision function used by proof construction and
  object-store evidence verification; snapshot freshness independently requires the exact evidence
  object's latest unique revision to remain suspended. ETF IDs canonicalize to uppercase before
  the data paths configuration uses them, and raw replay rejects every configured-ETF dataset except for the supported
  `instrument_master` and `daily_bars` datasets. Overlapping immutable coverage objects may now
  coexist as revision evidence; snapshot coverage proves the exact key-set union and removes a
  request already subsumed by an equal-or-wider request.
- Green 10 checks: Python 92 passed with the real credential-gated `trade_cal` network smoke
  executed rather than skipped; Ruff and strict mypy pass over 20 source files; Rust fmt/Clippy,
  26 tests and doc-tests pass; wheel/sdist content, canonical JSON, `tm-core` package verify and
  `git diff --check` pass. The credential is stored outside the repository with mode `0600`, loaded
  only into the test process environment, and is absent from specs/logs/artifacts.
- M1 independent review 10: FAIL with 0 Critical/3 High/0 Medium. An object's future `known_at_max`
  could hide a cutoff-eligible correction in another row and preserve stale PIT values/absence
  proofs; omitting upstream hashes bypassed all checks that replay raw/staging data and ETF checks;
  and mutable pagination refreshed only the terminal short page, so corrections in an earlier
  cached full page remained permanently stale. The Green 9 findings and real Tushare evidence
  were independently confirmed closed/working.
- M1 Green 11 requires source evidence by default when publishing canonical objects, with an
  explicit `trusted_imports` mode limited to intentional fixtures/offline imports. PIT planning
  rejects immutable objects that straddle the requested `known_at` cutoff, while revision
  consistency and persisted absence-proof freshness evaluate eligible rows rather than skipping
  the whole object by `known_at_max`. Mutable/allow-empty endpoints refresh every cached page from
  offset zero so corrections in a prior full page become visible. The focused counterexamples and
  the full 96-test Python suite pass with the real Tushare smoke executed and no skip; independent review
  11 remains required before M1 can close.
- M1 independent review 11: FAIL with 0 Critical/3 High/0 Medium. A correction could remain hidden when
  it lived only in a wider coverage or universe object; exact-scope objects still competed by one
  object-level `known_at_max`, losing per-key corrections; and composite status provenance could
  split its three endpoints across staging objects and wash a real suspension into `False`.
- M1 Green 12 keeps all eligible exact-scope revision objects and lets the PIT query rank revisions
  per business key. It also compares selected rows with request-scoped rows across every canonical
  object and rejects the query when the newest eligible fact exists only outside the selected scope.
  Each status staging must now independently replay all three composite endpoints. Four persisted
  RED cases are Green, the reviewer's original attack script is closed, and the real-token Python
  suite is 100 passed with no skip. Ruff/mypy, Rust fmt/Clippy/26 tests/docs, Python packages,
  `tm-core` package, JSON, credential scan and diff checks all pass; independent review 12 remains pending.
- M1 independent review 12: FAIL with 0 Critical/2 High/2 Medium/1 Low. Persisted manifests did not rerun
  global correction checks, and raw status provenance could omit the next page after a full 5000-row
  `suspend_d` page. Disjoint-universe conflicts also leaked into narrow requests, while partially
  overlapping requests duplicated their shared object. Green 12's original three Highs were closed.
- M1 Green 13 revalidates every persisted coverage against currently catalogued revisions at its
  original cutoff; proves each raw request has a contiguous page-zero-to-short-terminal chain using
  the endpoint's fixed limit; scopes revision checks by row instrument/key/time; and transitively
  unions overlapping requests for the same dataset/universe. Four persisted RED cases and the
  reviewer's external attacks are Green; real-token Python is 104 passed with no skip. Ruff/mypy,
  Rust fmt/Clippy/26 tests/docs, Python packages, `tm-core` package, JSON, credential and diff checks
  all pass; independent review 13 remains pending.
- M1 independent review 13 found three final Highs: persisted same-`known_at` conflict drift,
  cross-ingestion mutable-page splicing, and duplicate full-page fingerprints. Green 14 makes
  persisted and fresh identity checks identical, binds mutable chains to the latest single
  ingestion observation, and rejects repeated non-empty page fingerprints. The three external
  reproductions are rejected, while same-fact/future and transitive-overlap controls pass.
- M1 final checks: Python 107 passed/0 skipped with real Tushare; Ruff/mypy, Rust fmt/Clippy/26
  tests/docs, wheel/sdist, `tm-core` package, JSON, credential and diff checks pass. Per the user's
  2026-08-11 process decision, M2-M5 use one focused review each and M6 performs the final comprehensive
  review after E2E succeeds.

## Risks recorded during planning

- Provider permissions may not cover every requested Tushare dataset.
- Historical corporate-action completeness must be proven before formal E2E.
- Reading the same persisted Parquet bytes in both languages was an M1 integration risk.
  M0 specified identical Arrow schemas and tested all eleven typed Rust domain adapters.

## Next step recorded at the end of interface design

The recorded next step was to write the M2 factor/signal specification, settle its
public interfaces, and implement it with TDD. The milestone table above records
subsequent completion.
