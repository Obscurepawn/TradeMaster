# Rust Workspace Guide

## Scope

The Cargo workspace implements TradeMaster's exact domain types, order validation and fee schedules, trade execution, FIFO lot accounting, saved-request decoding, and in-process PyO3 bindings across [`tm-core`](tm-core/AGENTS.md), [`tm-engine`](tm-engine/AGENTS.md), [`tm-runner`](tm-runner/AGENTS.md), and [`tm-python`](tm-python/AGENTS.md).

## Invariants

- **Strict dependency DAG**: `tm-core` is the dependency-free domain leaf; `tm-engine` depends only on `tm-core`; `tm-runner` depends on `tm-core` and `tm-engine`; `tm-python` wraps `tm-core`, `tm-engine`, and `tm-runner` for Python callers. Never introduce upward or cyclic dependencies into `tm-core`.
- **Immutable domain records and typed errors**: Keep domain records immutable after construction, validate inputs via checked constructors, and surface domain or wire failures with structured `thiserror` enums rather than panics.
- **Deterministic replay and atomic accounting**: Order all simulation phases deterministically by timestamp and explicit intra-timestamp phase rules, use exact fixed-point integers (`Fixed8`, whole shares), and commit `LotLedger` state transitions atomically.

## Verification

Start every change to public behavior with a failing integration or property test, then run the full workspace checks from the repository root:

```bash
cargo fmt --all --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace
```
