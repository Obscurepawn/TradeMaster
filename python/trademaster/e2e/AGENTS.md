# End-to-End Execution and Bundle Guide

## Scope

`trademaster.e2e` orchestrates saved event-run execution through `trademaster._native`, packages verifiable v3 event-run bundles, and provides data loaders and comparison harnesses bridging event runs and [`trademaster.backtest`](../backtest) daily snapshot runs.

## Invariants

- **Execution sizing split**: Event-bundle workflows may convert precomputed target weights into whole-lot target quantities ahead of `EventLoop` execution, whereas daily snapshot runs pass complete target weights into `trademaster.backtest` so Rust fixes share quantities from prior closes and reserves cash separately. In both paths, Rust owns fills, fees, settlement, positions, and NAV.
- **Complete v3 bundle lineage and verification**: A v3 event bundle (such as for the industry fundamental strategy) records the canonical request and result, source and selection data snapshots, managed factor definitions, the materialization dependency DAG and Parquet files, report inputs, offline HTML, strategy JSON summary, and Markdown report. `verify_e2e_bundle` rehashes every artifact, validates parent factor lineage, reruns Rust in process, and rebuilds the HTML—halting immediately on any engine error or digest/identity mismatch.
- **Secret hygiene**: Never include API tokens or credentials in saved bundles, manifests, or logs.

## Verification

```bash
uv run pytest tests/test_e2e_orchestrator.py tests/test_rebalance_comparison.py tests/test_long_history.py
```
