# Legacy industry fundamental strategy

Direct new callers to `trademaster.strategies.industry_fundamental`. Top1 and
Top5 are configuration settings within one package and type. This legacy package builds
the historical universe, makes semiannual selections, sets target weights and
runs reports. Use managed definitions from `trademaster.factors` for all
fundamental formulas and transforms. Rust handles accounting.

Each formal selection records the complete candidate input snapshot, factor
definitions and materializations, strategy-definition hash and sparse selection
object. Rank by score and break ties by instrument ID. In reports, distinguish
the target portfolio from actual holdings after lot rounding and show the
remaining cash.

For long-horizon reports, use weekly valuations and 52 periods per year.
Determine each candidate's SW2021 industry on the signal date from its
`in_date/out_date` interval. Stop a formal run if a candidate has no matching
membership. The `static_latest_experiment` variant has its own strategy identity
and uses the latest classification. Exclude it from comparisons of formal
historical-industry backtests and identify it clearly in report titles.

This event runner carries a missing daily bar forward at the last close and
records a suspension inferred from the missing bar. Distinguish that inference
from provider-confirmed trading status when documenting execution evidence.
Record the use of end-normalized adjusted prices in each report and summary.
These prices determine lot counts and minimum fees. The ledger has no separate
cash-dividend or split posting for this strategy.
