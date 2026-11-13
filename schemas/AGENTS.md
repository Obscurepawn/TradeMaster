# Schema Guide

## Scope

This directory documents TradeMaster's shared event-run contracts and daily snapshot wire formats (see [the schema guide](README.md)). Canonical JSON schema files live in [`crates/tm-core/schemas/`](../crates/tm-core/schemas/AGENTS.md), which defines versioning, review, and cross-language test requirements.

## Invariants

- **Deterministic artifact sorting and enums**: Every one of the 11 event-run artifact tables carries `run_id`, a nonnegative `event_seq`, and intra-event tie-breaker keys (such as `fill_seq`, `lot_id`, or `instrument_id`). Emit rows in the exact sort order declared in `run-artifacts-v1.json` and enforced by `validate_artifact_table`, specify the same keys in explicit SQL `ORDER BY` clauses, and restrict enum columns to contract-declared values.
- **Snapshot wire compatibility**: When modifying the daily snapshot request, market, or result formats, keep the Rust wire types (`tm-runner` and `tm-engine::snapshot::wire`) and Python SDK encoders synchronized.

## Verification

- Event contracts: `cargo test --locked -p tm-core --test shared_schema` and `uv run pytest tests/test_schema_golden.py`
- Snapshot and event replay: `cargo test --locked -p tm-runner` and `uv run pytest tests/test_replay.py`
