# Strategies

Strategies select instruments and produce portfolio targets. Rust handles
cash, fills, positions, fees and settlement.

The industry strategy uses registered factors and dated signals. Keep managed
formulas, transforms, factor versions and evaluation in `trademaster.factors`.
Its event-run bundle records the configuration hash, candidate, factor and
selection identities, request, result, summary and reports.

The daily snapshot demos read prices at the decision cutoff and return
complete target weights. Save the snapshot, portable request, Rust result
and strategy summary. For Tushare inputs, check the local cache first and
supply credentials through the environment.
