# Python Package Guide

## Scope

`python/trademaster` implements provider adapters, point-in-time (PIT) storage and snapshot publication ([`data`](trademaster/data/AGENTS.md)), full-universe research orchestration ([`research`](trademaster/research/AGENTS.md)), managed factor definitions and evaluation ([`factors`](trademaster/factors/AGENTS.md)), runtime signal generation ([`signals`](trademaster/signals/AGENTS.md)), portfolio selection ([`strategies`](trademaster/strategies/AGENTS.md)), the daily snapshot SDK ([`backtest`](trademaster/backtest)), verifiable event bundles ([`e2e`](trademaster/e2e/AGENTS.md)), and offline analytics ([`reporting`](trademaster/reporting/AGENTS.md)). Cash, positions, order matching, and settlement live exclusively in Rust (`trademaster._native`).

## Invariants

- **Python-to-Rust boundary**: The daily snapshot SDK publishes feature Parquet files and a fixed-point `market.json`; `trademaster._native` opens the verified market in Rust and calls Python's `on_close(day)` at scheduled closes with closing account balances and intervening fills to collect target weights. Research, factor, and signal pipelines exchange Arrow tables without holding mutable account state.
- **Typed protocols and immutable storage**: Access external data providers strictly through typed `Protocol` interfaces so offline tests can substitute deterministic fakes backed by real DuckDB catalogs and Parquet files. Persist factor materializations and evaluation outputs in immutable Parquet indexed by DuckDB.
- **Point-in-time and label hygiene**: Keep forward-return labels isolated from factor and signal contexts; every label must record its entry and exit timestamps, alignment rule, and source data snapshot. Treat date-only financial announcements as observable no earlier than the next eligible session, and never allow revisions lacking a publication timestamp to retroactively alter earlier PIT views.
- **Module placement and task evidence**: Place shared provider/storage contracts in `trademaster.data` and twenty-year full-A panel workflows in `trademaster.research` (CLI strategy workflows may continue using `StrategyDataCache` for cache-first access). Assemble full-universe panels prior to any strategy filtering, verify that completed download tasks match both DuckDB evidence records and embedded Parquet request metadata, and pass downstream builders a typed, immutable snapshot of completed evidence.

## Verification

```bash
uv run ruff check python tests
uv run mypy --strict python tests
uv run pytest -q
```
