# Compact Market Rows and Streaming Snapshot Loading

**Status:** Implemented | **Date:** 2026-11-26 | **Scope:** Peak and resident memory of `Snapshot.open` and daily snapshot runs (`trademaster.backtest.snapshot`, `tm-python`, `tm-engine::snapshot`, `tm-runner`) | **Updates:** [Python and Rust Native Backtest Interface](20261105-python-rust-backtest-interface.md)

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

`PreparedMarket::new(data: SnapshotData)` takes ownership and reports the first failing row in file order with exactly the current checks and messages (price-limit marker consistency, row outside sampled sessions, unknown instrument, duplicate row, non-positive price; a non-positive price is kept as a flag bit because `0` encodes absence). One pass in file order finds the first row failing a per-row check and whether keys are already strictly increasing, as canonical snapshots are; an unsorted prefix is counting-sorted by session and sorted by `(instrument, input position)` within each session, which places a repeated key directly after its first occurrence, so the earliest duplicate is the smallest second position of an adjacent equal pair. This replaces the proposed session × instrument bitset, whose size grows with the calendar × universe product rather than with rows. Rows are then stored as per-session CSR columns (`offsets` plus instrument-sorted columns, 30 bytes per row); already sorted input is moved without a permutation, so arbitrary input row order remains valid at the cost of a 4-byte-per-row permutation only when needed. Lookups binary-search the session slice by instrument index; iteration walks the slice.

Engine code reads rows through a `Copy` `RowView` carrying the decoded scalars and a borrowed reason, from which `Opening` is built. Because `PreparedMarket` no longer borrows its input, `tm-python` drops the self-referential `IndexedMarket` (`self_cell`). `SnapshotSession` keeps closing marks in a dense `Vec<i64>` indexed by instrument (`0` = no mark, since marks stay positive through dividend adjustment) instead of rebuilding a `BTreeMap<String, i128>` every session; the shared `LotLedger` still values positions from a string-keyed map, which is now built per valuation from held instruments only, leaving `tm-engine/src/lib.rs` unchanged.

### Streaming, hashing loader (`tm-runner::snapshot::read_market`)

One loader serves `tm-runner` replay (`load_market`) and `tm-python`: a `BufReader` over a `HashingReader` feeds `serde_json::Deserializer::from_reader`, and the digest is finalized after the last byte. The deserializer must own the `BufReader`: std serves serde_json's single-byte reads straight from an owned `BufReader`'s buffer but not through `&mut BufReader`, which doubled load time (11.4 s versus 5.7 s on the Hong Kong market). The hasher and file are therefore borrowed by the reader, so after a parse error the bytes left in the file are hashed from the same file offset and `SHA-256 mismatch` is reported before any content error, preserving the current error priority. Errors are typed (`MarketFileError::{Read, Digest, Parse}`) with the previous messages.

serde_json's stream reader counts a byte it has only peeked at, so some errors (for example `unknown field`) name a column one past the in-memory parser's position. To keep error text identical, a correctly hashed file that fails to parse is read whole once more and parsed in memory, and that error is reported; only invalid files pay this cost. The `sha2` crate enables its `asm` feature on aarch64 (non-MSVC), where the portable implementation hashes 1.9 GB in 3.4 s versus 0.75 s; x86-64 already selects SHA extensions at run time.

`trademaster._native.Market.open(path, sha256)` replaces the `Market(content)` constructor, mapping a digest mismatch to `ValueError("market snapshot SHA-256 mismatch")`, file errors to the matching `OSError`, and parse errors to `ValueError("invalid market snapshot: ...")`. `snapshot.py` opens the file first (so a missing market raises the same `OSError` as before), translates the digest mismatch to `snapshot object hash mismatch: <file>`, and holds a content error until the features digest has been verified, keeping the order market digest, features digest, market content.

### Python grids (`trademaster.backtest.snapshot`)

- The features Parquet file is read in batches of 262,144 rows of the required columns with dictionary-encoded identifiers. Timestamps map to sessions with `searchsorted` and instruments with a dictionary lookup, values are scattered directly into the adjusted grid in file order (so a repeated observation resolves to the last one in the file, as the stable sort did), and research (non-adjusted) features are collected in the same pass and then sorted as before. Rows are never sorted for the grid; for each error kind the earliest offender by `(null time, available_at, file row)` is tracked, so exception types, messages, and their priority (unknown or null timestamp, then unknown or null instrument, then outside the listing interval) are unchanged, and rows with a null `name` remain in neither set. The schema check uses the Parquet file's Arrow schema because dictionary reads change column types.
- `Market.price_grid` writes the `float64` close grid (NaN for missing, `close as f64 / 1e8`, the same IEEE operations as NumPy's `int64 / int`) and the listing-evidence bytes straight into a new `bytearray` and `bytes` with the GIL released, removing the `i64 → Vec<u8> → PyBytes → float64` copies. The `bytearray` keeps the close rows writable, as the `np.where` result was.

### Compatibility

Internal Rust APIs change: `PreparedMarket` loses its lifetime and gains `next_session`, `instrument_ids`, and `grid_cells`; `SnapshotData::rows` becomes `MarketRows`; `run_snapshot` takes ownership of the market; `Permission` derives `Copy` and `PartialEq`; and the private `_native.Market` constructor is replaced by `open`, whose `price_grid` returns a `float64` `bytearray`. The only edits to existing tests replace `&data` with `data` at `run_snapshot` and `PreparedMarket::new` call sites. Python and wire contracts are unchanged.

## Verification Plan

1. **Characterization first (red/green):** before refactoring, add tests that pin current behavior and pass on the baseline — an error corpus with exact messages (unknown field, byte-truncated file, duplicate row, row outside sessions, unknown instrument, non-positive price, invalid UTF-8, simultaneous hash and parse errors, feature-file errors), shuffled row order producing identical results, and streaming versus in-memory loading producing identical markets.
2. **Unchanged suites:** all Rust and Python tests pass unchanged, including pinned `snapshot_id` fixtures, `tm-runner` round-trip and SHA-mismatch replay tests, and the `docs/examples/*-reference.json` benchmarks.
3. **Differential check:** build the baseline commit in a separate worktree and compare `result.json` and error strings byte for byte against this change on seeded random snapshots (suspensions, gaps, cash dividends, price-limit markers, delistings) and on the Hong Kong snapshot above. The comparison harness is not kept in product code.
4. **Benchmarks:** record open time, peak physical footprint (`/usr/bin/time -l` "peak memory footprint"), and steady footprint before and after on the Hong Kong snapshot and a synthetic 15M-row snapshot, with hardware and OS.

## Tasks

- [x] Characterization tests and error corpus on the baseline
- [x] `MarketRows`, owned `PreparedMarket`, CSR lookups, `RowView`, dense marks; remove `self_cell`
- [x] Streaming hashing loader shared by `tm-runner` and `tm-python`; `sha2` `asm`
- [x] Streaming adjusted-close and research features; direct `float64` close grid
- [x] Differential check and benchmarks recorded below
- [x] Update `crates/tm-python/AGENTS.md`, `crates/tm-engine/AGENTS.md`, `crates/tm-runner/AGENTS.md`, `docs/reference/snapshot-backtest.md`, and the interface spec

## Open Questions

- Resolved: `from_reader` stays within the 2× open-time budget (Hong Kong `Snapshot.open` 6.4–6.5 s versus 5.6–6.8 s before) once the parser owns its `BufReader`; a chunked reader is not needed. The remaining parse cost is serde_json's byte-at-a-time stream reader (about 4.0 s of the 5.75 s market load is spent parsing into `IgnoredAny`).
- An invalid market whose digest matches is read whole a second time to reproduce in-memory error positions, so its error path peaks near the file size, as every open did before this change.
- `close_session` allocates a dense mark vector per session (instrument count × 8 bytes); reusing two buffers would save the allocation but was not needed for the measured workloads.

## Verification and Regression Record

**Red/green.** Characterization suites were committed first and passed on the baseline: [`crates/tm-engine/tests/snapshot_market.rs`](../crates/tm-engine/tests/snapshot_market.rs) (21 malformed-row cases and 5 malformed-text cases with exact serde and validation messages including line and column, error precedence within and across rows, 16 shuffles producing identical results and price grids, and the earliest file-order error under shuffles), [`crates/tm-runner/tests/market_loading.rs`](../crates/tm-runner/tests/market_loading.rs) (truncation, trailing data, invalid UTF-8, unknown fields, duplicate rows, empty and non-JSON files, each also under a mismatched digest; missing and directory paths; file replay equal to an in-memory run), and [`tests/test_snapshot_open.py`](../tests/test_snapshot_open.py) (market error types and texts, market digest before features digest before market content, `OSError` for a missing market, feature error types, values and priority including null times and instruments, schema mismatches, duplicate observations, and shuffled market rows). Switching to `from_reader` turned the unknown-field cases red (column 576 became 577, 935 became 936), which led to the in-memory error re-parse; new tests for `Market.open` and the `float64` grid failed before their implementation.

**Differential check** (harness kept outside the repository, baseline = the parent of this change built in its own environment):

| Input | Compared | Result |
|---|---|---|
| 200 seeded random snapshots (25–90 sessions, 3–11 instruments, listings and delistings, suspensions with carried marks, upper and lower price-limit markers, unknown permissions, missing rows and closes, corporate-action gaps, cash dividends, research features; both `enforce_price_limits` settings) | Canonical `result.json` of daily-callback, weekly-callback, and dated-target runs with initial lots (511 runs: 96 completed, 415 `insufficient_data`, 89 requests rejected before running; 9,027 fills; 144 with dividends, 121 with carried marks), `tm-replay` of each saved request, `closes`/`scores` reads, and 1,600 corrupted republications (duplicate, zero, unknown-instrument, next-session, marker, unknown-field, bad-permission, truncated, invalid UTF-8 and shuffled markets; unknown-time, unknown or null instrument and listing-boundary features) | Report files byte-identical; every replay reproduced its result |
| Hong Kong snapshot below, weekly cross-sectional momentum over all active instruments with `detail=True` | `result.json` (50,161,967 bytes, 43,038 fills, `insufficient_data` after the last valued close), `request.json`, SHA-256 of the raw and adjusted `float64` grids, `tm-replay` of the saved request | Byte-identical; both replays match |

**Benchmarks.** Apple M4 (10 cores), 16 GB, macOS 27.2, Python 3.12.16, release builds; each measurement is one process started cold on a warm page cache. Footprint is macOS physical footprint (`/usr/bin/time -l` "peak memory footprint" or `proc_pid_rusage` lifetime maximum); steady is the current footprint after `Snapshot.open` and `gc.collect()`.

| Workload | Metric | Before | After |
|---|---|---:|---:|
| Hong Kong daily snapshot, 7,706,886 rows (2,812 instruments × 4,160 sessions, 1.93 GB `market.json`, 22 MB features) | `Snapshot.open` time | 5.6–6.8 s | 6.4–6.5 s |
| | `Snapshot.open` peak footprint | 6.18–6.21 GB | 0.68 GB |
| | Steady footprint after open | 5.89–5.92 GB | 0.65 GB |
| | Open + weekly momentum run (`detail=True`): run time, process peak | 12.7 s, 7.26 GB | 9.4 s, 1.73 GB |
| | `tm-replay` of the saved request: time, peak | 18.0 s, 4.23 GB | 12.8 s, 0.43 GB |
| | Rust market load alone (`read_market` + `PreparedMarket::new`): time, process peak | — | 5.75 s + 0.04 s, 0.18 GB |
| Synthetic daily snapshot, 15,931,862 rows (5,000 instruments × 4,000 sessions, seeded random walk with suspensions and price-limit markers, 3.74 GB `market.json`, 55 MB features) | `Snapshot.open` time | 14.1 s | 12.4 s |
| | `Snapshot.open` peak footprint | 10.07 GB | 1.10 GB |
| | Steady footprint after open | 7.27 GB | 1.10 GB |

The prepared market's columns take 30 bytes per row by construction (4-byte instrument index, two 8-byte prices, a 2-byte flag word, two 4-byte reason ids) plus per-session offsets; the remaining steady footprint is dominated by the two `float64` grids (8 bytes per instrument-session cell each) and the Python runtime.

**Suite totals.** 75 Rust workspace tests, 463 Python tests (4 skipped for absent live Tushare credentials or plan files), `cargo fmt --all --check`, `cargo clippy --locked --workspace --all-targets -- -D warnings`, `uv run ruff check python tests`, and `uv run mypy --strict python tests` pass.
