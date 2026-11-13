# M7: Managed Factor Registry, Materialization Store, and Strategy Refactor

**Status:** Completed | **Date:** 2026-08-23 | **Scope:** Managed factor DAG, Parquet/DuckDB materializations, factor evaluation, `tm-factor` CLI, and fundamental strategy migration (`trademaster.factors`, `trademaster.data`, `trademaster.strategies.industry_fundamental`) | **Updates:** [M2: Factor Computation and Signal Generation](20260811-m2-factor-signal-layer.md) | **Updated-by:** [M9: Twenty-Year All-A-Share Factor Research Pipeline and Quality Audit](20260823-m9-full-a-20y-factor-evaluation-quality.md)

> **Post-M9 Point-in-Time Audit Notice (2026-08-23):** During the M9 audit, `_active_industry` was found to ignore the signal date and `out_date`, assigning post-signal industry classifications (`in_date > signal_date`) in 55 of 560 Top-1 selections and 20 of 200 Top-10-industries selections. Combined with continuous backward-adjusted (QFQ) price execution approximations, those ten-year return figures are retained strictly as engineering replay diagnostics rather than point-in-time (PIT) alpha estimates. The default policy now enforces historical industry membership on the signal date and halts on missing intervals.

## Motivation and Defects Addressed

Auditing the ten-year [Top-1](20260812-industry-fundamental-top1-experiment.md) and [Top-10-industries](20260812-industry-fundamental-top10-industries.md) runs surfaced twelve architectural and correctness issues (`F-01` through `F-12`) across cross-sectional scoring, execution fallbacks, identity hashing, and factor reuse. Milestone M7 introduced a persistent managed factor subsystem with Parquet/DuckDB provenance, migrated the nine fundamental indicators and two composites into managed factor definitions, and replayed both strategies over the ten-year Tushare cache via v3 run bundles.

| ID | Defect and Consequence | Implemented Resolution |
|---|---|---|
| `F-01` | Public cross-sectional scorer standardized mixed `signal_time` rows together, leaking information across dates. | Rejected mixed timestamps in single-cross-section APIs and grouped managed factor calculations strictly by `event_time`. |
| `F-02` | Missing execution-day price limits or trading status fell back to permissive synthetic limits. | Enforced fail-closed execution whenever required market status or price-limit evidence is absent. |
| `F-03` | `fundamental_selections` omitted strategy and factor definition digests across incompatible variants. | Defined a versioned selection schema binding strategy, factor definition, and input snapshot hashes. |
| `F-04` | `strategy_id` and `run_id` encoded only `top_per_industry`, allowing weights or universes to change under the same ID. | Normalized the complete strategy configuration to derive canonical definition and run hashes. |
| `F-05` | Nine indicator formulas, signs, winsorization bounds, and weights were hard-coded inside strategy code. | Registered nine atomic `FactorDefinition` records plus two managed composites (within-industry and market-wide). |
| `F-06` | Exposure diagnostics retroactively applied each stock's final market-cap bucket across its entire history. | Applied size and industry classifications strictly within their effective observation intervals. |
| `F-07` | Strategy cache logic duplicated M1 Tushare/Parquet/DuckDB responsibilities under separate evidence rules. | Introduced provider-neutral `ResearchDataSource` returning immutable Parquet evidence while retaining existing ten-year cache files via a compatibility adapter. |
| `F-08` | Selection snapshots recorded only winning rows, preventing replay audits of the candidate universe or inputs. | Bound full-universe input snapshots, factor definition hashes, and output Parquet SHA-256 digests into `FactorMaterialization` and v3 run bundles. |
| `F-09` | Report summaries hard-coded "CSI 300" and "ten years" even when configured for other universes or windows. | Generated report titles, narrative summaries, and metadata dynamically from the normalized run configuration. |
| `F-10` | `fetched_at` defaulted to `backtest_end + 1 day` rather than recording actual ingestion time. | Recorded wall-clock ingestion time, run `as_of` cutoff, and row availability (`known_at`) as distinct fields. |
| `F-11` | Requesting `N` top industries silently returned fewer industries when fewer qualified. | Added an explicit exact-count vs. minimum-count option, rejecting industry shortfalls by default. |
| `F-12` | Sorting target-weight orders by `signal_id` allowed display IDs to alter board-lot purchase priority in small accounts. | Enforced sells before buys and deterministic sorting by `instrument_id` within each side (`signal_id` used only as a final tie-breaker). |

Model training, distributed task schedulers, and real-time streaming factor servers remained outside M7 scope, and moving multi-day exit retries into the Rust snapshot engine was deferred to [Code Architecture Refactoring](20261104-code-architecture-refactor.md).

## Public Interfaces and Storage Architecture

- **`FactorDefinition` & `ManagedFactorRegistry`:** Each definition binds a stable `(factor_id, version)`, family, description, upstream factor/dataset dependencies, parameters, lookback window, cross-sectional scope, implementation code SHA-256, and canonical definition SHA-256. `ManagedFactorRegistry` supports lookup by ID or definition digest and rejects duplicate identities, unknown dependencies, and DAG cycles.
- **`FactorManager` & `FactorMaterialization`:** `materialize()`, `load()`, and `query_values()` consume Arrow tables tied to an immutable **data snapshot** (via `ResearchDataSource`). Identical `(definition_sha256, input_snapshot_id, universe, event_range)` requests reuse verified Parquet files; cache misses compute in dependency order, validate schemas, write Parquet atomically, and index `FactorMaterialization` metadata (definition SHA, input snapshot and object hashes, output SHA, row count, universe, and `created_at`) in `FactorCatalog` (DuckDB).
- **`FactorEvaluator` & CLI (`tm-factor`):** Computes observation coverage, Pearson IC, Spearman RankIC, IC mean/std/ICIR, quantile portfolio returns, top-minus-bottom spread, quantile turnover, and pairwise factor correlations. Exposed via the `tm-factor` CLI (`list`, `describe`, `materialize`, `evaluate`).

## Milestones and Replay Results

| Subtask | Status | Verification Summary |
|---|---|---|
| M7.1: Defect reproduction and fixes (`F-01`–`F-12`) | Completed | Focused regression suites covering every defect class |
| M7.2: Managed `FactorDefinition`, DAG validation, and code/config hashing | Completed | Parameter, source-hash, and cycle-rejection unit suites |
| M7.3: Parquet/DuckDB materialization store and v3 bundle provenance | Completed | Temporary DuckDB/Parquet corruption and provenance tests |
| M7.4: `FactorEvaluator` diagnostics and `tm-factor` CLI | Completed | Hand-calculated IC/quantile fixtures and wheel CLI smoke tests |
| M7.5: Migration of nine fundamental indicators and two composites | Completed | Exact reproduction of all 560 Top-1 and 200 Top-10 selection rows |
| M7.6: Ten-year Rust replay, v3 reports, and independent review | Completed | Zero provider calls, 540 byte-identical bundle files per variant |

Because fixing `F-12` removes `signal_id` sensitivity during board-lot cash allocation, economic equivalence was verified against replaying the *legacy request on the corrected Rust runtime* rather than preserving buggy order priority:

- **Catalog & selection parity:** The managed catalog persisted 11 definitions, 220 materializations, and 220 factor Parquet files whose source rebuilds reproduced identical definition digests. All-industry Top-1 matched all 560 selection rows across 20 dates (`108.3896%` total return; `CNY 1,041,947.75675909` ending NAV), and Top-10-industries × Top-1 matched all 200 selection rows (`47.1649%` total return; `CNY 735,824.26104058` ending NAV under deterministic `instrument_id` lot sizing).
- **Zero-call v3 bundle replay:** Initial and replay directories for each variant contained 540 byte-identical files (41 snapshots, 11 definitions, 220 materializations, 220 factor Parquet objects, summary JSON, Markdown, and standalone HTML) with `0` provider calls under an invalid token.

## Verification Record

- **Independent review closure:** Resolved 3 High findings (covering missing-status fallbacks, parent factor dependency verification, and forward-label alignment) with regression tests; re-review confirmed zero remaining Critical or High issues.
- **Test, type, and package suites:** Python passed 164 tests (0 skipped, including live stock/ETF Tushare E2E suites with `TUSHARE_TOKEN` isolated to an external `0600` file and 0 credential scan matches), Ruff, and strict `mypy` across 55 source files. Rust passed `cargo fmt --check`, `cargo clippy`, workspace tests, doc-tests, and Cargo workspace packaging. Installing the built wheel into a clean virtual environment verified `trademaster.factors`, strategy imports, and `tm-factor list` (returning all 11 definitions). Architecture diagrams (`docs/architecture/trademaster-v1-architecture.svg`) passed structural and 4000px visual checks, and all local Markdown links resolved.

## Changed Files

- `specs/INDEX.md`, `specs/20260823-m7-factor-management-strategy-refactor.md`, `AGENTS.md`, `pyproject.toml`
- `docs/factor-management.md`, `docs/architecture/overview.md`, `docs/architecture/trademaster-v1-architecture.svg`
- `python/trademaster/data/research.py`, `python/trademaster/data/__init__.py`, `python/trademaster/factors/`
- `python/trademaster/e2e/__init__.py`, `python/trademaster/e2e/real_tushare.py`
- `python/trademaster/strategies/AGENTS.md`, `python/trademaster/strategies/industry_fundamental/`, `python/trademaster/strategies/industry_fundamental_top5/`
- `crates/tm-engine/src/lib.rs`, `crates/tm-engine/tests/scheduled_strategy.rs`
- `tests/test_factor_management.py`, `tests/test_factor_evaluation.py`, `tests/test_factor_cli.py`, `tests/test_fundamental_managed_factors.py`, `tests/test_industry_fundamental_top5.py`, `tests/test_fundamental_strategy_data_cache.py`, `tests/test_e2e_orchestrator.py`, `tests/test_real_tushare_e2e.py`
- `artifacts/strategies/industry-fundamental-managed-top1-final-v2{,-replay}/`, `artifacts/strategies/industry-fundamental-managed-top10-industries-top1-final-v2{,-replay}/`
