# Industry fundamental strategy · legacy module

Existing callers can keep importing `trademaster.strategies.industry_fundamental_top5`. For new scripts, use the [public strategy package](../industry_fundamental/README.md). Both names run the same code. Set `--top-per-industry` to change the number of stocks selected per industry.

| Legacy name | Public name in `trademaster.strategies.industry_fundamental` |
| --- | --- |
| `IndustryFundamentalTop5Config` | `IndustryFundamentalConfig` |
| `IndustryFundamentalTop5Strategy` | `IndustryFundamentalStrategy` |

## Reproduce a selection or check a historical run

The [offline selection example](../industry_fundamental/README.md#try-the-selection-on-a-small-portfolio) checks eight targets of 12.5% each. The [ten-year Top1 command](../industry_fundamental/README.md#check-coverage-for-the-ten-year-top1-run) checks historical membership in the project cache. For `000009.SZ` on 2016-11-07 the historical interval is missing, so the command stops before it can produce a report.

At each rebalance the runner saves the complete candidate snapshot, materializes nine atomic factors and two composites, then records the stocks it selects. `fundamental.composite.industry_relative@1` ranks stocks within an industry. `fundamental.composite.global@1` supplies the scores averaged to rank industries. A complete 20-rebalance run produces 220 factor materializations. The v3 bundle links these records to the Rust result and reports. With complete cached requests, the CLI reports `provider_calls` of zero. [Factor management guide](../../../../docs/factor-management.md)

## Historical data and accounting

| What affects the result | How this runner handles it |
| --- | --- |
| Publication timing | Selection uses original statements (`update_flag=0`) announced before the signal date. |
| Industry membership | The default looks up each candidate’s SW2021 industry on the signal date using `in_date` and `out_date` from `index_member_all`. A gap stops selection. The `static_latest_experiment` sensitivity test can use a later classification to fill that gap. It records a separate strategy ID. |
| Execution evidence | For a stock with a daily bar, the runner uses positive volume as its tradability test and requires the stock’s price limits on execution days. If the bar is missing and a prior close exists, it carries that price forward and records the stock as suspended. The evidence records the missing bar. If there is no prior close, it skips the stock for that day. |
| Corporate actions | Orders, lots and commissions use prices adjusted by `adj_factor` and normalized to the end of the period. The ledger has no separate postings for cash dividends or splits, so share counts, cash and minimum commissions are approximate. |
| Blocked exits | The strategy schedules exit attempts for up to 20 trading sessions and revisits remaining holdings on later rebalances. The report separates residual positions and cash from target weights. |

An August 2026 audit found that earlier Top1 and Top10-industry reports applied current industry membership to earlier dates. The project withdrew those performance conclusions and retained the reports to document the classification error. [Audit record](../../../../specs/20260823-m9-full-a-20y-factor-evaluation-quality.md)
