# End-to-end runs

This module handles the native Python-to-Rust event interface and the run
bundle. It can convert precomputed target weights into whole-lot target
quantities before event execution. The daily snapshot demos send complete
target weights through `trademaster.backtest`. Rust calculates the share
quantities and cash reserves, processes fills and settlement, and records
positions, fees and net asset value.

The industry strategy's v3 event bundle records the exact request and result,
source and selection snapshots, factor definitions, the materialization
dependency graph and its Parquet files, report inputs and HTML, the strategy
summary and its Markdown report. The verifier rehashes every object, checks
parent factor records, reruns Rust and rebuilds the HTML. Stop on an
engine error or identity mismatch. Keep
credentials out of artifacts and logs.
