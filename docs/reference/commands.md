# Command reference

Run these commands from the repository root. `uv sync --locked --extra dev` installs the Python package and development tools. The installation builds the Rust extension. Python console scripts are registered in [pyproject.toml](../../pyproject.toml). The Rust workspace is defined in [Cargo.toml](../../Cargo.toml).

## Trade execution

| Invocation | Input and result |
|---|---|
| `uv run tm-replay docs/examples/round-trip.json` | Reads a saved event request and writes the original-format JSON result to stdout. [Worked example](execution.md#replay-the-three-session-example) · [Command implementation](../../python/trademaster/replay.py) |
| `uv run tm-replay path/to/request.json` | The request schema selects the snapshot or event engine. A snapshot request resolves its market file relative to the request directory. [File reader](../../crates/tm-runner/src/snapshot.rs) · [Public API](snapshot-backtest.md) |
| `uv run python -m trademaster.e2e.long_history prepare` and `run` | Prepare the 2011–2025 snapshot once, then time the moving-average strategy and buy-and-hold offline. Use `run --check docs/examples/broad-etf-reference.json` to verify both results. [ETF data and timing method](../examples/broad-etf.md) |
| `uv run python -m trademaster.e2e.demo_strategies --help` | Run with `--engine events` (default) for the three event-runner reports, or `--engine snapshot` for the same three strategies with pre-open sizing. `--data-root` selects a completed local research cache, `--plan` its SHA and `--output` the report directory. [Source](../../python/trademaster/e2e/demo_strategies.py) · [Run guide](../examples/tushare-strategies.md) |
| `uv run python -m trademaster.e2e.compare_rebalance --help` | Compares the event and snapshot runners on the same TuShare decisions. The command reads the original strategy bundles, verifies a replay against them, and writes results, daily equity comparisons and repeated timing measurements. [Results and methodology](../examples/rebalance-comparison.md) · [Source](../../python/trademaster/e2e/compare_rebalance.py) |
| `uv run python -m trademaster.strategies.industry_fundamental --help` | The module accepts dates, initial cash, portfolio selection settings and historical industry policy. A completed run prints paths to the bundle manifest, HTML, Markdown and summary. [Usage and options](../../python/trademaster/strategies/industry_fundamental/README.md) · [Parser](../../python/trademaster/strategies/industry_fundamental_top5/__main__.py) |
| `uv run python -m trademaster.strategies.industry_fundamental_top5 --help` | Historical command for the same strategy. The [compatibility guide](../../python/trademaster/strategies/industry_fundamental_top5/README.md) records the settings used in earlier runs. |

The [four-session blocked-sale example](../../specs/examples/blocked_rotation.py) runs with `uv run python specs/examples/blocked_rotation.py`. It prints daily cash and equity and writes the request and result under `artifacts/blocked-rotation`.

Run `uv run python specs/examples/profile_replay.py` to time the installed Rust extension on 16 instruments over 64, 128, 256 and 512 sessions. The script prints the median replay time in seconds for each history length. [Profiling script](../../specs/examples/profile_replay.py)

## Factor definitions and values

`uv run tm-factor [--root PATH] COMMAND` operates on the managed factor store. The default root is `.trademaster/factors`. An identity such as `fundamental.roe@1` selects a definition. A materialization ID selects saved values for a particular scope and input lineage. [Parser and dispatch](../../python/trademaster/factors/__main__.py) · [Storage and examples](../factor-management.md)

| Command | Input and result |
|---|---|
| `list`, `describe ID@VERSION`, `graph ID@VERSION` | List built-ins, inspect one definition, or print its dependency order. |
| `materialize ID@VERSION` | Supply `--input-parquet`, `--snapshot-manifest`, `--start`, `--end` and `--as-of`. Parent materializations and coverage keys can be supplied explicitly. Returns a materialization identity. |
| `verify MATERIALIZATION_ID` | Reopens the saved materialization and verifies the object against the recorded definition and evidence. |
| `evaluate MATERIALIZATION_ID` | Takes forward returns, a label snapshot, eligible entry times and holding horizons. Saves the factor diagnostics and their evaluation identity. |
| `library-sync`, `library-status` | Persist the built-in candidate catalog and summarize readiness. |
| `library-list`, `library-describe CANDIDATE_ID` | Filter persisted candidates by collection, category, status, dependencies or data availability, or inspect an individual candidate record. |

## Full-A acquisition and research

`uv run tm-research-full-a --data-root PATH COMMAND` operates on one local acquisition store. Put global options before the command. `plan` obtains trading calendars and security identities through the configured TuShare environment variable. After planning, use the returned `--plan-sha256` to select a plan. [Parser and dispatch](../../python/trademaster/research/__main__.py) · [Full research workflow](../full-a-factor-research.md)

| Command | Input and result |
|---|---|
| `plan` | Accepts a date range, statement start and benchmarks. Produces the acquisition plan. |
| `download`, `status`, `verify` | Resume pending tasks or inspect counts by status. `download --max-tasks N` limits work in this invocation. `--phase` restricts the endpoint group. Once the whole plan has finished, `verify` checks its recorded outcomes and completed objects. |
| `reconcile-universe` | Reconciles instrument identities against cached market evidence when the current security list omitted historical names. |
| `materialize` | Builds the market panel, factor values and labels for the requested observation frequency and return horizons. Writes a panel manifest under `--output-root`. |
| `materialize-fundamental` | Takes a plan and a specific panel manifest, then writes fundamental inputs and outputs for the evidenced scope. |
| `evaluate` | Computes diagnostics from the panel and selected factor manifest, producing an offline report under `--report-root`. |
| `verify-report` | Verifies a saved report against its manifest. |

For exact required flags, append `--help` to a command. For example, `uv run tm-research-full-a --data-root data/research/demo evaluate --help` prints the evaluation inputs without contacting a provider.
