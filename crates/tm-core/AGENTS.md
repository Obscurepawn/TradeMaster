# tm-core Guide

This crate defines stable identifiers, exact scalar types, immutable domain
records, statuses, and shared errors. Provider, strategy, execution, and
ledger implementations belong in other crates or modules. This crate holds
no process-global state.

The public API includes checked constructors and typed `ArtifactRecord`
adapters. Writers use `typed_artifact_record_batch` to save run records.
The generic row adapter checks incoming rows. Domain constructors still
validate those values before a writer can use them.
