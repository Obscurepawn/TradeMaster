# Compact Market Rows and Streaming Snapshot Loading

**Status:** Proposed | **Date:** 2026-11-26 | **Scope:** Peak and resident memory of `Snapshot.open` and daily snapshot runs (`trademaster.backtest.snapshot`, `tm-python`, `tm-engine::snapshot`, `tm-runner`) | **Updates:** [Python and Rust Native Backtest Interface](20261105-python-rust-backtest-interface.md)

## Motivation and Scope

Opening a data snapshot currently materializes the same daily market several times over. Python reads the entire canonical `market.json` into one `bytes` object to verify its SHA-256 digest, Rust deserializes it into a `Vec<DailyRow>` whose rows own `String` session, instrument, evidence, and reason fields, `PreparedMarket` then indexes every session with a `BTreeMap<&str, &DailyRow>`, the close grid crosses the boundary through three intermediate copies, and the adjusted-close features are loaded as a full Arrow table that is filtered, sorted, and indexed before being scattered into a grid. Because the macOS allocator does not promptly return large freed blocks, these transient copies also inflate the steady physical footprint.

On a 7,706,886-row Hong Kong daily snapshot (2,812 instruments × 4,160 sessions, 1.93 GB `market.json`; Apple M4, 16 GB, macOS physical footprint) the open path measured:

| Stage | Footprint delta |
|---|---:|
| `market.json` read and hashed in Python | +1.84 GB |
| Rust parse into `SnapshotData` + `PreparedMarket` (~285 B/row: 230 B row structs, 55 B per-session index) | +2.10 GB |
| Close-grid export (`i64` → `Vec<u8>` → `PyBytes` → `float64`) | +0.29 GB |
| Adjusted-close features (load, filter, sort, `index_in`, scatter) | +1.5–1.8 GB |
| **Peak** | **≈5.8 GB** (~0.67 GB per million rows) |

At that rate a full A-share history (~15M rows, 5,854 × 4,102) needs ~12 GB and the full US listed universe (~30M rows, 15,279 × 4,251) ~25 GB per process, which rules out daily research on a 16 GB workstation and makes parallel parameter sweeps impossible. The run loop pays for the same layout: `close_session` copies every row's instrument ID into a fresh `BTreeMap<String, i128>` of marks each session (~0.6 s per run on the snapshot above).

This specification reduces the resident market to a compact columnar store, streams the market file through a hashing reader so its bytes are never held whole, and streams the adjusted-close features straight into the grid.

### Non-Goals

- No change to the published snapshot format, canonical JSON, manifest, `snapshot_id`, or `SnapshotWriter`; existing snapshots stay valid.
- No change to backtest semantics, results, or public Python APIs (`Snapshot.open`, `DecisionDay.closes`, `backtest`).
- No memory-mapped files: they would introduce the repository's first `unsafe` code and raise `SIGBUS` when a snapshot on an external or network volume disappears.
- Sparse per-listing grids, a vectorized history-window API, and changes to the per-instrument history cache are deferred; they change public behavior or API surface and are evaluated separately.

## Design

### Columnar market rows (`tm-engine::snapshot::rows`)

A new `MarketRows` type replaces `Vec<DailyRow>` inside `SnapshotData`. Its `Deserialize` implementation visits the `rows` sequence and deserializes each element as the existing `DailyRow` wire type, so `deny_unknown_fields`, enum validation, and serde error text (including line and column) are unchanged, and then immediately compresses the row:

- session and instrument strings are interned to `u32` ids (canonical JSON places `rows` before `sessions`, so ids are resolved against the calendar after parsing);
- `open` and `close` become `i64` columns with `0` meaning absent, which is unambiguous because non-positive prices are already rejected;
- both opening permissions, `suspended`, `corporate_action_gap`, both price-limit markers, and "evidence is non-empty" are packed into one flag word (only the non-emptiness of `evidence` is consumed by the engine);
- buy and sell reasons are interned to `u32` ids into a small string table.

`PreparedMarket::new(data: SnapshotData)` takes ownership and validates rows in file order with exactly the current checks and messages (price-limit marker consistency, row outside sampled sessions, unknown instrument, duplicate row using a session × instrument bitset, non-positive price). It then counting-sorts rows into per-session CSR storage (`offsets` plus instrument-sorted columns), so arbitrary input row order remains valid. Lookups binary-search the session slice by instrument index; iteration walks the slice.

Engine code reads rows through a `Copy` `RowView` carrying the decoded scalars and a borrowed reason, from which `Opening` is built. Because `PreparedMarket` no longer borrows its input, `tm-python` drops the self-referential `IndexedMarket` (`self_cell`). `close_session` keeps marks in a dense `Vec<i64>` indexed by instrument instead of rebuilding a `BTreeMap<String, i128>` every session. Expected resident size is ~33 B per row (measured 33.0 B/row and 0.23 GB parse peak in a prototype on the snapshot above).

### Streaming, hashing loader (`tm-runner::snapshot::load_market`)

One loader serves `tm-runner` replay and `tm-python`: `BufReader<HashingReader<File>>` feeds `serde_json::Deserializer::from_reader`, and the digest is finalized after the last byte. On a parse error the loader drains the remaining bytes first and reports `SHA-256 mismatch` when the digest is wrong, preserving the current error priority. The `sha2` crate is built with its `asm` feature (on aarch64 the portable implementation hashes 1.9 GB in 3.4 s versus 0.75 s). `trademaster._native.Market` gains `open(path, sha256)`; `snapshot.py` stops reading the market file into memory while keeping its market-then-features verification order.

### Python grids (`trademaster.backtest.snapshot`)

- The features Parquet file is read in batches of the required columns with dictionary-encoded identifiers. Timestamps map to sessions with `searchsorted` and instruments with a dictionary lookup, values are scattered directly into the adjusted grid, and research (non-adjusted) features are collected in the same pass. Rows are not sorted; only when a batch contains an invalid row is the earliest offender by `available_at` located, so exception types, messages, and their priority (unknown timestamp, then unknown instrument, then outside the listing interval) are unchanged. The schema check uses the Parquet file's Arrow schema because dictionary reads change column types. A prototype produced a bit-identical grid with a +0.15 GB instead of +1.78 GB transient and half the time, and matched 2,000 randomized error cases.
- `Market.price_grid` emits the `float64` close grid (NaN for missing) directly, removing the `i64 → Vec<u8> → PyBytes → float64` copies (~1.0 GB transient saved at 65M cells).

### Compatibility

Internal Rust APIs change: `PreparedMarket` loses its lifetime, `run_snapshot` takes ownership of the market, and the private `_native.Market` constructor is replaced by `open`. Python and wire contracts are unchanged.

## Verification Plan

1. **Characterization first (red/green):** before refactoring, add tests that pin current behavior and pass on the baseline — an error corpus with exact messages (unknown field, byte-truncated file, duplicate row, row outside sessions, unknown instrument, non-positive price, invalid UTF-8, simultaneous hash and parse errors, feature-file errors), shuffled row order producing identical results, and streaming versus in-memory loading producing identical markets.
2. **Unchanged suites:** all Rust and Python tests pass unchanged, including pinned `snapshot_id` fixtures, `tm-runner` round-trip and SHA-mismatch replay tests, and the `docs/examples/*-reference.json` benchmarks.
3. **Differential check:** build the baseline commit in a separate worktree and compare `result.json` and error strings byte for byte against this change on seeded random snapshots (suspensions, gaps, cash dividends, price-limit markers, delistings) and on the Hong Kong snapshot above. The comparison harness is not kept in product code.
4. **Benchmarks:** record open time, peak physical footprint (`/usr/bin/time -l` "peak memory footprint"), and steady footprint before and after on the Hong Kong snapshot and a synthetic 15M-row snapshot, with hardware and OS.

## Tasks

- [ ] Characterization tests and error corpus on the baseline
- [ ] `MarketRows`, owned `PreparedMarket`, CSR lookups, `RowView`, dense marks; remove `self_cell`
- [ ] Streaming hashing loader shared by `tm-runner` and `tm-python`; `sha2` `asm`
- [ ] Streaming adjusted-close and research features; direct `float64` close grid
- [ ] Differential check and benchmarks recorded below
- [ ] Update `crates/tm-python/AGENTS.md`, `crates/tm-engine/AGENTS.md`, `docs/reference/snapshot-backtest.md`, and the interface spec

## Open Questions

- `from_reader` parsing may be slower than `from_slice`; if the streaming loader exceeds today's open time by more than 2×, record the measurement and evaluate a chunked reader before reconsidering memory mapping.

## Verification and Regression Record

Pending.
