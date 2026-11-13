# Industry fundamental strategy · legacy module

`trademaster.strategies.industry_fundamental_top5` is the backward-compatible import alias for the [public strategy package](../industry_fundamental/README.md)—executing the exact same semiannual CSI 300 factor-selection code while preserving legacy symbol names for existing callers. New scripts should import `trademaster.strategies.industry_fundamental` and pass `--top-per-industry` to configure industry concentration.

| Legacy name | Public name in `trademaster.strategies.industry_fundamental` |
| --- | --- |
| `IndustryFundamentalTop5Config` | `IndustryFundamentalConfig` |
| `IndustryFundamentalTop5Strategy` | `IndustryFundamentalStrategy` |

## Reproduce a selection or check a historical run

The [offline selection fixture](../industry_fundamental/README.md#try-the-selection-on-a-small-portfolio) verifies eight equal-weighted (`12.5%`) picks across two sample industries, while the [ten-year Top1 coverage probe](../industry_fundamental/README.md#check-coverage-for-the-ten-year-top1-run) audits point-in-time (PIT) SW2021 membership in the local cache—halting before report generation when `000009.SZ` lacks a valid membership interval on `2016-11-07`.

On each semiannual rebalance date, the runner snapshots the active CSI 300 candidate universe, materializes nine atomic fundamental factors plus two composites (`fundamental.composite.industry_relative@1` for intra-industry stock selection and `fundamental.composite.global@1` for cross-industry mean scoring), and logs selected target weights. Across a full 20-rebalance decade, the `v3` audit bundle links all 220 factor materializations directly to the Rust execution result and tear sheets, reporting `provider_calls` of zero whenever local Parquet evidence is complete. [Factor management guide](../../../../docs/factor-management.md)

## Historical data and accounting

Because this strategy predates the raw-price daily snapshot ledger, its legacy event-runner workflow applies several research approximations that affect trade sizing and historical interpretation:

| What affects the result | How this runner handles it |
| --- | --- |
| Publication timing | Selection observes only initial financial disclosures (`update_flag=0`) announced strictly before the signal date. |
| Industry membership | By default, each candidate's SW2021 group on the signal date must match an `in_date`/`out_date` interval in `index_member_all`, halting immediately on any gap; the diagnostic `static_latest_experiment` mode backfills gaps with later classifications under a separate strategy ID. |
| Execution evidence | When a daily bar exists, positive volume establishes tradability alongside daily price limits on execution dates; when a bar is missing after a prior close, the runner carries that close forward and marks the stock suspended (recording the missing bar in evidence), or skips the stock if no prior close exists. |
| Corporate actions | Orders, whole-lot quantities, and commissions are sized and booked on `adj_factor`-adjusted prices normalized to period end rather than raw quotes with separate cash-dividend or split postings—making share counts, cash balances, and minimum-commission thresholds approximate. |
| Blocked exits | Positions dropped from the target portfolio retry zero-weight exit orders across up to 20 trading sessions and are revisited on subsequent rebalances, with residual illiquid holdings separated from target weights in the tear sheet. |

During the August 2026 M9 audit, reviewers found that earlier Top1 and Top10-industry runs had backfilled missing historical SW2021 intervals with contemporary industry classifications; those performance conclusions were formally withdrawn while retaining the historical bundles as audit evidence. [Audit record](../../../../specs/20260823-m9-full-a-20y-factor-evaluation-quality.md)
