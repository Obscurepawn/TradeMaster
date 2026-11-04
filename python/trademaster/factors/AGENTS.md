# Factors

This module registers and versions factors, resolves dependencies, computes
factors deterministically, writes Parquet, indexes results in DuckDB, evaluates
factors and emits Arrow tables. Provider calls and account updates belong to
other layers.

Pass M1 data snapshots through `FactorContext`. Keep the exact input snapshot
provenance in the output and use the stable factor schema. Each row carries
the registered `factor_id/version` and an event time at or before the cutoff.

Resolve each dataset dependency to a canonical field. For a composite factor,
each factor dependency must be registered or declared as an external factor
by identity. Reject unresolved dependencies.

## Managed factors and the public library

Use `FactorDefinition` to identify a managed factor's code, parameters and
dependencies. For each materialization, record the source snapshot, exact
input table, scope, parent materializations and output hash. Keep the values
in Parquet and use `factors/catalog.duckdb` for discovery. Bump the version
whenever a definition changes. On every load, rehash the definitions,
manifests and Parquet files and reject mismatches.

Store public sources, collections and candidates as immutable, content-addressed
JSON in `factors/library/`. The top-level manifest lists every member and its
hash. Check DuckDB entries and dependency indexes against that manifest.
Candidates can be blocked, risk models or local variants. Verify the formula,
data and PIT rules, implementation identity and results against reference
cases. Register the factor and its definition in `ManagedFactorRegistry`.
Set the library candidate's `implementation_status` to `implemented` or
`implemented_variant` and link it to the verified definition.
Preserve the formula name used in the source report and document
any differences in the local implementation.

## Computation and evaluation

Compute separately for each instrument unless the spec calls for a
cross-sectional calculation. Break sorting ties by instrument ID. For
cross-sectional transforms, group by event time and select the eligible
universe before fitting winsorization or standardization statistics.

Keep forward returns in `evaluation.py` and out of factor and signal contexts.
Align labels to the next eligible execution event. Record the feature
materialization and the evidence for each label separately.
