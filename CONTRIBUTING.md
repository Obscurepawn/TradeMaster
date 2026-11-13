# Contributing to TradeMaster

Thank you for your interest in contributing to TradeMaster. The project pairs an ergonomic Python research and strategy SDK with a deterministic Rust execution and accounting workspace so that quantitative studies remain reproducible from raw market ingestion through ledger valuation and reporting.

This guide covers setting up a local development environment, navigating the repository boundaries, preserving core domain invariants, and running the verification suite before opening a pull request.

## Prerequisites and Environment Setup

TradeMaster builds a native PyO3 extension (`trademaster._native`) directly into the Python environment via Maturin. Before starting, ensure you have the following installed:

- **Python 3.12 or later**
- **[uv](https://docs.astral.sh/uv/)** for Python dependency resolution, virtual environments, and wheel builds
- **Rust 1.97 or later** with `rustfmt` and `clippy` (`rustup component add rustfmt clippy`)

From the repository root, install the locked environment and compile the Rust extension in editable development mode:

```bash
uv sync --locked --extra dev
```

Both `uv.lock` and `Cargo.lock` are committed to guarantee reproducible builds across machines and CI. Do not update lockfiles in unrelated pull requests; when dependency changes are intentional, explain why in your pull request description.

Optional live-provider tests read credentials strictly from environment variables (`TUSHARE_TOKEN` for Tushare Pro and `INDEXONE_API_KEY` for IndexOne). You do not need either key to run the offline unit, integration, or replay test suites—network-gated tests skip automatically when credentials are absent.

## Repository Architecture

Python handles data acquisition, point-in-time queries, factor computation, strategy callbacks, and reporting, while Rust enforces market trading rules and maintains exact cash and position lots.

| Path | Responsibility |
|---|---|
| [`python/trademaster/backtest`](python/trademaster/backtest) | Daily portfolio **data snapshot** builder/loader, `on_close(day)` strategy protocol, `Policy` configuration, and `BacktestResult` inspection. |
| [`python/trademaster/data`](python/trademaster/data), [`research`](python/trademaster/research) | Typed provider adapters (Tushare Pro and IndexOne), DuckDB coverage catalogs, Parquet object storage, and resumable full-market research panels. |
| [`python/trademaster/factors`](python/trademaster/factors), [`signals`](python/trademaster/signals), [`strategies`](python/trademaster/strategies) | Versioned factor definitions, dependency graphs, public factor candidate catalog, Arrow signal generation, and reference strategies. |
| [`python/trademaster/e2e`](python/trademaster/e2e), [`reporting`](python/trademaster/reporting) | Event-runner orchestration, v3 provenance bundle verification, performance analytics, and standalone offline Plotly HTML reports. |
| [`crates/tm-core`](crates/tm-core) | Exact `Fixed8` (`10^-8`) and integer `Quantity` types, immutable domain records, embedded JSON schemas, and 11-table Parquet artifact validation. Depends on no higher-level crate. |
| [`crates/tm-engine`](crates/tm-engine) | Pre-open order sizing, cash reservation, venue validation (T+0/T+1 settlement, price limits, suspensions, lot rounding), FIFO lot ledger, and cash-dividend accounting. |
| [`crates/tm-runner`](crates/tm-runner) | Saved-request decoding and deterministic JSON replay across daily snapshot and event-runner schemas (`tm-replay`). |
| [`crates/tm-python`](crates/tm-python) | In-process PyO3 (`abi3-py312`) bindings exposing indexed markets, scheduled close callbacks, and request replay to Python. |
| [`tests/`](tests) | Python unit, DuckDB/Parquet integration, schema golden, and end-to-end replay suites (complemented by Rust integration tests under `crates/*/tests`). |
| [`docs/`](docs/README.md), [`specs/`](specs/INDEX.md), [`schemas/`](schemas/README.md) | User and programmer documentation, milestone design specifications, and shared wire/artifact schema guides. |

For file-by-file navigation and CLI flags, consult the [repository map](docs/reference/repository.md), [architecture overview](docs/architecture/overview.md), and [command reference](docs/reference/commands.md).

## Core Engineering Invariants

Every change to data handling, factor evaluation, execution, or reporting must uphold these invariants:

1. **Exact ledger arithmetic vs. floating-point research**: Factor scores and cross-sectional rankings may use floating-point math, whereas every monetary amount, price, fee rate, portfolio weight, and share quantity entering the execution engine or ledger must use exact representations (`Decimal` in Python validated to at most eight decimal places, `Fixed8` scaled by `10^8` and integer `Quantity` in Rust). Ledger postings are atomic—if any step fails, the entire state transition rolls back.
2. **Point-in-time cutoffs and execution timing**: Backtests consume an explicit **data snapshot** whose manifest records per-session close times and decision `as-of` cutoffs. Features or financial statements published after a session's cutoff cannot enter that decision, and any signal that observes a closing price can execute no earlier than the next eligible market open.
3. **Explicit trading evidence (never assume tradability)**: When preparing a data snapshot, source OHLC bars alone do not prove an instrument could trade at the open. Preserve missing trading-status evidence as `"unknown"` so the engine halts with `insufficient_data` rather than fabricating fills across undocumented halts or corporate-action gaps.
4. **Separate order sizing and cash reservation**: In the daily snapshot engine, setting final share quantities (computed from prior closing marks adjusted for ex-date cash dividends and rounded down to whole lots) and reserving cash (setting aside the uninvested cash weight plus the capped buy budget and estimated fees) are distinct pre-open steps completed before opening prices and permissions are evaluated. Target-weight portfolios process sells before buys in deterministic instrument-ID order so changing signal identifiers never alters capital allocation.
5. **Immutable artifacts and content-addressed lineage**: Store provider extracts, factor materializations, and event-run tables in immutable Parquet files bound by SHA-256 manifests. Modifying an input dataset, factor implementation, or parameter set requires publishing a new manifest or incrementing the factor version.
6. **Secret hygiene and clean workspaces**: Never hardcode, log, or commit API tokens or `.env` files. Keep machine-local caches (`data/`), run outputs (`artifacts/`), and local reference checkouts (`research/`, `docs/research/`) untracked as configured in [`.gitignore`](.gitignore).

## Workflow, Testing, and Specifications

- **Check specifications first**: Before changing public APIs, trading rules, or storage formats, review [`specs/INDEX.md`](specs/INDEX.md) and the specification governing that subsystem. Significant architectural or behavioral changes should record their design decisions, verification output, and scope in the relevant spec file.
- **Start behavioral changes with a failing test**: Write a focused unit, property, or integration test demonstrating the bug or new behavior before modifying implementation code, make it pass, and refactor cleanly.
- **Test real storage over mocks**: Prefer temporary DuckDB databases, real Parquet files, Arrow tables, and typed fake providers implementing the data protocols. Reserve mocks for external network calls or deliberately injected I/O failures.
- **Reproducible performance claims**: When optimizing hot paths, measure the same representative workload before and after your change and record the inputs, hardware/OS environment, metrics, and both timings so another contributor can reproduce the comparison.
- **Documentation style**: Write documentation, diagrams, and code comments in clear, authoritative technical English using precise quantitative finance and systems terminology. Document invariants once at their canonical layer (`README.md` for capabilities and quickstarts, `docs/reference/` for exact API and trading-rule contracts, `specs/` for RFC rationale and verification), specify **data snapshot** `as-of` cutoffs and causal execution timing clearly, and standardize naming on **Tushare** / **Tushare Pro**, **IndexOne**, and **A-share** / **A-shares**.

### Using Coding Agents

If you develop with an AI coding agent, repository-wide and directory-scoped instructions live in [`AGENTS.md`](AGENTS.md), [`python/AGENTS.md`](python/AGENTS.md), [`crates/AGENTS.md`](crates/AGENTS.md), and [`tests/AGENTS.md`](tests/AGENTS.md). For strategy authoring, snapshot preparation, and report verification workflows, point your agent to the bundled [TradeMaster backtesting skill](.skills/trademaster-backtest/SKILL.md).

## Quality Gates

Run the full check suite from the repository root before submitting a pull request (matching [`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

```bash
# Synchronize locked dependencies and build the native extension
uv sync --locked --extra dev

# Python lint, strict static typing, and test suite
uv run ruff check python tests
uv run mypy --strict python tests
uv run pytest -q

# Rust formatting, workspace lints, and unit/integration tests
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace

# Verify wheel and sdist packaging (including embedded schemas)
uv build
```

## License

By contributing to TradeMaster, you agree that your contributions will be licensed under the [Apache License, Version 2.0](LICENSE).
