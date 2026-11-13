# Reporting Guide

## Scope

`trademaster.reporting` reads immutable backtest and evaluation outputs by run identity, computes pure performance and risk metrics, and renders self-contained offline HTML reports.

## Invariants

- **Immutable inputs and benchmark alignment**: Load persisted run artifacts strictly by run identity, count recorded trades directly from ledger outputs, and require benchmark observations covering the strategy's valuation dates.
- **Pure, finite metric functions**: Implement all return, volatility, turnover, and drawdown calculations as pure functions with explicit annualization and date-alignment rules. Return finite numeric values, using `None` for undefined ratios (such as zero-denominator Sharpe/Sortino) and for the recovery duration of an unrecovered drawdown.
- **Self-contained offline HTML**: Record the run, data snapshot, strategy, and benchmark IDs in every HTML report, embed Plotly JS inline so charts render without network access, label all axes and units explicitly, and visually distinguish the strategy curve, benchmarks, drawdowns, transaction costs, industry exposure, and market-cap exposure.

## Verification

```bash
uv run pytest tests/test_reporting_metrics.py tests/test_reporting_report.py
```
