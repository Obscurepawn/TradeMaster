# Industry fundamental strategy

`trademaster.strategies.industry_fundamental` provides the public Python API and CLI entry point for TradeMaster's semiannual CSI 300 fundamental strategy. Rebalancing each May and November, the strategy scores point-in-time (PIT) CSI 300 constituents across nine value, quality, growth, and safety factors, selects top-ranked stocks within eligible SW2021 industries (up to five per industry by default at equal weight), and executes target-weight signals through the Rust event runner into a self-verifying `v3` audit bundle.

```mermaid
flowchart LR
    A["Candidates on the signal date"] --> B["Nine fundamental factors"]
    B --> C["Rank industries and stocks"]
    C --> D["Equal-weight targets"]
    D --> E["Rust fills and holdings"]
```

## Choose the portfolio

| Portfolio | CLI options |
| --- | --- |
| Up to five stocks per eligible industry (default) | `--top-per-industry 5` |
| One stock per eligible industry | `--top-per-industry 1` |
| One stock in each of the ten highest-scoring industries | `--top-industries-by-mean-score 10 --top-per-industry 1` |

Within each SW2021 group, stocks are ranked by `fundamental.composite.industry_relative`; when `--top-industries-by-mean-score` is set, industries themselves are ranked first by their members' mean `fundamental.composite.global` score (halting selection if fewer than the requested ten eligible industries exist). Ties at either level break deterministically by ascending instrument or industry ID.

## Try the selection on a small portfolio

From the repository root, run the offline unit fixture covering nine sample stocks across two industries:

```bash
uv run --locked --extra dev pytest -q \
  tests/test_industry_fundamental_top5.py::test_selects_top_five_per_industry_with_stable_ties_and_exact_equal_weights
```

The test prints `1 passed`, verifying deterministic tie-breaking and equal `12.5%` target weights across eight selected stocks (five from the first industry and all three eligible names from the second). [Fixture and expected selections](../../../../tests/test_industry_fundamental_top5.py)

## Check coverage for the ten-year Top1 run

The default historical window spans **2016-08-12 through 2026-08-11** starting from **CNY 500,000** in cash, enforcing verified PIT SW2021 industry membership as of each signal date. Export `TUSHARE_TOKEN` in your shell (used to initialize the provider client alongside cached Parquet evidence) and run the Top1 check from the repository root:

```bash
uv run --locked python -m trademaster.strategies.industry_fundamental \
  --data-root data/strategies/industry-fundamental-top5-10y \
  --output-root artifacts/strategies/industry-fundamental-managed \
  --top-per-industry 1
```

Against the audited project cache, this command halts intentionally before stock selection or Rust execution with `historical industry interval is unavailable for 000009.SZ at 2016-11-07`—because that constituent's earliest recorded SW2021 membership interval begins on `2021-07-30`. Completing a formal PIT backtest requires uninterrupted `in_date/out_date` intervals for every CSI 300 candidate across all rebalance dates. [Historical coverage audit](../../../../specs/20260823-m9-full-a-20y-factor-evaluation-quality.md)

## Read a completed run

When PIT inputs are complete, the CLI prints JSON pointing to the standalone HTML `report`, `markdown_report`, and `summary` (recording final cash weight, rejected orders, and any residual holdings outside the final target). Within each rebalance batch, the event runner executes sells before buys in deterministic instrument-ID order, and the `bundle_manifest` cryptographically links candidate data snapshots, all 11 factor definitions and rebalance materializations, sparse stock selections, the Rust request/result, and rendered tear sheets.

Weekly portfolio valuations are annualized at 52 periods per year. Note that this legacy event workflow carries forward the prior close as a suspended mark on missing bars and sizes orders, share counts, and fees on `adj_factor`-adjusted prices rather than raw market quotes plus explicit cash-dividend postings.

[Factor definitions and lineage](../../../../docs/factor-management.md) · [Historical data and accounting limits](../industry_fundamental_top5/README.md#historical-data-and-accounting) · [Public Python API](__init__.py)
