# Architecture Documentation Guide

## Scope

This directory maintains TradeMaster's system walkthrough ([`overview.md`](overview.md)) and editable SVG architecture diagrams (`trademaster-overview.svg`, `trademaster-data-flow.svg`, `trademaster-v1-architecture.svg`) covering Python-to-Rust boundaries, execution phases, and data lineage.

## Invariants

- **Code-backed claims**: Anchor every description of current runtime behavior in checked-in source code or regression tests, and explicitly label any unimplemented interface or planned change as proposed.
- **Separation of concerns**: Document component ownership, runtime call sequencing, and schema/data transformations as distinct views rather than conflating them in one diagram or prose block.
- **Synchronized updates**: Update the affected SVG diagrams, [`overview.md`](overview.md), and [`docs/reference/`](../reference/) together whenever a public API, wire contract, or execution sequence changes. Keep all diagram labels and legends in English.
