# Ledger State Hashing Cost in Daily Snapshot Runs

**Status:** Completed | **Date:** 2026-11-26 | **Scope:** Per-fill and per-session cost of `LotLedger` transitions (`tm-core` account snapshots and ledger effects, `tm-engine` `LotLedger::apply`/`settle`, `tm-engine::snapshot`) | **Updates:** [M3: A-Share Execution Rules and Lot Accounting in Rust](20260811-m3-rust-rules-ledger.md)

## Motivation and Scope

Every `LotLedger` transition records a `LedgerEffect` that binds a `before` and `after` `AccountSnapshot`. Building an `AccountSnapshot` serializes the NAV, every position, and every open FIFO lot to JSON and SHA-256 hashes the result (`account-state/v1` payload in `tm-core`), and `LedgerEffect::try_new` uses the two `state_hash` values both as audit evidence and to assert that a transition changed state exactly when it reports changes. `apply` additionally clones the whole ledger before posting fills so a failed transition leaves no partial state. `settle` runs once per session and `apply` once per execution, so the cost of every transition grows linearly with the number of open lots.

The daily snapshot engine never publishes these hashes: its results carry daily equity, positions, attempts, orders, fills, and batches. Yet the hashing dominates realistic runs. On a 7.7M-row Hong Kong snapshot (2,812 instruments × 4,160 sessions; Apple M4), a weekly 30-name momentum strategy took 12.6 s, of which 2.5 s ran inside the Python strategy; replaying the same recorded targets without any Python strategy still took 9.6 s for 43,038 fills, while a three-instrument buy-and-hold replay over the same sessions took 0.7–0.9 s. A synthetic study measured ~1.8 ms per fill once 2,400–4,800 lots were open, versus ~0.07 ms per fill when the lot count stayed bounded. Full-universe A-share and US strategies that keep adding to positions would therefore spend most of their run time re-serializing unchanged lots.

### Non-Goals

- No change to the `account-state/v1` payload, `state_hash` values, ledger effect contents, event-run audit tables, run bundles, or any published result; event-loop bundles must remain byte-identical and replayable.
- No change to FIFO lot semantics, settlement timing, fees, or daily snapshot results.
- Snapshot market memory is handled separately in [Compact Market Rows and Streaming Snapshot Loading](20261126-snapshot-compact-market-rows.md).

## Investigation

### Profile

A replay of fixed weekly targets on a synthetic lot-accumulating snapshot (200 instruments × 1,500 sessions, 40 names topped up every week, ~2,800 open lots on average and 3,166 at peak, 31,041 fills) took 81.2 s, or 2.6 ms per fill. Sampling the run with macOS `sample` (1 ms interval) attributed the 15,623 samples spent in the session loop as follows:

| Work | Share |
|---|---:|
| `Ledger::apply` per fill | 92.8% |
| — before/after `AccountSnapshot` construction (`snapshot_with_marks` ×2) | 71.3% |
| —— SHA-256 of the `account-state/v1` payload | 33.9% |
| —— `serde_json` encoding of the payload | 9.5% |
| —— `AccountSnapshot::try_new` ordering and quantity validation | 12.2% |
| —— materializing one `PositionLot` per open lot | 15.6% |
| — `LedgerEffect::try_new` lot replay and comparison | 11.4% |
| — whole-ledger clone | 4.1% |
| — fill arithmetic, postings, and lot updates | 6.0% |
| `Ledger::settle` per session (one snapshot, occasionally two) | 3.8% |
| Opening and closing `AccountSnapshot` valuations | 2.7% |
| Sizing, permissions, and result rows | 0.7% |

Every term above except the last row scales with the number of open lots, and the per-fill terms dominate because a fill builds two full account snapshots. SHA-256 alone cannot be made incremental: the payload begins with the event time, cash, and NAV, which change on every transition, so neither a cached hash prefix nor cached per-lot JSON fragments would avoid hashing the whole lot list.

### Consumer inventory

| Consumer | Reads | Persists |
|---|---|---|
| `EventLoop::run` (`tm-engine`) | `Ledger::apply`/`settle` effects; `Ledger::snapshot` per event and per submitted order; `PortfolioView::state_hash` handed to strategies and validators | `RunTrace::ledger_effects` and `account_snapshots` |
| `tm-runner` | `RunTrace` | `trademaster.e2e-result/v1`: `ledger_effects` and `account_snapshots` (serde, including `before_state_hash`, `after_state_hash`, `state_hash`) and `nav[].state_hash` |
| `tm-core` artifact adapters | `LedgerEffect::{before,after}_state_hash` | `account_states` audit table; `validate_run_artifact_rows` checks the hash chain and recomputes the final `account-state/v1` hash from the positions and lots tables |
| Python `trademaster.contracts` / e2e bundles | result JSON and audit tables | v3 run bundles (hash-chain validation) |
| Daily snapshot engine (`tm-engine::snapshot`, `tm-python`) | `Ledger::apply` per fill, `Ledger::settle` per session, `snapshot_with_marks` at each sized open and each close | Nothing: `SnapshotResult`, `result.json`, and `NativeAccount` carry cash, market value, equity, receivables, positions, orders, fills, and batches, never a state hash or ledger effect |

The change assertion `changed == (before_state_hash == after_state_hash)` in `LedgerEffect::try_new`, like the lot replay beside it, is therefore a self-check whose only persisted consequence lies on the event path.

## Design

The daily snapshot engine stops building audit records it never publishes, while the event path keeps them byte for byte:

- **Shared staging (`LotLedger::stage_execution` and `commit`).** A filled execution is computed against the committed lots plus a small overlay of the lots it opens, reduces, or closes, and returns its posting groups, lot changes, cash, clock, mark, and transition sequence. `commit` installs the result infallibly, so any error still leaves cash, lots, marks, and hashes untouched. The fill loop, its checks, and their order and messages are unchanged.
- **Audited path (`Ledger::apply`, `Ledger::settle`).** Unchanged in behavior: build the before snapshot, stage, commit into a clone, build the after snapshot, and validate the `LedgerEffect` before replacing the ledger. Event runs remain O(open lots) per transition because their outputs persist the hashes of both states.
- **Unaudited path (crate-private `apply_unaudited`, `settle_unaudited`, `valuation`).** The daily engine posts fills and settlements through the same staging and unlock logic without account snapshots or effects, and values opens and closes with `valuation`, which aggregates positions and NAV without materializing lots or hashing them. Position totals are now keyed by borrowed instrument IDs, removing one string allocation per lot from every valuation.

Candidate outcomes: *hashes on demand* is adopted for the daily engine in its simplest form (no hash at all where none is consumed, rather than a lazily computed one); *no whole-ledger clone* is adopted for the daily engine through staging; *cheaper identical hashes* is rejected because the varying payload prefix forces a full rehash regardless of cached fragments.

Errors raised only by the skipped work—`AccountSnapshot`, `PositionLot`, and `LedgerEffect` validation and the before-snapshot `missing mark price` check—cannot occur on the daily path: its lots are created by the ledger or validated initial lots, every held instrument receives a mark at each close before the next transition, and valuation at the following open or close still applies the overflow and mark checks. A unit test (`crates/tm-engine/src/unaudited_ledger_tests.rs`) drives 40 seeded sequences of 120 random buys, sells, oversells, duplicate lot IDs, multi-fill executions, and settlements through both paths and requires identical ledger state, error text, and valuations after every step.

## Tasks

- [x] Profile and consumer inventory recorded above
- [x] Characterization tests on the baseline
- [x] Selected design implemented
- [x] Differential check and benchmarks recorded below
- [x] Update `crates/tm-engine/AGENTS.md` (`tm-core` contracts and the execution reference are unchanged)

## Open Items

- The remaining daily-path cost is O(open lots) scanning without allocation or hashing: a sale scans all open lots for the instrument's sellable ones, each valuation aggregates every lot, and the session loop scans every lot for `corporate_action_gap` and missing closes. In the synthetic run these account for roughly 31%, 23%, and 34% of the remaining 0.84 s. A per-instrument lot index would make them proportional to positions, but it changes the `LotLedger` layout that `tm-engine::snapshot` reads directly and is left for a follow-up coordinated with the market-row work in [Compact Market Rows and Streaming Snapshot Loading](20261126-snapshot-compact-market-rows.md).
- Event runs still build two account snapshots per transition by design.

## Verification and Regression Record

- **Characterization (green before and after):** `crates/tm-engine/tests/ledger_state_characterization.rs` pins the before/after `account-state/v1` hashes and an FNV-1a fingerprint of the serialized `LedgerEffect` for a two-fill buy, a T+0 buy, a no-op execution, two settlement unlocks, an empty settlement, a partial and a full FIFO sell, and rejected locked and oversized sells, plus the final closing hash; a second test pins the full daily result of a lot-accumulating run with initial lots, mixed `t0`/`t1` settlement, and a cash dividend. Both passed on the unmodified engine (commit `9f84a34`) and pass unchanged after the change. Cash dividends are not ledger transitions—the daily engine credits cash and adjusts marks directly—so they are pinned through the daily result.
- **Differential check against the unmodified engine (`fb67dbb`, built and run in a separate environment):**
  - Daily `result.json`, byte-identical on all 9 synthetic scenarios (the 200 × 1,500 snapshot above with `t0` and `t1` instruments, 1% random suspensions, and 600 cash dividends): fixed-target accumulation with and without `detail`, six seeded random target sequences mixing daily and sparse rebalances, full exits, initial lots, slippage, attempt limits, and price-limit settings, and a `week_end` Python strategy whose choices depend on the closing cash, equity, and sellable quantities it receives.
  - Hong Kong snapshot (7,706,886 rows; 2,812 instruments × 4,160 sessions): the weekly 30-name momentum strategy and the engine-only replay of its 730 recorded target batches produced byte-identical `result.json` with and without `detail` (43,038 fills; 180 open lots on average, 309 at peak).
  - Event runner: the 7 distinct event requests submitted by the Python suite and the `docs/examples/round-trip.json` replay produced byte-identical `trademaster.e2e-result/v1` bytes, including every ledger effect and state hash.
- **Benchmarks** (Apple M4, 16 GB, macOS 27.2; release `abi3` extension; Python 3.12; single process; wall time of `backtest()` excluding `Snapshot.open`):

| Workload | Fills | Before | After | Per fill before → after |
|---|---:|---:|---:|---:|
| Synthetic accumulation, 200 × 1,500, ~2,800 open lots | 31,041 | 81.2 s | 0.84 s | 2.62 → 0.027 ms |
| Synthetic seeded random targets (six runs, at most 146 open lots) | 10,164–30,300 | 0.47–1.63 s | 0.11–0.23 s | 0.04–0.10 → 0.007–0.011 ms |
| Hong Kong recorded-target replay | 43,038 | 9.7–9.9 s | 0.94–1.03 s | 0.23 → 0.022 ms |
| Hong Kong weekly momentum with the Python strategy | 43,038 | 12.5 s | 3.5 s | — |

- **Repository checks:** `uv run ruff check python tests` and `uv run mypy --strict python tests` (108 files) pass; `uv run pytest -q` passes 453 tests with 4 credential- or data-gated skips; `cargo fmt --all --check`, `cargo clippy --locked --workspace --all-targets -- -D warnings`, and `cargo test --locked --workspace` (71 tests, including the 3 added here) pass.
