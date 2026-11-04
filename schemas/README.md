# Event-run schemas and daily snapshot protocol

Edit the shared JSON contracts in [`crates/tm-core/schemas/`](../crates/tm-core/schemas/). Rust embeds them at compile time. The wheel includes the same files under `trademaster/schemas/` in the Python wheel.

| Contract | What it defines |
| --- | --- |
| [Run artifacts](../crates/tm-core/schemas/run-artifacts-v1.json) | Fields, types, enum values, sort order and validation rules for the 11 event-run artifact tables, including orders, fills, positions and NAV. |
| [Run manifest](../crates/tm-core/schemas/run-manifest-v1.json) | Run and snapshot identities, configuration hash, artifact locations, checksums and row counts. |
| [Completed manifest fixture](../crates/tm-core/schemas/run-manifest-v1.fixture.json) | A concrete example used to compare the Python and Rust representations. |

The `tm-replay REQUEST.json` command runs saved event and snapshot requests through the native extension. An event request contains the scheduled signals and market events. Its [wire types](../crates/tm-runner/src/lib.rs) and [wire tests](../crates/tm-runner/tests/wire.rs) cover decoding and execution. The [Python schema tests](../tests/test_schema_golden.py) and [Rust schema tests](../crates/tm-core/tests/shared_schema.rs) check the persisted event contracts.

A snapshot request references a daily market file, while the result records daily equity, portfolio target outcomes and optional order details. See the [snapshot request](../crates/tm-runner/src/snapshot.rs), [market and result types](../crates/tm-engine/src/snapshot/wire.rs) and [snapshot replay tests](../crates/tm-runner/tests/snapshot_replay.rs).
