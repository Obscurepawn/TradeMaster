# Python layer

Python handles provider adapters, point-in-time (PIT) queries, factors, signals,
strategy adapters, CLI commands and reports. Rust maintains cash and positions.
Research and factor code exchange Arrow tables. The daily snapshot SDK writes
feature Parquet and a fixed-point JSON market file. The native extension opens the verified market and runs the Rust ledger. At
each scheduled close the Python strategy reads the account and returns weights. Access
providers through typed protocols. Tests use real DuckDB and Parquet and fake
providers that implement the same protocols.

Store factor values and evaluation tables in immutable Parquet files. DuckDB
indexes them for discovery. Keep forward-return labels out of signals. Each
label records its entry and exit times, alignment rule and source snapshot.

Use `trademaster.research` for twenty-year studies of all A shares. Its download
plan tracks tasks for the historical universe and the evidence needed to resume
downloads. Compute and evaluate factors out of core with DuckDB and Arrow.
Build the panel for the full historical universe before any strategy selection.
The CLI still uses `StrategyDataCache` for cache-first provider access. Put new
shared data interfaces in `trademaster.data` and research interfaces in the
research package.

For each completed task, verify that its request matches the evidence record and
Parquet metadata. Give builders a typed, immutable snapshot of completed tasks.
Treat date-only financial announcements as available in the next eligible
session. A revision with no publication time cannot change earlier observations.
