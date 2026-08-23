# E2E Orchestration Guide

This module owns only the typed Python-to-Rust process boundary and reproducible run bundle.
It may allocate vectorized target weights into lot-sized target quantities before execution,
but it must never simulate fills, cash, positions, fees, settlement, or NAV in Python.

Bundle v3 binds the exact request/result, source and selection snapshots, factor definitions,
materialization DAG and Parquet, report input/HTML, strategy summary and Markdown. The verifier
re-hashes every object, validates factor parent lineage, replays Rust and rebuilds HTML. Subprocess
failures and identity mismatches fail closed. Credentials never enter artifacts or logs.
