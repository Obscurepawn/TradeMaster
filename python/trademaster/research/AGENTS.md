# Research Pipeline Guide

## Scope

`trademaster.research` (`tm-research-full-a`) plans and resumes bulk historical downloads across the full A-share universe, materializes dense point-in-time (PIT) market/fundamental panels and forward-return labels, computes factor diagnostics, and emits verifiable study reports.

## Invariants

- **Content-addressed plans and leased downloads**: Address immutable download plans by SHA-256. Store provider responses in Parquet while using private DuckDB tables to coordinate task leases and object indexes; downstream panel builders consume only `completed_evidence_snapshot`. Verify dataset coverage independently of HTTP success, tracking historical universe membership, endpoint absent-row semantics, provider permission errors, and historical lookback limits as distinct states. Workers may fetch and commit only while holding an active task lease, and completed tasks must match the request SHA-256 against both the DuckDB evidence record and embedded Parquet metadata.
- **PIT panel construction and evaluation hygiene**: Make date-only financial announcements observable no earlier than the next eligible trading session, and never backfill revisions lacking a publication timestamp onto the original announcement date. Retain explicit rows for missing observations and suspensions across dense session windows, and ensure every factor score or forward return flagged `is_valid=True` is finite. Require forward-return entry timestamps for close-derived features to fall strictly after that close, and record the factor definition, source objects, universe policy, label alignment, and evaluation parameters in every report manifest.

## Verification

```bash
uv run pytest tests/test_full_a_research.py tests/test_full_a_panel.py \
  tests/test_full_a_fundamental.py tests/test_research_tushare_client.py
```
