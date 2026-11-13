# Legacy Industry Fundamental Strategy Guide

## Scope

`trademaster.strategies.industry_fundamental_top5` hosts the legacy implementation modules retained for backward compatibility; direct all new callers and documentation to [`trademaster.strategies.industry_fundamental`](../industry_fundamental/AGENTS.md), where `Top1`, `Top5`, and top-industry variants are configuration options of one strategy type.

## Invariants

- **Factor delegation and selection provenance**: Compute all fundamental formulas and cross-sectional transforms via managed definitions in [`trademaster.factors`](../../factors/AGENTS.md), while Rust handles execution and accounting. Persist the complete candidate input snapshot, factor definitions and materializations, strategy-definition hash, and sparse selection artifact for every formal rebalance. Rank candidates by score with `instrument_id` tie-breaking, and distinguish target portfolio weights from post-lot-rounding holdings and residual cash in reports.
- **SW2021 historical membership and valuation cadence**: Use weekly valuation dates (52 periods/year) for long-horizon reports. Resolve each candidate's SW2021 industry on the signal date from its `[in_date, out_date]` interval and abort any formal run when a candidate lacks historical membership. Keep `static_latest_experiment` under a distinct strategy identity, label its use of latest industry classifications in report titles, and exclude it from formal PIT industry comparisons.
- **Missing-bar inference and adjusted-price disclosure**: When this event workflow carries a missing daily bar forward at the prior close and infers a suspension from bar absence, distinguish that inference from provider-confirmed trading status in reports and documentation. Explicitly disclose in every summary and report that end-normalized adjusted prices govern lot counts and minimum-commission thresholds, with no separate cash-dividend or split postings in the ledger.

## Verification

```bash
uv run pytest tests/test_industry_fundamental_top5.py \
  tests/test_fundamental_managed_factors.py \
  tests/test_fundamental_strategy_data_cache.py
```
