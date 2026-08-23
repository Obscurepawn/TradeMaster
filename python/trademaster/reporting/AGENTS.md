# Reporting Layer Guide

Reporting consumes immutable, identity-bound run outputs. It never repairs missing
trades, forward-fills benchmarks silently, or mutates strategy results.

Metrics are pure functions with explicit annualization and alignment policies. Return
`None` for undefined ratios or unrecovered drawdowns; never emit NaN or infinity.

HTML reports bind run/snapshot/strategy/benchmark identities and embed Plotly JS for
offline reading. Charts must label units and distinguish strategy, benchmarks,
drawdowns, transaction costs, industry exposure, and market-cap exposure.
