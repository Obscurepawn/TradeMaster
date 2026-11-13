# Test Guide

## Scope

`tests/` contains TradeMaster's Python unit, integration, contract, research, and replay suites exercising public Python APIs and the compiled `trademaster._native` Rust extension.

## Invariants

- **Behavior-focused assertions over real storage**: Assert on public interfaces and persisted records that callers depend on, giving each test a single primary reason to fail. Use temporary DuckDB databases, real Parquet files, and Arrow tables; reserve mocks or deterministic fakes strictly for external network calls, slow I/O, destructive actions, or nondeterministic inputs.
- **Explicit credential-gated skips**: When absent environment credentials (`TUSHARE_TOKEN` or `INDEXONE_API_KEY`) prevent a live provider test from executing, mark it as `skipped` and exclude skipped tests from reported live-network verification counts.

## Verification

```bash
uv run pytest -q
```
