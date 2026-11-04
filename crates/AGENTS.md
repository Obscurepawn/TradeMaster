# Rust Workspace Guide

Rust implements the trading rules used to execute orders and update accounts.
Keep domain records immutable, use typed errors, and make event ordering
deterministic and ledger updates atomic. Other crates can depend on `tm-core`.
Keep dependencies on higher-level crates out of `tm-core`.

Start each change to public behavior with a failing integration or property
test. From the repository root, run these workspace checks before handoff:

```bash
cargo fmt --all --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace
```
