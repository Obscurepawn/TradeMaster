# TradeMaster Agent Guide

## System mission

TradeMaster is a reproducible A-share research and backtesting system. Python
owns data ingestion, point-in-time research, factors, signals, and reporting.
Rust owns the authoritative event loop, market rules, execution, and ledger.

The golden path is:

```text
Tushare -> Parquet -> DuckDB/PIT snapshot
         -> resumable full-A research plan/dense session panel
         -> managed factor definitions/DAG -> factor Parquet/evaluation
         -> Python selections/signals -> Arrow -> Rust event runtime
         -> immutable bundle v3 -> report
```

## Global invariants

- Parquet is authoritative; DuckDB is a coverage catalog and query layer.
- Formal runs bind an immutable snapshot manifest and fail closed on missing
  required data, schema drift, or insufficient provider permissions.
- A close-derived signal cannot execute before the next eligible market event.
- Order intent, accepted order, fill, rejection, expiry, ledger posting, and
  snapshots are distinct contracts.
- Money, prices, rates, and quantities crossing the ledger boundary use exact
  representations. Floating-point factor scores never mutate the ledger.
- Factor ID/version binds code, parameters and dependencies. Formal materializations
  bind exact source snapshots, inputs, parent factor objects and output hashes.
- Full-A report identity binds the exact market/factor input manifests and evaluation
  config. Overlapping forward labels cannot be compounded as independent PnL, and
  factor quantile returns are not a substitute for Rust market-rule replay.
- Public factor candidates bind source release, license boundary, dependencies,
  constructibility and review status. Catalog membership never implies that a
  candidate is executable; only a linked managed definition may materialize values.
- Target-weight execution order is sell-first and instrument-stable; signal IDs cannot
  change the economic allocation of an otherwise identical target portfolio.
- Secrets are read from environment variables and never written to artifacts.
- `research/` and `docs/research/` are read-only evidence, not runtime code.

## Development workflow

Read `specs/INDEX.md` and the active spec before changing code. Behavior-bearing
work follows red-green-refactor. Record concise Red and Green evidence in the
spec. For M2-M5, run milestone gates and one bounded fresh-context review focused
on public contracts, the golden path, and obvious correctness defects; do not
repeat adversarial review cycles indefinitely. After M6 E2E succeeds, run one
high-intensity global review across the complete data-to-report chain.

Preserve unrelated working-tree changes. Do not edit candidate repositories
under `research/`.

<!-- develop-with-specs:start -->
## Durable specifications

For work represented under `specs/`, read `specs/INDEX.md` and the relevant
spec before editing. Keep its task status, decisions, current state, changed
files, verification evidence, and next step current at meaningful transitions
and before handoff. Do not rely on chat as the only project memory.
<!-- develop-with-specs:end -->
