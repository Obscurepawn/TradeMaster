# Schema Guide

This directory links to the event-run contracts. Edit them in
[`crates/tm-core/schemas/`](../crates/tm-core/schemas/AGENTS.md), which
lists the versioning, review and test requirements.

The 11 event-run artifact tables include `run_id` and a nonnegative
`event_seq`. Additional sort keys identify items within an event, such as
`fill_seq`, `lot_id` or `instrument_id`. Write rows in the order declared
in `run-artifacts-v1.json` and checked by `validate_artifact_table`. Use
the same keys in an explicit SQL `ORDER BY`. Use the enum values listed in
the contract.

For changes to the daily snapshot request or result, check the Rust wire
types and run the snapshot CLI tests linked in [the schema guide](README.md).
