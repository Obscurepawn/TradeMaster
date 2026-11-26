# tm-python Guide

## Scope

`tm-python` builds the `trademaster._native` PyO3 extension (`cdylib`), connecting Python's daily snapshot SDK ([`src/lib.rs`](src/lib.rs)) and saved-request execution/replay ([`src/replay.rs`](src/replay.rs)) to `tm-core`, `tm-engine`, and `tm-runner` in process. Keep all sizing, validation, matching, fee calculation, and ledger logic inside the underlying Rust crates rather than in binding glue.

## Invariants

- **GIL release and callback error propagation**: Detach the GIL (`py.detach(...)`) across market file streaming and parsing, `PreparedMarket` indexing, price-grid filling, session advancement, `run_event`, and `replay_request`. Reattach (`Python::attach(...)`) only at scheduled, successfully valued closes to invoke the Python strategy callback with `(session_index, NativeAccount, fills)`. Propagate Python exceptions raised inside callbacks unchanged with their tracebacks intact; map invalid caller inputs or wire payloads to `PyValueError` and `SnapshotError` engine failures to `PyRuntimeError`.
- **Shared `PreparedMarket`, isolated `SnapshotSession` state**: `Market` (`#[pyclass(frozen)]`) owns an immutable `PreparedMarket` opened with `Market.open(path, sha256)`, which streams the file through `tm_runner::snapshot::read_market` so the market bytes are never held whole; map a digest mismatch to `PyValueError("market snapshot SHA-256 mismatch")` (which `snapshot.py` reports as an object hash mismatch), file errors to the matching `OSError`, and parse errors to `PyValueError`. `price_grid` writes float64 closes and listing evidence straight into the returned Python buffers. Reuse an open `Market` across repeated backtest runs while creating an independent `SnapshotSession` per `Market.run(...)` call so cash, FIFO lots, active targets, and fill cursors never leak between runs. Reject unsorted or out-of-range `decision_indices` and disallow supplying both a Python `callback` and precomputed `run.targets`.
- **Exact integer boundary representations**: Exchange monetary balances, target weights, prices, fees, and share counts across the FFI boundary as exact scaled integers (`i128`/`i64` `Fixed8` units and whole-share counts; `NativeAccount` stores positions sorted by `instrument_id` for binary search) or canonical UTF-8 JSON bytes (`PyBytes`). Never convert ledger values through binary floating point in `tm-python`.
- **`abi3-py312` compatibility and type stubs**: Maintain compatibility with PyO3's `abi3-py312` stable ABI so one compiled wheel supports Python 3.12+, and update [`python/trademaster/_native.pyi`](../../python/trademaster/_native.pyi) alongside any signature or class change.
- **Wheel schema packaging (`build.rs`)**: [`build.rs`](build.rs) stages `../tm-core/schemas/*.json` into `$OUT_DIR/schemas` (with `cargo:rerun-if-changed=../tm-core/schemas`) so `maturin` bundles the exact compile-time contracts into `trademaster/schemas/`.

## Verification

- Lint Rust bindings: `cargo clippy --locked -p tm-python --all-targets -- -D warnings`
- After rebuilding the editable extension (`uv sync --locked --extra dev`), run [`tests/test_snapshot_backtest.py`](../../tests/test_snapshot_backtest.py), [`tests/test_replay.py`](../../tests/test_replay.py), and [`tests/test_schema_golden.py`](../../tests/test_schema_golden.py).
