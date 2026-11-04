# M8 public factor libraries from Huatai and Guotai Junan

Status: completed
Owner: Codex
Updated: 2026-08-23
Open questions: none

## Sources and scope

Use `docs/research/public-factor-library-survey.md` to catalog the 53 A-share style factors summarized by Huatai in 2020 and Guotai Junan's (GTJA) Alpha191. Track which candidates can be materialized, which have only local variants, and which await formula review, data or license clarification. Catalog membership alone does not establish that a factor has executable code or historical values.

**Huatai.** The main collection contains the 53 factors in the 2020 empirical study of within-industry stock selection. Separate records cover 50 money-flow factors, 19 consensus forecast factors, 51 financial-quality factors, historical percentile factors (89 reported, 86 enumerated), and the structured risk model. Money flow and consensus forecasts are `blocked_data`. Reserve the risk model for a future `RiskModelDefinition`.

**Guotai Junan.** Register every 2017 Alpha1–Alpha191 as a source candidate. Before promotion to an executable definition, require the source formula, a canonical abstract syntax tree, approved corrections, versioned operator semantics and a reference case. Unresolved issues include `SMA/MAX/MIN`, benchmark/MKT/SMB/HML inputs, regression and recursive `SELF`. Retain blocked status for affected formulas.

**Licensing.** The broker reports are `paper-disclosure` sources. Publication of a paper does not confer an open-source code license. Use the Apache-2.0 DolphinDB module as an independent numerical reference without adding a DolphinDB Server runtime dependency. Retain source attribution and `legal_review` until redistribution rights for full implementations are confirmed.

## Public records and status

| Interface | Contents or responsibility |
|---|---|
| `FactorSourceRecord` | Source type, title, release, date, reference, evidence hash, license and redistribution terms. |
| `FactorCandidateRecord` | Stable identity, collection, category, formula, dependencies, status, reasons for blocking, linked definition and semantic differences. |
| `FactorCollectionDefinition` | Ordered members, member count, source hash, collection type and collection hash. |
| `PublicFactorLibrary` | Content-addressed JSON stores the records. DuckDB indexes sources, collections and candidates for discovery. Reject conflicts and corruption. |
| CLI | `tm-factor library-list`, `library-describe` and `library-status`. |

| Status | Meaning |
|---|---|
| `implemented` | Formula, data, PIT and license metadata are complete and a definition can materialize the factor. |
| `implemented_variant` | A related local definition exists. Its calculation differs from the source. |
| `ready` | The formula is clear and the data is available. Implementation is pending. |
| `blocked_data` | Required historical fields or PIT snapshots are unavailable under the data permissions. |
| `blocked_semantics` | The source or an operator still admits unresolved interpretations. |
| `legal_review` | The calculation is feasible. Rights to copy or redistribute it are unresolved. |
| `benchmark_only`, `risk_model_only`, `shadow_only` | Use the corresponding benchmark, risk-model or shadow interface outside individual-stock alpha generation. |

## Milestones and acceptance

| Milestone | Owner | Status | Evidence |
|---|---|---|---|
| M8.1 Source, collection and candidate identity | Codex | completed | Content-addressed JSON, DuckDB and rejection tests |
| M8.2 Huatai 53 manifest and status mapping | Codex | completed | 53 factors, category counts 8/4/12/5/1/1/13/5/4, data tiers 14/36/3 |
| M8.3 GTJA 191 candidate manifest and unresolved formulas | Codex | completed | IDs 001..191, data tiers 146/40/4/1 |
| M8.4 Link definitions and select the first implementations | Codex | completed | 11 exact implementations and 7 explicit variants |
| M8.5 CLI, storage, documentation, packages and review | Codex | completed | 176 tests, package checks, wheel installation and one review with findings resolved |

- [x] Huatai has exactly 53 distinct members across the nine documented categories. GTJA Alpha001–Alpha191 are consecutive, unique and each has a source and status. Unaudited formulas remain candidates.
- [x] Mark the 50 money-flow and 19 consensus factors `blocked_data` and the risk model `risk_model_only`.
- [x] Hash every source, candidate and collection. Reject different content under one identity, and reject corrupt files. Queries support collection, category, status and dependency. CLI output is stable JSON.
- [x] Map related local fundamental factors as `implemented_variant` and record the exact difference.
- [x] Update READMEs, factor documentation and module instructions. Confirm CLI operation after installing the wheel in isolation. Pass Python and Rust checks and complete one scoped independent review.

## Decisions and results

Register the complete source catalogs first. Promote candidates in batches as data and formula questions are resolved. Keep Qlib Alpha158/360, WQ101, vendor shadow factors and factor-return benchmarks in separate collections from the two broker libraries.

After `library-sync`, the local catalog held 8 sources, 7 collections and 460 candidates. Read each candidate's metadata from its JSON file and use `library/catalog.duckdb` to find it. The registry had 22 executable definitions across the 11 existing fundamental factors, Huatai Size and ten GTJA factors.

The real Tushare cache test used `strategy_requests` to verify `daily/adj_factor` object hashes. It materialized the ten GTJA factors and Huatai Size over 2,378 sessions in Parquet and verified cache hits. The ten-year daily cache covered only the union of 446 previously selected stocks. That sample cannot support an unbiased all-A-share IC estimate.

## Development and review evidence, 2026-08-23

1. `uv run pytest -q tests/test_public_factor_library.py tests/test_factor_cli.py` initially failed during collection because `trademaster.factors.public_library` did not exist. The tests specified Huatai's nine category counts and data tiers, GTJA's continuous numbering and tiers, seven collections and 460 candidates, content-addressed persistence, corruption rejection and CLI queries.
2. `tests/test_public_executable_factors.py` initially failed because `trademaster.factors.public_factors` did not exist. It specified Huatai Size, ten low-ambiguity GTJA formulas, Tushare market-cap units, warm-up behavior and hand-calculated values.
3. `uv run pytest -q tests/test_public_executable_factors.py tests/test_public_factor_library.py tests/test_public_factor_real_cache_e2e.py tests/test_factor_cli.py` reported `10 passed`. Targeted Ruff and strict mypy checks passed.
4. Independent review found DuckDB could silently change membership and filter results. GTJA adjusted prices were described only as an external input. The review also found gaps in definition mapping, dependency queries, units and Alpha015's missing-data boundary. New tests initially failed during collection because the unified `factors.builtin` registry did not exist.
5. After targeted fixes, the four test groups reported `13 passed`. They covered deleted candidate index rows, tampered discovery status, missing definitions, implemented candidates without a formula, dependency filters, explicit HFQ dependencies, parameters and units, as well as Alpha015 missing data. The top-level membership manifest and checks against DuckDB were included. At the user's request the review ended after those fixes.

**Release verification**

- `TUSHARE_TOKEN="$(<...token-file...)" uv run pytest -q` reported `176 passed`, including live Tushare network interface tests and materialization from the real local cache. No token appeared in output or artifacts.
- `uv run ruff check python tests` and `uv run mypy` passed. Mypy checked 62 source files.
- `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings` and `cargo test --workspace` passed.
- `uv build` produced a wheel and sdist. Installing the wheel into a new virtual environment yielded 22 managed definitions and 460 candidates. A dependency query returned ten implemented GTJA factors.
- Static checks for Markdown fences, conflict markers and placeholders passed.
- The local library was `data/strategies/industry-fundamental-top5-10y/factors/library` with `library_sha256=c771e024b1dfeba5460e25c13bda616ea03ccf3b9f383831d637243678c77e6e`.

## Changed files

- `specs/INDEX.md`
- `specs/20260823-m8-huatai-gtja-factor-library.md`
- `python/trademaster/factors/public_library.py`
- `python/trademaster/factors/public_cn.py`
- `python/trademaster/factors/public_factors.py`
- `python/trademaster/factors/builtin.py`
- `python/trademaster/factors/__main__.py`
- `python/trademaster/factors/__init__.py`
- `tests/test_public_factor_library.py`
- `tests/test_public_executable_factors.py`
- `tests/test_public_factor_real_cache_e2e.py`
- `tests/test_factor_cli.py`
- `docs/public-factor-library.md`
- `docs/factor-management.md`
- `AGENTS.md`
- `python/trademaster/factors/AGENTS.md`
