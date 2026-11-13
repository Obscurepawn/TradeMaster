# Industry Fundamental Strategy Guide

## Scope

`trademaster.strategies.industry_fundamental` is the public entry point for all SW2021 industry-fundamental strategy configurations (`Top1`, `Top5`, and top-N industry selection), orchestrating candidate scoring, industry selection, target-weight construction, event execution, and reporting. [`industry_fundamental_top5`](../industry_fundamental_top5/AGENTS.md) remains solely for backward compatibility.

## Invariants

- **Single unified strategy package**: Select portfolio breadth (`Top1`, `Top5`, or top industries) via configuration parameters rather than forking separate packages or strategy types.
- **Managed factor lineage and formal run bundles**: Compute all fundamental indicators and transforms through [`trademaster.factors`](../../factors/AGENTS.md). Every formal run must record the managed factor definitions, materializations, source data snapshots, strategy-definition hash, dated signals, Rust execution results, JSON summary, and Markdown/HTML reports.

## Verification

```bash
uv run pytest tests/test_industry_fundamental_top5.py
```
