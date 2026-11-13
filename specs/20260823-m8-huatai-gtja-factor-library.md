# M8: Huatai Style Factors and Guotai Junan Alpha191 Catalog

**Status:** Completed | **Date:** 2026-08-23 | **Scope:** Content-addressed public factor candidate catalog, readiness/licensing governance, and initial executable factors (`trademaster.factors.public_library`, `trademaster.factors.public_factors`) | **Builds-on:** [Survey of Public Factor Libraries and Institutional Research](20260823-public-factor-library-survey.md)

## Motivation and Scope

Building on the [public factor library survey](20260823-public-factor-library-survey.md) (`docs/research/public-factor-library-survey.md`), Milestone M8 catalogs Huatai Securities' 53 A-share style factors (2020) and Guotai Junan's (GTJA) Alpha001–Alpha191 (2017) as governed source candidates, tracks formula ambiguity, point-in-time (PIT) data availability, and licensing terms, and promotes an initial batch of unambiguous formulas into executable `ManagedFactorRegistry` definitions. Catalog membership never implies executable status: only candidates explicitly linked to a managed definition can materialize factor values.

- **Huatai collections:** Registers the 53 cross-industry style factors across nine categories (`8/4/12/5/1/1/13/5/4`; data tiers `14/36/3`), alongside separate collections for 50 money-flow factors (`blocked_data`), 19 analyst consensus factors (`blocked_data`), 51 financial-quality factors, historical-percentile factors (`89` reported, `86` chart-enumerated), and the structured multi-factor risk model (`risk_model_only`, reserved for a future `RiskModelDefinition`).
- **Guotai Junan Alpha191:** Registers all 191 consecutive candidates (`Alpha001`–`Alpha191`; data tiers `146/40/4/1`). Promotion to an executable definition requires the published formula, a canonical operator specification, unit/adjustment bindings, and hand-verified test vectors; formulas involving ambiguous `SMA`/`MAX`/`MIN` conventions, benchmark/`MKT`/`SMB`/`HML` regressions, or recursive `SELF` state remain blocked (`blocked_semantics` or `blocked_data`).
- **Source attribution and licensing:** Broker research publications are recorded as `paper-disclosure` sources (which do not grant open-source software licenses). Apache-2.0 DolphinDB factor modules serve only as an external numerical reference without introducing any DolphinDB runtime dependency, and unverified redistribution scopes remain tagged `legal_review`. Qlib Alpha158/360, WorldQuant Alpha101, vendor shadow factors, and return-series benchmarks remain in distinct collections.

## Catalog Schema and Candidate Lifecycle

| Component | Responsibility |
|---|---|
| `FactorSourceRecord` | Records source type (`paper-disclosure`, `open-source`, etc.), title, release date, citation, evidence SHA-256, license, and redistribution terms. |
| `FactorCandidateRecord` | Records stable candidate ID, collection, category, source formula, dataset dependencies, readiness status, blocking reasons, linked `FactorDefinition` (if any), and documented semantic deviations. |
| `FactorCollectionDefinition` | Binds ordered member IDs, expected member count, source SHA-256, collection type, and canonical collection SHA-256. |
| `PublicFactorLibrary` | Persists canonical content-addressed JSON records plus a top-level membership manifest and indexes them in `library/catalog.duckdb`, failing closed on hash mismatches, conflicting IDs, or deleted DuckDB index rows. |
| CLI (`tm-factor`) | Exposes deterministic JSON discovery commands: `tm-factor library-list`, `library-describe`, and `library-status`. |

| Readiness Status | Operational Meaning |
|---|---|
| `implemented` | Formula, PIT data contract, and license metadata are verified, and a linked `FactorDefinition` can materialize values. |
| `implemented_variant` | Linked to a local `FactorDefinition` whose preprocessing or input convention intentionally differs from the source disclosure (with exact differences recorded). |
| `ready` | Formula and PIT data inputs are unambiguous; executable implementation is queued. |
| `blocked_data` | Required historical fields or PIT timestamps are unavailable under current provider permissions. |
| `blocked_semantics` | Published formula or operator notation admits unresolved interpretations. |
| `legal_review` | Technical inputs are clear, pending confirmation of redistribution rights. |
| `benchmark_only` / `risk_model_only` / `shadow_only` | Reserved for benchmark evaluation, risk modeling, or vendor comparison outside stock-level alpha generation. |

## Milestones and Materialization Results

| Subtask | Status | Verification Summary |
|---|---|---|
| M8.1: Source, collection, and candidate content-addressed store | Completed | JSON digest, DuckDB sync, and tamper-rejection tests |
| M8.2: Huatai 53-factor and companion collection manifests | Completed | 53 distinct style members (`8/4/12/5/1/1/13/5/4`), `blocked_data` / `risk_model_only` tags |
| M8.3: GTJA `Alpha001`–`Alpha191` manifest and blocker audit | Completed | Consecutive IDs `001..191`, data tiers `146/40/4/1` |
| M8.4: Unified `factors.builtin` registry and initial promotions | Completed | 11 exact `implemented` factors (Huatai Size + 10 GTJA) and 7 `implemented_variant` mappings |
| M8.5: CLI discovery, real-cache materialization, and review | Completed | 460 cataloged candidates, 22 managed definitions, 176 passing Python tests |

Following `library-sync`, the catalog (`data/strategies/industry-fundamental-top5-10y/factors/library`, `library_sha256=c771e024b1dfeba5460e25c13bda616ea03ccf3b9f383831d637243678c77e6e`) held **8 sources, 7 collections, and 460 candidates**, paired with **22 executable `FactorDefinition` entries** (the 11 M7 fundamental definitions, Huatai Size, and 10 unambiguous GTJA price/volume factors).

Real-cache integration tests verified `daily` and `adj_factor` object hashes via `strategy_requests`, materialized Huatai Size and all ten GTJA factors across 2,378 trading sessions into Parquet, and confirmed zero-recomputation cache hits. *(Limitation: because the ten-year strategy cache covered only the 446 stocks previously selected in CSI 300 runs, unbiased all-A-share IC evaluation required the full-universe panel built in [M9](20260823-m9-full-a-20y-factor-evaluation-quality.md).)*

## Verification Record

- **Independent review fixes:** An independent review identified that direct DuckDB mutations could silently drop candidate rows or alter status filters, that GTJA backward-adjusted (HFQ) price inputs needed explicit dependency/unit declarations, and that `Alpha015` required a strict missing-data boundary check. Adding a top-level membership manifest cross-checked against DuckDB, unified `factors.builtin` registration, explicit HFQ dependency/unit metadata, and `Alpha015` boundary regressions expanded the focused M8 suite to 13 passing test groups (`test_public_factor_library.py`, `test_public_executable_factors.py`, `test_public_factor_real_cache_e2e.py`, `test_factor_cli.py`).
- **Release checks:** Python passed 176 tests (0 skipped, with live Tushare token supplied externally and 0 credential leaks), Ruff, and strict `mypy` across 62 source files; Rust passed `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, and `cargo test --workspace`. Installing the `uv build` wheel into a clean virtual environment verified all 22 managed definitions, 460 candidates, and CLI dependency queries returning the 10 implemented GTJA factors.

## Changed Files

- `specs/INDEX.md`, `specs/20260823-m8-huatai-gtja-factor-library.md`, `AGENTS.md`, `python/trademaster/factors/AGENTS.md`
- `docs/public-factor-library.md`, `docs/factor-management.md`
- `python/trademaster/factors/public_library.py`, `python/trademaster/factors/public_cn.py`, `python/trademaster/factors/public_factors.py`, `python/trademaster/factors/builtin.py`, `python/trademaster/factors/__main__.py`, `python/trademaster/factors/__init__.py`
- `tests/test_public_factor_library.py`, `tests/test_public_executable_factors.py`, `tests/test_public_factor_real_cache_e2e.py`, `tests/test_factor_cli.py`
