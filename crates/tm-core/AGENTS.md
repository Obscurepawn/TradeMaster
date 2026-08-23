# tm-core Guide

This crate owns stable identifiers, exact scalar types, immutable domain
records, statuses, and shared errors. It contains no provider, strategy,
execution, or ledger implementation and has no process-global state.

Checked constructors and typed `ArtifactRecord` adapters are part of the public
contract. Formal artifact writers use `typed_artifact_record_batch`; the generic
row adapter exists for boundary validation and must not replace domain checks.
