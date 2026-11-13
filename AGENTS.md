# TradeMaster Agent Guide

- **Backtesting & Data Workflows**: Use the [TradeMaster backtesting skill](.skills/trademaster-backtest/SKILL.md) when implementing strategies, importing historical market data, configuring Tushare or IndexOne, replaying saved runs, or inspecting backtest reports.
- **Contributor & Repository Orientation**: See [CONTRIBUTING.md](CONTRIBUTING.md) for local environment setup and contribution workflow, and [docs/reference/repository.md](docs/reference/repository.md) for the file-by-file module and test map.

## System Architecture

TradeMaster pairs a Python research and data-engineering layer with a deterministic Rust execution and accounting engine (`trademaster._native`, compiled via PyO3/maturin). It ships with China A-share and ETF trading defaults while supporting US equities and other markets through explicit per-instrument parameters (`venue`, `currency`, `buy_lot_size`, `tick_size`, `settlement` as `t1` or `t0` share resale availability) and run-level execution policies (commission, stamp duty, transfer fees, slippage, and optional `enforce_price_limits`).

### Data Sources and Point-in-Time Storage

- **Tushare (`trademaster.data`)**: `TushareProvider` and `CacheFirstDataPortal` persist raw and staged provider records as immutable Parquet objects, track coverage and revisions in DuckDB, and publish verified point-in-time (PIT) **data snapshots**.
- **IndexOne (`trademaster.data.indexone`)**: `IndexOneDataSource` queries read-only market prices, company financials, security reference data, and FX rates into an isolated DuckDB request index and immutable Parquet cache. Because fetch timestamps record collection time rather than historical publication, IndexOne observations require explicit calendar, opening-permission, and corporate-action evidence before publication into a backtest data snapshot.

### Execution Paths

Both execution modes share `tm-core` fixed-point domain types (`Fixed8` and integer share units) and `tm-engine`'s FIFO `LotLedger`:

1. **Daily Snapshot Backtest (`trademaster.backtest` -> `tm-python` -> `tm-engine::snapshot`)**: Python publishes feature Parquet files, a fixed-point `market.json`, and a hash-verified manifest. Rust advances session by session in process, invokes Python's `on_close(day)` callback at scheduled closes with read-only closing balances and intervening fills, and receives complete target portfolio weights (or `None` to retain active targets). Before the next eligible open, Rust fixes whole-lot order quantities from prior closing marks and reserves cash separately, validates opening trading permissions, and updates cash, FIFO share lots, and cash-dividend receivables.
2. **Event Loop & Verifiable Run Bundles (`trademaster.research` / `factors` / `signals` / `e2e` -> `tm-runner` -> `tm-engine::EventLoop`)**: Resumable research plans (`tm-research-full-a`) assemble full-universe PIT panels, materialize dependency-ordered managed factors (`tm-factor`) into Parquet, and emit dated Arrow signal tables. `tm-runner` executes canonical event requests against `LotLedger`, while v3 run bundles bind source manifests, factor lineage, 11 typed audit tables, and offline HTML/Markdown reports (`trademaster.reporting`) for deterministic verification and replay (`tm-replay`).

### Workspace Map

| Path | Responsibility |
|---|---|
| [`crates/tm-core`](crates/tm-core/AGENTS.md) | Exact fixed-point scalars, stable IDs, immutable domain records, Arrow adapters, and [shared JSON schemas](crates/tm-core/schemas/AGENTS.md). |
| [`crates/tm-engine`](crates/tm-engine/AGENTS.md) | `EventLoop`, `LotLedger`, configurable order validation and fee schedules, and the daily `snapshot` engine. |
| [`crates/tm-runner`](crates/tm-runner/AGENTS.md) | Canonical JSON request decoding, provenance hash verification, and replay entry points. |
| [`crates/tm-python`](crates/tm-python/AGENTS.md) | PyO3 `abi3-py312` native extension (`trademaster._native`) bridging Python callbacks and byte-slice replay to Rust. |
| [`python/trademaster`](python/AGENTS.md) | [`backtest`](python/trademaster/backtest), [`data`](python/trademaster/data/AGENTS.md), [`research`](python/trademaster/research/AGENTS.md), [`factors`](python/trademaster/factors/AGENTS.md), [`signals`](python/trademaster/signals/AGENTS.md), [`strategies`](python/trademaster/strategies/AGENTS.md), [`e2e`](python/trademaster/e2e/AGENTS.md), and [`reporting`](python/trademaster/reporting/AGENTS.md). |
| [`schemas/`](schemas/AGENTS.md) · [`tests/`](tests/AGENTS.md) · [`specs/`](specs/INDEX.md) | Public schema guide, Python integration/regression suites, and milestone design specifications. |

## Domain and Execution Invariants

- **Immutable Parquet storage and manifest lineage**: Persist all provider extracts, staged tables, factor values, and evaluation panels in Parquet; use DuckDB for coverage tracking, discovery, and as-of queries. Bind every input file and SHA-256 digest to its snapshot manifest, and publish a new manifest whenever any upstream input changes rather than mutating an existing manifest.
- **Explicit failure and missing-evidence surfacing**: Surface missing observations, schema mismatches, and provider permission errors immediately at the failing boundary. When required prices, opening permissions, or corporate-action continuity are missing for a held or targeted instrument, the daily engine halts cleanly with `status="insufficient_data"` and records `first_issue`.
- **Causal execution timing**: Any feature, factor score, or target signal informed by a session's close (or a close-time decision cutoff) may execute no earlier than the next eligible market open.
- **Distinct audit records**: Represent order intents, accepted orders, fills, rejections, expiries, ledger postings, and account/NAV snapshots as separate, immutable domain types.
- **Exact monetary and quantity arithmetic**: Factor research and statistical diagnostics may use IEEE-754 floating point, whereas all cash balances, prices, fee/tax rates, target weights, and share quantities crossing into execution or posted to `LotLedger` must use exact fixed-point (`Fixed8`) or integer representations.
- **Cash dividends and explicit instruments**: On raw (unadjusted) execution prices, determine entitled share counts from held shares at the `record_session` close, adjust the prior mark and accrue `dividend_receivable` into account equity before sizing on `ex_session`, and credit cash at the `pay_session` close so it becomes spendable at the next open (`record_session < ex_session <= pay_session`). Keep the signal instrument and traded instrument explicit (such as an index signal driving ETF execution).
- **Managed factor lineage and public candidates**: Each `factor_id@version` uniquely binds its formula code, parameters, and dataset or upstream factor dependencies; bump the version whenever a definition changes. Published materializations must record source data snapshots, input hashes, parent factor materializations, and output hashes. For public factor candidates (`factors/library/`), record the source release, licensing constraints, dependencies, computability, and review status; only candidates linked to a verified managed definition (`implemented` or `implemented_variant`) may produce values.
- **Factor vs. strategy evaluation**: Full-market A-share studies must record both market and factor input manifests alongside evaluation settings. Because compounding overlapping multi-day forward returns double-counts trading sessions, evaluate factor predictive power with non-compounded horizon diagnostics and cross-sectional quantile returns (alongside IC / Rank IC), and measure realizable strategy performance strictly from the Rust `LotLedger` equity curve under explicit trading rules.
- **Deterministic target-weight rebalancing**: Process sell orders before buy orders within each rebalance batch and sort each side deterministically by `instrument_id`. Signal IDs and order IDs may break final ties, but renaming identifiers must never change portfolio allocations.
- **Secret hygiene and read-only reference trees**: Read credentials (`TUSHARE_TOKEN`, `INDEXONE_API_KEY`) strictly from environment variables and never persist them in source files, logs, DuckDB catalogs, manifests, or saved run outputs. Treat `research/` and `docs/research/` as read-only reference material.

## Documentation

- **Language and canonical spelling**: Write `README.md`, `CONTRIBUTING.md`, all `AGENTS.md` files, `docs/`, `specs/`, diagram labels/legends, code comments, and runnable example output in English. Standardize capitalization on **`Tushare`** / **`Tushare Pro`** and **`A-share`** / **`A-shares`**.
- **Cohesive, authoritative technical prose**: Open sections with purpose and architectural mental model so readers understand how components fit together before encountering edge cases. Connect related clauses with clear logical transitions instead of choppy `SVO. SVO.` sentence fragments. Use precise quantitative finance and systems terminology—such as *point-in-time (PIT)*, *settlement lag*, *FIFO lot ledger*, *cross-sectional winsorization*, and *IC / Rank IC*—defining project-specific conventions clearly on first use rather than substituting vague circumlocutions.
- **Audience stratification (document invariants once at their canonical scope)**: State architectural boundaries and model limitations once where they govern usage or implementation, linking to the canonical contract instead of repeating boilerplate caveats across every page:
  - `README.md`: High-level capabilities, runnable quickstarts, and entry-point links.
  - `docs/reference/`: Authoritative API contracts, trading-rule semantics, wire schemas, and edge-case behavior.
  - `specs/`: Normative RFC design records, architectural trade-offs, and concise verification evidence—free of conversational session logs or attribution chatter (such as *"Owner: Codex"* or *"The user requested"*).
- **Domain precision**:
  - Call a backtest's market and feature input bundle a **data snapshot**, specifying its as-of cutoff when explaining what a strategy can observe at decision time.
  - When documenting execution sizing, treat order-quantity calculation (fixing whole-lot share counts from prior closing marks before the open) and cash reservation (setting aside notional plus worst-case fees and slippage from the buy budget) as distinct steps.
  - Name refactors, diagrams, and examples after the behavior they illustrate, reserving version numbers (`v1`, `v3`) for concrete releases or persisted schema and bundle formats.
  - Keep `README.md` examples runnable from the repository root, keep diagrams synchronized with current code (explicitly marking unimplemented interfaces as proposed), and support any performance change with reproducible before/after benchmarks recording inputs, hardware/OS environment, metrics, and both measurements.

## Development Workflow

Before editing code or contracts, read [`specs/INDEX.md`](specs/INDEX.md), the active specification, any milestone specification governing the subsystem, and the nearest nested `AGENTS.md`. Preserve unrelated working-tree changes.

For any change to runtime or public behavior, follow a strict red/green/refactor loop: start with a failing unit, integration, or property test, implement the minimal change to pass, refactor cleanly, and record the failing and passing verification evidence in the governing spec. Use the review scope defined in the active spec (completed M2–M6 specifications preserve the historical review scopes for those milestones).

### Verification Commands

Run the full Python and Rust checks from the repository root before handoff:

```bash
uv run ruff check python tests
uv run mypy --strict python tests
uv run pytest -q
cargo fmt --all --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace
```

<!-- develop-with-specs:start -->
## Maintaining specifications

At each significant milestone and before handoff, update the governing file under `specs/` so work can resume cleanly from the repository state alone. Record durable engineering state—task status, normative design decisions, changed module boundaries, red/green verification results, and remaining open items—as a concise technical specification without chat narration or session transcripts.
<!-- develop-with-specs:end -->
