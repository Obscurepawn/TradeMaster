# Schema Guide

This directory only documents schema routing. Canonical packaged contracts are
under `crates/tm-core/schemas/`; changing a field, type, unit, nullability, enum,
sort key, or semantic constraint requires a version decision, Python and Rust
golden updates, migration notes, and fresh-context review.

Every persisted run record carries `run_id` and a non-negative `event_seq`.
Records with multiple items per event add an item sequence; snapshot tables use
their stable business identifier. Writers must emit the canonical sort order
declared by `validate_artifact_table`, and readers must always `ORDER BY` it.
UTF-8 enum vocabularies in the golden file are closed sets, not free-form tags.
