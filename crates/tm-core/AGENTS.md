# tm-core Guide

## Scope

`tm-core` defines TradeMaster's stable identifiers, exact fixed-point scalar types (`Fixed8`, `Quantity`), immutable domain and audit records, shared status/rejection enums, typed errors, Arrow/Parquet artifact adapters, and compile-time embedded [JSON schemas](schemas/AGENTS.md).

## Invariants

- **Pure domain boundary**: Keep provider I/O, strategy logic, execution matching, and stateful ledger accounting out of `tm-core`. Hold zero process-global mutable state and depend on no higher-level workspace crates.
- **Checked construction**: Enforce domain constraints inside `try_new` and exact scalar constructors so callers cannot materialize invalid prices, negative quantities, or malformed lifecycle transitions.
- **Typed artifact serialization**: Persist event-run audit records through `ArtifactRecord` implementations and `typed_artifact_record_batch`. While the generic row adapter validates table schema and ordering, domain constructors remain the authoritative check before any record reaches an Arrow writer.

## Verification

```bash
cargo test --locked -p tm-core
```
