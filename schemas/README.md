# Event-run schemas and daily snapshot protocol

Cross-language persistence contracts are maintained in a single canonical location under [`crates/tm-core/schemas/`](../crates/tm-core/schemas/): Rust embeds these JSON definitions at compile time, and the wheel build packages the identical files under `trademaster/schemas/` so Python and Rust validate against the exact same specifications.

| Contract | Specification scope |
| --- | --- |
| [Run artifacts](../crates/tm-core/schemas/run-artifacts-v1.json) | Column names, fixed-point and Arrow types, enum domains, deterministic sort keys (`run_id`, `event_seq`, and table-specific secondary keys), and relational invariants across all 11 event-run artifact tables—including orders, fills, positions, and NAV hash chains. |
| [Run manifest](../crates/tm-core/schemas/run-manifest-v1.json) | Content-addressed run and **data snapshot** identities, configuration digest, artifact paths, SHA-256 checksums, and row counts. |
| [Completed manifest fixture](../crates/tm-core/schemas/run-manifest-v1.fixture.json) | Golden manifest instance used by regression suites to verify byte-compatible serialization across Python and Rust. |

Running `tm-replay REQUEST.json` dispatches saved wire payloads for either simulation engine through the native Rust extension:

- **Event-runner wire and artifact contracts.** An event request bundles instrument specifications, fee settings, timestamped market events, and scheduled signals into a self-contained JSON payload; [wire types](../crates/tm-runner/src/lib.rs) and [wire tests](../crates/tm-runner/tests/wire.rs) govern request decoding and execution, while [Python schema tests](../tests/test_schema_golden.py) and [Rust schema tests](../crates/tm-core/tests/shared_schema.rs) enforce cross-language agreement on persisted v1 event artifacts. See the [execution reference](../docs/reference/execution.md) for the runtime contract.
- **Daily snapshot wire protocol.** A snapshot request references an immutable daily market JSON file and records fixed-point (`10^8`-scaled) daily equity, target-batch outcomes, dividend entitlements, and optional attempt, order, fill, and position tables; see the [snapshot replay reader](../crates/tm-runner/src/snapshot.rs), [wire record definitions](../crates/tm-engine/src/snapshot/wire.rs), [snapshot replay tests](../crates/tm-runner/tests/snapshot_replay.rs), and the [daily snapshot API reference](../docs/reference/snapshot-backtest.md).
