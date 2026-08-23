# Python Layer Guide

Python owns provider adapters, PIT queries, factors, signal generation, user
strategy adapters, CLI orchestration, and reporting. It does not implement an
independent cash/position engine.

Use Arrow tables at subsystem and Rust boundaries. Provider calls are isolated
behind typed protocols; tests use real DuckDB/Parquet and contract-faithful
provider fakes.

Managed factor values and evaluation tables are immutable Parquet. Their DuckDB
catalog is discovery-only. Forward-return labels require explicit entry/exit times,
alignment and label snapshot provenance and never enter signal generation.

Twenty-year full-A research is orchestrated under `trademaster.research`. Its
download plan owns dynamic historical-universe tasks and resumable provider evidence;
factor computation and evaluation remain out-of-core in DuckDB/Arrow. Do not route
the full-universe panel through a strategy's post-selection universe. The current CLI
still reuses the compatibility `StrategyDataCache` as the cache-first provider object
adapter; common protocols already live under `trademaster.data`, and new research
contracts must not be added to the legacy strategy package.

Full-A completed evidence binds task/evidence/Parquet request identity and is handed to
builders through a typed immutable snapshot. Financial date-only announcements become
available on the next eligible session; unknown-time revisions cannot backfill history.
