# Strategies Guide

## Scope

`trademaster.strategies` implements reusable portfolio-selection logic that translates market history and factor scores into target allocations, while Rust (`trademaster._native`) owns order sizing, validation, fills, cash, FIFO lots, fees, and settlement.

## Invariants

- **Managed factor separation**: Delegate reusable fundamental and technical factor formulas, cross-sectional transforms, versioning, and factor diagnostics to [`trademaster.factors`](../factors/AGENTS.md). Event-run strategies (such as [`industry_fundamental`](industry_fundamental/AGENTS.md)) persist their configuration hash, candidate/factor/selection identities, canonical request, Rust result, summary, and reports in a verifiable run bundle.
- **Daily snapshot callback contract**: Daily snapshot strategies implement `on_close(day)` over [`trademaster.backtest`](../backtest), reading prices and features available at the decision cutoff alongside `day.account` and `day.fills`, and returning complete target portfolio weights (or `None` to retain active targets). Persist the data snapshot reference, portable `request.json`, Rust `result.json`, and strategy summary.
- **Cache-first data and environment credentials**: Resolve Tushare or IndexOne inputs from local caches first and read API credentials exclusively from environment variables.

## Verification

```bash
uv run pytest tests/test_demo_strategies.py tests/test_tushare_demo_strategies.py \
  tests/test_all_a_momentum.py tests/test_industry_fundamental_top5.py
```
