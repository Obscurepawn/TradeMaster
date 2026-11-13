# TradeMaster v1 Architecture and System Contract

**Status:** Completed | **Date:** 2026-08-11 | **Scope:** Initial v1 architecture, shared Arrow artifact contracts, and M0–M6 milestone integration (`tm-core`, `tm-engine`, `tm-runner`, `trademaster`) | **Updated-by:** [Code Architecture Refactoring: Daily Snapshot SDK and Rust Portfolio Sizing](20261104-code-architecture-refactor.md), [Python and Rust Native Backtest Interface](20261105-python-rust-backtest-interface.md)

## Motivation and Context

TradeMaster v1 establishes a reproducible daily research and backtesting framework for Chinese A-shares and exchange-traded funds (ETFs), built from a greenfield repository baseline informed by nine reference quantitative systems. The architecture separates research flexibility from execution invariants: Python handles data ingestion, point-in-time (PIT) queries, factor computation, signal generation, and standalone HTML reporting, while Rust handles order validation, daily execution simulation, fee schedules, and atomic lot accounting.

Vectorized computation is restricted to factor and signal pipelines. Every formal backtest executes over an immutable Parquet/DuckDB **data snapshot** through the deterministic Rust event kernel so that trading constraints, cash balances, and lot-level settlement rules remain auditable after the run.

## Scope

### In Scope

- Daily A-share and ETF research workflows and event-driven backtests.
- Cache-first Tushare Pro ingestion into content-addressed raw, staging, and canonical Parquet tiers.
- DuckDB object and coverage catalog, PIT views (`known_at <= as_of`), and immutable data snapshots.
- Python factor directed acyclic graph (DAG), cross-sectional transforms, and signal generation.
- Rust event loop, A-share trading restrictions, exact fee calculation, and FIFO T+1 lot ledger.
- Multi-benchmark performance metrics, industry and market-cap exposure diagnostics, and standalone Plotly HTML reports.
- Deterministic synthetic strategies and credential-gated live Tushare end-to-end verification bundles.

### Out of Scope for v1

- Intraday (minute, tick, or Level-2) order-book simulation and market-impact modeling.
- A parallel vectorized cash or portfolio accounting engine.
- Complete Hong Kong or US brokerage and clearing implementations (exposed only as object-safe extension traits for calendars, fees, and FX across `SSE`, `SZSE`, `BSE`, `HKEX`, `NYSE`, and `NASDAQ`).
- Covariance matrices, idiosyncratic risk models, or factor risk-attribution models.

## Design Requirements and Architectural Decisions

### Contract-First Boundary

Public Python protocols (`python/trademaster/contracts.py`), Rust domain types and traits (`crates/tm-core/`, `crates/tm-engine/`), and the versioned Arrow reference schema (`trademaster.run-artifacts/v1`) were defined and verified in Milestone M0 before implementing subsystem behavior. Python and Rust read and validate the exact same canonical completed-run manifest and eleven sequenced Arrow/Parquet lifecycle tables: signals, order intents, accepted orders, rejections, fills, expiries, ledger postings, lots, positions, account snapshots, and `account_states` transitions.

### Market Causality and Execution Rules

- **Close-to-next-open causality:** Signals computed from session `T` closing data execute no earlier than the next eligible market event (the session `T+1` open). Unfilled daily orders expire at the end of the session without silent cross-day retries and remain recorded in the run artifacts.
- **A-share market constraints:** Trading suspensions block both buys and sells. Daily execution rejects buys at or above the upper price limit and sells at or below the lower price limit. Stock purchases require 100-share board lots, and shares bought under T+1 unlock for sale at the next trading session's settlement phase.
- **Exact ledger arithmetic:** Factor scores use floating-point arithmetic, whereas money, prices, fee rates, and quantities crossing into Rust ledger postings use exact `i128` (`Fixed8`) scaled integers.
- **Fail-closed validation and secret isolation:** Backtests halt immediately if required data snapshot inputs are missing or fail schema, hash, or coverage checks. Provider fallbacks and benchmark choices are recorded explicitly. Tushare credentials are read exclusively from the `TUSHARE_TOKEN` environment variable and excluded from request hashes, logs, manifests, and output bundles.

### Key Architectural Decisions

- **Immutable ETF path configuration (2026-08-10):** The configured ETF symbol set is part of immutable `DataPaths` configuration so provider routing (`etf_basic` / `fund_daily`), raw replay, canonical publication, and crash recovery enforce identical asset-class rules.
- **Dated index-membership observations (2026-08-10):** In v1, `index_weight` is stored as a dated composition observation whose canonical validity interval covers only the observed date (`effective_to = effective_from`) with `known_at` set to provider ingestion time; unobserved announcement or removal dates are never inferred.
- **Milestone review cadence (2026-08-11):** Following deep adversarial contract and PIT hardening in M0 and M1, Milestones M2–M5 each underwent one focused independent review, followed by a comprehensive end-to-end system audit at M6 completion.

## Milestone Summary and Acceptance Criteria

All eight v1 acceptance criteria passed prior to closing the specification:

- [x] **AC-1:** Public Python and Rust interfaces compile and conform to the shared `trademaster.run-artifacts/v1` Arrow schemas.
- [x] **AC-2:** Cache-first ingestion and PIT data snapshot queries pass real DuckDB/Parquet integration and tamper tests.
- [x] **AC-3:** Factor values and signals are cutoff-invariant (`known_at <= as_of`) and isolated from future return labels.
- [x] **AC-4:** A-share trading rules, exact fee schedules, T+1 lot unlocking, and atomic ledger transitions match hand calculations.
- [x] **AC-5:** Precomputed cross-sectional/single-asset signals and online event strategies execute through the same Rust event loop.
- [x] **AC-6:** Standalone HTML reports reproduce hand-calculated return, drawdown, risk, and multi-benchmark metrics.
- [x] **AC-7:** Synthetic and live Tushare end-to-end runs produce self-verifying bundles binding data snapshots, requests, traces, and reports.
- [x] **AC-8:** Independent review findings across M0–M6 are resolved and covered by regression tests.

| Milestone | Specification | Verification Summary |
|---|---|---|
| M0: Public contracts, schemas, architecture diagrams | `20260810-trademaster-v1.md` | 15 Python / 26 Rust contract tests; 4000px SVG render check; zero Critical/High/Medium issues at close |
| M1: Data storage and PIT queries | [M1 Specification](20260810-m1-data-foundation.md) | 107 Python / 26 Rust tests; live Tushare smoke test; 14 hardening cycles closed |
| M2: Factor and signal layer | [M2 Specification](20260811-m2-factor-signal-layer.md) | 122 Python tests; real M1 snapshot integration; focused review closed |
| M3: Rust trading rules and ledger | [M3 Specification](20260811-m3-rust-rules-ledger.md) | 34 Rust workspace tests; A-share limits, fees, and FIFO T+1 ledger; focused review closed |
| M4: Unified event runtime | [M4 Specification](20260811-m4-unified-event-runtime.md) | 39 Rust workspace tests; unified adapter and settlement sequence; focused review closed |
| M5: Performance metrics and reports | [M5 Specification](20260811-m5-metrics-reporting.md) | 128 Python tests; multi-benchmark diagnostics and standalone HTML; focused review closed |
| M6: End-to-end bundles and replay | [M6 Specification](20260811-m6-full-e2e.md) | 132 Python / 41 Rust tests; live Tushare full-chain replay (`0 -> 0` calls); system review closed |

## Changed Files

- **Workspace root:** `AGENTS.md`, `Cargo.toml`, `Cargo.lock`, `pyproject.toml`, `uv.lock`
- **Public contracts:** `crates/tm-core/`, `crates/tm-engine/`, `python/trademaster/contracts.py`
- **Schemas and contract tests:** `schemas/`, `tests/test_public_interfaces.py`, `tests/test_schema_golden.py`
- **Subsystem guides and architecture docs:** `crates/**/AGENTS.md`, `python/AGENTS.md`, `tests/AGENTS.md`, `docs/architecture/`, `specs/`

## Limitations and Risks

- **Provider permission tiers:** Tushare Pro endpoint availability depends on the caller's account tier; missing permissions fail closed rather than substituting incomplete histories.
- **Corporate-action completeness:** Continuous adjusted prices used in signal generation approximate splits and cash dividends; exact raw-price dividend accounting requires explicit corporate-action records (later added for daily snapshots in [Code Architecture Refactoring](20261104-code-architecture-refactor.md)).
- **Cross-language Parquet fidelity:** Python and Rust interoperability relies on strict Arrow schema agreement, lexical UTC microsecond formatting, and byte-level SHA-256 verification across all eleven run-artifact tables.

## Verification and Regression Record

### M0 Interface and Artifact Hardening (Cycles 1–11)

Before M1 began, M0 underwent eleven iterative test-and-review cycles to eliminate serialization mismatches and unverified trust boundaries between Python and Rust:

| Area | Failure Mode Identified in Review | Implemented Contract and Regression Guard |
|---|---|---|
| Public domain & trait accessors (Cycles 1–5, 8) | External `OrderValidator`, `ExecutionModel`, and `Ledger` implementations lacked read accessors for order intents, market/portfolio times, and fill context; duplicate accepted orders and backward fill timestamps were possible. | Added immutable getters on `SubmittedOrder`, `AcceptedOrder`, checked positive `Fill`, `ExecutionBatch`, `ValidationView`, and `LedgerEffect` (supporting zero-fill, multi-fill, and lot-unlocking transitions), plus object-safe calendar, fee, and FX traits. |
| Checked `InstrumentSpec` deserialization (Cycles 10–11) | Derived Serde deserialization allowed external JSON payloads to construct invalid `InstrumentSpec` values without calling `InstrumentSpec::try_new()`; fee quotes accepted arbitrary currency strings. | Implemented a custom Serde deserializer delegating strictly to `InstrumentSpec::try_new()`, derived fee quote currencies from the validated instrument, and added adversarial deserialization tests. |
| Byte-verified `RunManifest` & 11-table audit (Cycles 6–9) | Python and Rust serialized `RunManifest` differently; verifiers trusted caller-supplied hash maps instead of hashing Parquet bytes; cross-table order/fill/lot references could drift. | Unified canonical UTF-8 JSON serialization; added `account_states` as the 11th table; required `verify_run_manifest()` (Python) and `RunManifest::verify_artifacts` (Rust) to resolve only contained relative URIs, verify SHA-256 over raw Parquet bytes, decode typed rows, and check global IDs, quantity conservation, and `cash + market_value == NAV`. |
| Monotonic chronology & state-hash chain (Cycles 8–11) | Set-based timestamp checks missed backward transitions within a single event batch; Python/Rust wire parsers accepted loose timestamp formats or out-of-range row counts. | Defined a shared SHA-256 account-state digest over ordered microsecond/scaled-integer NAV, position, and lot payloads; validated transition timestamps monotonically in persisted order; enforced UTC `Z` timestamps (at most 6 fractional digits, rejecting year zero and nanoseconds), `u64` row counts, and strict unknown-field rejection. |
| Snapshot coverage & PIT views (Cycles 5–10) | Manifests could claim coverage exceeding actual Parquet contents, and strategy views did not enforce `event_time` cutoffs. | Bound requested interval, canonical field sets, resolved universe, and covered object evidence into `CoverageResult` and Arrow metadata digests; enforced UTC `event_time` cutoffs on factor, signal, and market views. |

At M0 closure, 26 Rust tests, 15 Python contract tests, `cargo fmt --check`, `cargo clippy -D warnings`, Ruff, strict `mypy`, wheel/sdist isolated imports (`py.typed` and embedded schemas), Cargo workspace packaging, and 4000px SVG rendering checks all passed with zero open Critical, High, or Medium findings.

### M1–M6 Subsystem and End-to-End Closure

Detailed verification records for the data layer, factor/signal pipeline, Rust ledger, event runtime, reporting, and end-to-end bundles are maintained in their respective milestone specifications ([M1](20260810-m1-data-foundation.md)–[M6](20260811-m6-full-e2e.md)). At v1 completion after resolving the single comprehensive M6 review (1 Critical, 4 High, 2 Medium):

- **Python suite:** 132 passed, 0 skipped (with live Tushare credentials supplied only to the test process from an external `0600` file), passing Ruff, strict `mypy`, and wheel/sdist build checks.
- **Rust suite:** 41 workspace tests and doc-tests passed, alongside `cargo fmt --check`, `cargo clippy -D warnings`, and isolated `cargo package` verification across `tm-core`, `tm-engine`, and `tm-runner`.
- **Deterministic replay:** Replaying the saved live Tushare run in a fresh process made zero provider calls (`0 -> 0`) and reproduced identical request, result, and standalone HTML SHA-256 digests.
