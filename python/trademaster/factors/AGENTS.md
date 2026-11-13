# Factors Guide

## Scope

`trademaster.factors` registers and versions factor definitions, resolves dataset and upstream factor dependencies, computes deterministic factor values into Parquet, indexes materializations in DuckDB (`factors/catalog.duckdb`), curates the public factor candidate library (`factors/library/`), and runs cross-sectional factor evaluation (`tm-factor`). Provider network calls and portfolio accounting belong to other layers.

## Invariants

- **Snapshot lineage and dependency resolution**: Supply point-in-time data snapshots via `FactorContext`, emit rows conforming to the canonical factor schema with `factor_id@version` and `event_time <= cutoff`, and preserve exact input snapshot provenance. Map dataset dependencies to canonical schema fields, require composite factor dependencies to be registered in `ManagedFactorRegistry` or explicitly declared as external factors by identity, and reject unresolved dependencies.
- **Managed factor definitions and hash verification**: `FactorDefinition` binds code, parameters, and dependencies; bump the version whenever any component changes. Every materialization records its source data snapshot, input table digest, scope, parent factor materializations, and output Parquet hash. Rehash definitions, manifests, and Parquet files on every load and fail immediately on any mismatch.
- **Public factor candidate catalog (`factors/library/`)**: Store public sources, collections, and candidates as immutable, content-addressed JSON tracked by a top-level hash manifest, validating DuckDB catalog rows and dependency indexes against that manifest. Record whether each candidate is computable, blocked, a risk model, or a local variant (`implemented` vs. `implemented_variant`), verify formulas, PIT rules, and reference cases before linking a candidate to a `ManagedFactorRegistry` definition, preserve the source report's formula name, and document any local variant deviations.
- **Deterministic computation and label isolation**: Compute time-series factors independently per instrument unless cross-sectional semantics are specified, breaking rank/sort ties by `instrument_id`. For cross-sectional transforms, group by `event_time` and filter to the eligible universe before fitting winsorization or standardization statistics. Confine forward-return calculations strictly to `evaluation.py` (never exposing forward returns inside factor or signal contexts), align labels to the next eligible execution event, and record feature materializations separately from label evidence.

## Verification

```bash
uv run pytest tests/test_factor_interfaces.py tests/test_factor_management.py \
  tests/test_factor_cli.py tests/test_factor_evaluation.py \
  tests/test_factor_evaluation_v2.py tests/test_public_factor_library.py \
  tests/test_public_executable_factors.py
```
