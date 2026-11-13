# Packaged Contract Guide

## Scope

This directory holds the canonical event-run JSON contracts (`run-artifacts-v1.json`, `run-manifest-v1.json`, and golden fixtures) embedded into `tm-core` at compile time and copied into the Python wheel under `trademaster/schemas/` by [`crates/tm-python/build.rs`](../../tm-python/build.rs).

## Invariants

- **Explicit contract versioning**: Treat any change to a field name, data type, unit, nullability, enum set, sort key, or semantic constraint as a versioned schema change.
- **Coordinated migration and review**: Choose an explicit contract version, update the golden fixtures and migration notes in [`schemas/README.md`](../../../schemas/README.md), and obtain an independent interface review before merging.

## Verification

Run both cross-language contract suites from the repository root:

- [Rust shared schema tests](../tests/shared_schema.rs): `cargo test --locked -p tm-core --test shared_schema`
- [Python golden schema tests](../../../tests/test_schema_golden.py): `uv run pytest tests/test_schema_golden.py`
