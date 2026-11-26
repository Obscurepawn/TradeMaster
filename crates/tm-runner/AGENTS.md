# tm-runner Guide

## Scope

`tm-runner` decodes saved canonical event and daily snapshot requests, verifies referenced market artifacts, drives `tm-engine`, and serializes deterministic JSON outputs for `tm-python` and `tm-replay`. Order sizing, validation, execution, fees, settlement, and ledger accounting belong exclusively in `tm-engine`.

## Invariants

- **Strict event-request decoding**: Validate exact field sets, canonical fixed-point scaled integers, UTC timestamps, unique identifiers, optional price-limit pairs, and trading calendars before constructing `tm-core` domain types or entering `EventLoop`.
- **Snapshot provenance and relative resolution**: Decode ISO-8601 session dates and exact integer amounts, resolve the market JSON path relative to the saved request file's directory, verify the market SHA-256 hash while streaming the file into the parser (`snapshot::read_market`, shared with `tm-python`; a digest mismatch takes priority over content errors, and content errors keep their in-memory parse text), and validate the snapshot manifest whenever the request carries a `snapshot_id`.
- **Portable, secret-free results**: Exclude credentials and machine-specific host paths from serialized run outputs so replays remain portable and byte-reproducible across environments.

## Verification

- Rust wire, market loading, and replay suites ([`tests/wire.rs`](tests/wire.rs), [`tests/market_loading.rs`](tests/market_loading.rs), [`tests/snapshot_replay.rs`](tests/snapshot_replay.rs)): `cargo test --locked -p tm-runner`
- End-to-end CLI and Python extension replay ([`tests/test_replay.py`](../../tests/test_replay.py)): `uv run pytest tests/test_replay.py`
