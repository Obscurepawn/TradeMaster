# Legacy Industry Fundamental Compatibility Guide

This package is the compatibility implementation path. New callers use
`trademaster.strategies.industry_fundamental`; Top1/Top5 are configuration values,
not package or type identities. It owns historical-universe assembly, semiannual
selection, target weights, and the concrete report runner. It does not own factor
formulas or accounting.

Fundamental formulas and transforms come only from managed definitions under
`trademaster.factors`. Every formal selection binds the complete candidate input
snapshot, factor definitions/materializations, strategy-definition hash and sparse
selection object. Selection is stable by score then instrument ID. The report must
distinguish targets from lot-constrained realized holdings and cash.

Long-horizon reports use weekly valuation observations with 52 periods per year. Formal strategy
runs resolve SW2021 membership by `in_date/out_date` at the signal date and fail closed on an
uncovered candidate. `static_latest_experiment` is a separate, explicitly non-PIT strategy identity;
never title or compare it as a formal historical-industry backtest.

The compatibility runner still uses continuous prices derived from Tushare adjustment factors.
Until Rust owns explicit corporate-action and cash-dividend events, its lot counts, minimum fees and
position accounting are a research approximation, not an exact A-share execution backtest. Every
report and summary must disclose that boundary.
