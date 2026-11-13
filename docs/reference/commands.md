# Command reference

Run all CLI entry points and module commands from the TradeMaster repository root after provisioning the workspace with `uv sync --locked --extra dev`, which compiles the native Rust extension (`tm-python`) and installs the Python package alongside development tools. Installed console scripts are declared in [pyproject.toml](../../pyproject.toml), while Rust crate members and shared toolchain settings are configured in [Cargo.toml](../../Cargo.toml).

## Trade execution

| Invocation | Required inputs and produced artifacts |
|---|---|
| `uv run tm-replay docs/examples/round-trip.json` | Replays a saved event-runner request through the native Rust extension and streams canonical wire JSON to `stdout`. [Worked example](execution.md#replay-the-three-session-example) · [Command implementation](../../python/trademaster/replay.py) |
| `uv run tm-replay path/to/request.json` | Automatically dispatches to the daily snapshot or event engine based on the request schema; snapshot requests resolve their market JSON path relative to the directory containing `request.json`. [File reader](../../crates/tm-runner/src/snapshot.rs) · [Public API](snapshot-backtest.md) |
| `uv run python -m trademaster.e2e.long_history prepare` and `run` | `prepare` builds the 2011–2025 broad-ETF data snapshot once; `run` executes and benchmarks both the daily moving-average strategy and buy-and-hold baseline offline, accepting `--check docs/examples/broad-etf-reference.json` to verify exact ledger agreement. [ETF data and timing method](../examples/broad-etf.md) |
| `uv run python -m trademaster.e2e.demo_strategies --help` | Executes three benchmark strategies with `--engine events` (default, emitting v3 HTML report bundles) or `--engine snapshot` (sharing one verified data snapshot and writing replayable requests and results), taking `--data-root` for the local research cache, `--plan` for its SHA-256 plan ID, and `--output` for the destination directory. [Source](../../python/trademaster/e2e/demo_strategies.py) · [Run guide](../examples/tushare-strategies.md) |
| `uv run python -m trademaster.e2e.compare_rebalance --help` | Compares event-runner and daily-snapshot execution across identical Tushare strategy decisions—verifying saved event bundles, aligning trades and daily equity curves, and recording repeated native replay timings. [Results and methodology](../examples/rebalance-comparison.md) · [Source](../../python/trademaster/e2e/compare_rebalance.py) |
| `uv run python -m trademaster.strategies.industry_fundamental --help` | Runs the managed-factor industry-rotation strategy across configurable date ranges, initial cash, constituent caps, and point-in-time industry classification policies, printing paths to the output bundle manifest, HTML report, Markdown summary, and JSON metrics. [Usage and options](../../python/trademaster/strategies/industry_fundamental/README.md) · [Parser](../../python/trademaster/strategies/industry_fundamental_top5/__main__.py) |
| `uv run python -m trademaster.strategies.industry_fundamental_top5 --help` | Preserves the historical module entry point for the same strategy; see the [compatibility guide](../../python/trademaster/strategies/industry_fundamental_top5/README.md) for settings used in legacy ten-year runs. |

Standalone specification scripts exercise targeted execution behaviors and latency benchmarks directly:
- Run `uv run python specs/examples/blocked_rotation.py` ([source](../../specs/examples/blocked_rotation.py)) to trace the four-session limit-blocked rotation, printing daily cash and equity while writing replayable artifacts under `artifacts/blocked-rotation`.
- Run `uv run python specs/examples/profile_replay.py` ([source](../../specs/examples/profile_replay.py)) to benchmark native Rust event replay across 16 instruments over 64, 128, 256, and 512 trading sessions, printing median wall-clock seconds per horizon.

## IndexOne acquisition

`uv run tm-indexone --data-root data/indexone --request path/to/query.json` queries an allowlisted IndexOne core dataset or reopens its hash-verified local Parquet cache entry, printing a JSON summary with row count, Parquet path, `request_sha256`, and `content_sha256`. Append `--offline` to enforce cache-only reads without network access or `--refresh` to collect a new immutable observation; see the [request-file walkthrough and credential setup](indexone.md#download-a-small-price-sample).

## Factor definitions and values

`uv run tm-factor [--root PATH] COMMAND` manages versioned factor definitions, content-addressed materializations, evaluations, and the public candidate catalog (defaulting `--root` to `.trademaster/factors`). Versioned identifiers such as `fundamental.roe@1` address registered definitions, whereas materialization IDs address persisted Parquet outputs bound to a specific universe, date range, and input lineage. [Parser and dispatch](../../python/trademaster/factors/__main__.py) · [Storage and examples](../factor-management.md)

| Subcommand | Inputs and behavior |
|---|---|
| `list`, `describe ID@VERSION`, `graph ID@VERSION` | Enumerates built-in factor definitions, prints a single definition's parameters and metadata, or displays its topological DAG dependency order. |
| `materialize ID@VERSION` | Computes and persists factor values given `--input-parquet`, `--snapshot-manifest`, `--start`, `--end`, and `--as-of` (plus optional explicit parent materializations and coverage keys), returning the new materialization ID. |
| `verify MATERIALIZATION_ID` | Reloads a persisted materialization and validates its Parquet digest, schema, and lineage against the recorded definition and upstream evidence. |
| `evaluate MATERIALIZATION_ID` | Joins a verified materialization against causal forward-return labels, eligible entry timestamps, and holding horizons, persisting IC, quantile diagnostics, and the evaluation identity. |
| `library-sync`, `library-status` | Persists the built-in public A-share factor candidate catalog to disk and summarizes implementation and review readiness. |
| `library-list`, `library-describe CANDIDATE_ID` | Queries persisted public candidates by collection, category, review status, dependencies, or data availability, or inspects a single candidate's provenance record. |

## Full-A acquisition and research

`uv run tm-research-full-a --data-root PATH COMMAND` drives resumable full-market A-share data acquisition, panel construction, and out-of-core factor evaluation against a local research store (place global options such as `--data-root` before the subcommand). Running `plan` queries trading calendars and security listings using your Tushare environment token (`TUSHARE_TOKEN`) and outputs a `--plan-sha256` digest used by all subsequent steps. [Parser and dispatch](../../python/trademaster/research/__main__.py) · [Full research workflow](../full-a-factor-research.md)

| Subcommand | Inputs and behavior |
|---|---|
| `plan` | Freezes study date bounds, financial-statement lookback start, and benchmark indices into a content-addressed `DownloadPlanManifest`. |
| `download`, `status`, `verify` | Executes resumable task batches (`--max-tasks N` caps work per invocation; `--phase` targets a specific endpoint group), summarizes task counts by state, or verifies all recorded task outcomes and Parquet digests once acquisition completes. |
| `reconcile-universe` | Reconciles security master coverage against cached daily market observations when current exchange listings omit delisted historical tickers. |
| `materialize` | Constructs the dense `(instrument, session)` market panel, public factor scores, and causal forward-return labels for the requested observation frequency and horizons, writing a panel manifest under `--output-root`. |
| `materialize-fundamental` | Joins point-in-time financial statement revisions and industry classifications onto a target panel manifest, persisting fundamental factor values across covered instruments. |
| `evaluate` | Computes out-of-core universe coverage, IC, rank IC, and quantile diagnostics from a panel and factor manifest, publishing a self-contained report under `--report-root`. |
| `verify-report` | Validates a published research report and its metric tables against its provenance manifest. |

Append `--help` to any subcommand—for example, `uv run tm-research-full-a --data-root data/research/demo evaluate --help`—to inspect exact flag requirements locally without contacting a data provider.
