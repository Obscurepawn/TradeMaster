# Ledger State Hashing Cost in Daily Snapshot Runs

**Status:** Proposed | **Date:** 2026-11-26 | **Scope:** Per-fill and per-session cost of `LotLedger` transitions (`tm-core` account snapshots and ledger effects, `tm-engine` `LotLedger::apply`/`settle`, `tm-engine::snapshot`) | **Updates:** [M3: A-Share Execution Rules and Lot Accounting in Rust](20260811-m3-rust-rules-ledger.md)

## Motivation and Scope

Every `LotLedger` transition records a `LedgerEffect` that binds a `before` and `after` `AccountSnapshot`. Building an `AccountSnapshot` serializes the NAV, every position, and every open FIFO lot to JSON and SHA-256 hashes the result (`account-state/v1` payload in `tm-core`), and `LedgerEffect::try_new` uses the two `state_hash` values both as audit evidence and to assert that a transition changed state exactly when it reports changes. `apply` additionally clones the whole ledger before posting fills so a failed transition leaves no partial state. `settle` runs once per session and `apply` once per execution, so the cost of every transition grows linearly with the number of open lots.

The daily snapshot engine never publishes these hashes: its results carry daily equity, positions, attempts, orders, fills, and batches. Yet the hashing dominates realistic runs. On a 7.7M-row Hong Kong snapshot (2,812 instruments × 4,160 sessions; Apple M4), a weekly 30-name momentum strategy took 12.6 s, of which 2.5 s ran inside the Python strategy; replaying the same recorded targets without any Python strategy still took 9.6 s for 43,038 fills, while a three-instrument buy-and-hold replay over the same sessions took 0.7–0.9 s. A synthetic study measured ~1.8 ms per fill once 2,400–4,800 lots were open, versus ~0.07 ms per fill when the lot count stayed bounded. Full-universe A-share and US strategies that keep adding to positions would therefore spend most of their run time re-serializing unchanged lots.

### Non-Goals

- No change to the `account-state/v1` payload, `state_hash` values, ledger effect contents, event-run audit tables, run bundles, or any published result; event-loop bundles must remain byte-identical and replayable.
- No change to FIFO lot semantics, settlement timing, fees, or daily snapshot results.
- Snapshot market memory is handled separately in [Compact Market Rows and Streaming Snapshot Loading](20261126-snapshot-compact-market-rows.md).

## Investigation

1. Profile a replay of recorded targets (no Python strategy) on a lot-accumulating workload and attribute time to account-snapshot serialization, SHA-256, ledger cloning, and the remaining engine work.
2. Enumerate every consumer of `AccountSnapshot::state_hash`, `LedgerEffect::{before,after}_state_hash`, and the change assertion in `LedgerEffect::try_new`, in both the event loop and the daily snapshot engine, and record which outputs persist them.

## Candidate Designs

The investigation selects one or a combination, preferring the smallest change that keeps every output identical:

- **Hashes on demand:** let the daily snapshot engine post transitions without materializing state hashes, replacing the hash-equality change assertion with a structural comparison of the touched state, while the event loop keeps computing them because its audit tables persist them.
- **Cheaper identical hashes:** keep each lot's serialized payload cached when the lot is created or changed, so a snapshot concatenates cached fragments in canonical order and hashes them once instead of re-serializing every lot; the digest stays byte-identical.
- **No whole-ledger clone:** apply fills to a staging copy of only the touched instruments' lots and cash, committing on success, instead of cloning the entire ledger per execution.

## Verification Plan

1. Characterization first: tests that pin `state_hash` values and ledger-effect contents for representative transitions (buy, partial and full FIFO sells, settlement unlocks, cash dividends, no-op executions) pass on the baseline before any change.
2. All Rust and Python suites, pinned bundle hashes, and `docs/examples/*-reference.json` benchmarks pass unchanged.
3. Differential check against the baseline commit built in a separate worktree: byte-identical daily `result.json` on seeded random snapshot runs and the Hong Kong replay, and byte-identical event-loop run bundles for the existing e2e fixtures.
4. Benchmarks before and after: the Hong Kong recorded-target replay and a synthetic lot-accumulating replay, reporting time per fill and total run time with hardware and OS.

## Tasks

- [ ] Profile and consumer inventory recorded below
- [ ] Characterization tests on the baseline
- [ ] Selected design implemented
- [ ] Differential check and benchmarks recorded below
- [ ] Update `crates/tm-engine/AGENTS.md`, `crates/tm-core/AGENTS.md`, and execution reference docs if contracts are clarified

## Verification and Regression Record

Pending.
