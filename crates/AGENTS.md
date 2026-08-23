# Rust Workspace Guide

Rust owns authoritative execution semantics. Keep domain records immutable,
failures typed, ordering deterministic, and ledger mutations atomic. Crates may
depend on `tm-core`; `tm-core` must not depend on higher-level crates.

All public behavior starts with a failing integration or property test. Run
`cargo fmt --check`, workspace Clippy with `-D warnings`, and workspace tests
before milestone handoff.
