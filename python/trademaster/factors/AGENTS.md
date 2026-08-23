# Factor Layer Guide

This module owns versioned factor registration, dependency DAGs, deterministic
execution, Parquet materialization, DuckDB discovery, evaluation, and canonical
Arrow factor outputs. It never fetches provider data directly and never mutates
portfolio or ledger state.

Every input must already be bound to an M1 snapshot through `FactorContext`.
Outputs preserve that exact snapshot provenance, use the frozen factor schema,
contain no future event time, and identify every row with the registered
`factor_id/version`.

Registry dependencies fail closed. Dataset dependencies must resolve to a canonical
dataset field. Composite factor dependencies must refer to a registered factor or an
explicitly declared external factor identity.

Formal managed factors use `FactorDefinition` and bind code, parameters, dependencies,
source snapshot, exact input table, scope, parent materializations, and output hash.
Parquet is authoritative; `factors/catalog.duckdb` is only discovery metadata. A
definition may not change under the same factor ID/version: bump the version instead.
Loading always re-hashes definitions, manifests, and Parquet and fails closed.

Public libraries use a separate governance boundary. Source, collection, and
candidate records are immutable content-addressed JSON under `factors/library/`;
the top-level JSON manifest binds the complete member set and every object hash;
DuckDB rows and dependency indexes are discovery-only and must be checked back
against that manifest. A candidate may be blocked, a risk model,
or a local variant. Do not place it in `ManagedFactorRegistry` until formula
semantics, data/PIT policy, implementation identity, and golden cases justify its
`implemented` status. Preserve report formula names and local semantic differences.

Forward returns belong only to `evaluation.py`. They must never be passed to a factor
or signal context. Evaluation alignment starts at the next eligible execution event,
and its artifacts bind the feature materialization separately from label evidence.

Factor implementations operate independently per instrument unless their spec
explicitly declares cross-sectional semantics. Stable sorting must always use an
instrument identifier tie-break; never rely on provider or hash iteration order.
Cross-sectional transforms isolate each event time before winsorization or
standardization and determine the eligible universe before fitting distribution
statistics.
