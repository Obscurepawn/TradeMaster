# M7 managed factors and fundamental strategy migration

Status: complete
Owner: Codex
Updated: 2026-08-23

> **M9 correction, 2026-08-23.** `_active_industry` ignored the signal date and `out_date`. It assigned an industry that the stock joined *after* the signal in 55 of 560 Top1 selections and 20 of 200 Top10 selections, as shown by `in_date`. These future classifications invalidate a point-in-time (PIT) interpretation of the reported returns. The reports also used continuous QFQ prices to approximate corporate actions. Retain them for diagnosis only. The default policy now checks historical membership on the signal date and stops on a gap. Static classifications have a separate experimental policy.

## Purpose and scope

The all-industry Top1 and ten-industry Top1 runs exposed problems with scoring, execution, identity and reuse. M7 addressed twelve findings, built a factor registry with persistent values and PIT provenance, moved the nine fundamental indicators into it and replayed both strategies on the existing ten-year Tushare cache. The strategy retains industry selection, TopN and rebalance rules.

| ID | Finding and consequence | Resolution |
|---|---|---|
| F-01 | The public scorer standardized different `signal_time` values together, leaking information across dates. | Reject mixed times in the single-cross-section API. Group managed factor calculations by event time. |
| F-02 | Missing execution-day limits or status produced permissive synthetic limits. Orders could fill with incomplete market rules. | Stop formal execution when required market status is missing. |
| F-03 | All variants wrote `fundamental_selections` without strategy or factor definition hashes. A single schema version described different records. | Define the derived schema and record strategy, factor and input identities. |
| F-04 | `strategy_id/run_id` encoded only TopN. Weights, preprocessing and universe could change without changing identity. | Normalize the full configuration and derive definition and run hashes. |
| F-05 | Nine scores, directions, preprocessing steps and weights lived in the strategy. | Register nine atomic factors and two composites, one within industries and one across the market. |
| F-06 | The last selected size bucket was applied to the whole history. | Use each classification only during its effective period in exposure reports. |
| F-07 | The strategy duplicated M1 Tushare, Parquet, DuckDB, cache and snapshot responsibilities, with separate rules for evidence. | Define one research data interface that returns evidence. Retain the ten-year cache through a compatibility adapter. |
| F-08 | The selection snapshot identified only winners. Replaying it could not verify the full universe, nine inputs or definitions. | Identify input snapshots, definitions and output hashes on materializations. |
| F-09 | Public configuration allowed different dates, universes and benchmarks, but reports still said “CSI 300” and “ten years.” | Generate summaries, titles and text from the recorded run configuration. |
| F-10 | `fetched_at` defaulted to the backtest end plus one day. | Record actual wall-clock ingestion, run as-of time and row availability (`known_at`) separately. |
| F-11 | A request for N industries silently returned fewer if fewer qualified. | Make exact vs. minimum counts an explicit option. Reject a shortfall by default. |
| F-12 | Sorting a target-weight portfolio by `signal_id` let display IDs change purchase priority and whole-lot holdings in small accounts. | Sell before buying. Within each side use instrument ID, with signal ID only as a final tie-break. |

M7 fixed F-01 through F-06 and F-08 through F-12. For F-07 it introduced the interface without deleting or redownloading the cache. The work excluded model training, distributed scheduling and real-time factor service. The original M7 scope retained disclosure of missing historical SW2021 revisions and the adjusted-price execution approximation. Moving exit retries into Rust was deferred to a separate milestone. The existing idempotent zero-target exit policy stayed in effect.

## Public interfaces

- `FactorDefinition` records stable ID/version, family, description, dependencies, parameters, lookback, scope, implementation SHA and definition SHA.
- `ManagedFactorRegistry` supports lookup by identity or definition SHA. It rejects duplicate identities, unknown dependencies and cycles.
- `FactorManager.materialize()/load()/query_values()` accept Arrow data tied to a snapshot. An identical definition, input, universe and event range reuses Parquet. Otherwise compute, validate, write atomically and register the result in DuckDB.
- `FactorMaterialization` records definition SHA, input snapshot ID and object hashes, output SHA, schema, row count, range, universe and creation time (`created_at`).
- `FactorEvaluator` covers observation coverage, Pearson IC, RankIC, IC mean/standard deviation/ICIR, returns by quantile, long–short spread, turnover and factor correlations.
- `FactorCatalog` lists definitions, materializations and evaluations. Parquet holds factor values. DuckDB indexes and queries them.
- The proposed CLI actions were `factor list`, `factor describe`, `factor materialize` and `factor evaluate`. The released entry point was tested as `tm-factor list`.

## Milestones and acceptance

| Milestone | Owner | Status | Evidence |
|---|---|---|---|
| M7.1 Reproduce and correct public behavior | Codex | complete | Focused regressions and disposition of F-01 through F-12 |
| M7.2 Definitions, registry, dependencies and identity | Codex | complete | Definition, parameter, code-hash and graph tests |
| M7.3 Parquet/DuckDB storage, cache and PIT provenance | Codex | complete | Real temporary databases/files and bundle v3 |
| M7.4 Evaluation and CLI | Codex | complete | Hand calculations and CLI smoke tests |
| M7.5 Move nine indicators into managed factors | Codex | complete | Exact matches for 560 Top1 and 200 Top10 selection rows |
| M7.6 Ten-year runs, replay, report and scoped review | Codex | complete | Zero provider calls, equivalent economic outputs and passing review |

- [x] Correct each reported defect or document why part of it remains. Use canonical content hashes for definitions and materializations. A change in code or parameters changes the definition.
- [x] Store factor values in Parquet. Reject damaged files or inconsistent provenance. Identify the input snapshot, definition, universe, event range and output hash for each materialization.
- [x] Check IC, RankIC, quantile returns, turnover and related metrics against hand calculations. Obtain both atomic and composite scores through the factor API.
- [x] Compare all 20 selection dates and economic results for each strategy against its baseline. Locate and explain differences.
- [x] Run ten real years through Rust and generate HTML. Replay the cache with an invalid token and zero provider calls.
- [x] Pass Python, Rust and packaging checks and one scoped independent review. Update the architecture documentation and SVG, module instructions, strategy README, and factor storage, CLI and E2E instructions.

## Decisions

Python owns computation, management and evaluation of factors. Rust receives exact signals and owns trading rules, execution and accounting. The user chose real end-to-end replay as the main acceptance check for research strategy migration. Public interfaces, correctness fixes and factor infrastructure still used test-driven development.

Reuse the real ten-year cache. `ResearchDataSource` returns evidence for the immutable Parquet object found in the cache. The factor manager receives a candidate input snapshot without depending on a particular cache implementation.

Keep all legacy selection rows identical. Fixing F-12 can change older stored results. Use a replay of the *old request on the corrected runtime* as the economic reference. Restoring signal-ID ordering merely to match an old digest would restore the allocation bug.

## Historical results

- The factor root held 11 definitions, 220 materializations and 220 factor Parquet files. Rebuilding definitions from source reproduced the catalog hashes.
- All-industry Top1 reproduced all 560 selection rows over 20 dates. NAV, fills, rejections and final positions matched replay of the old request on the corrected runtime. Total return was 108.3896% and ending NAV was 1,041,947.75675909 CNY.
- Ten industries × Top1 reproduced all 200 rows over 20 dates and matched the corrected economic reference. Total return was 47.1649% and ending NAV was 735,824.26104058 CNY.
- Each variant's final and replay directories contained 540 byte-identical files. Both used zero provider calls. Each v3 bundle included 41 snapshots, 11 definitions, 220 materializations, 220 factor objects, and the summary and Markdown report.
- The review found three High issues in the status fallback, parent dependencies and label alignment. The same reviewer checked the fixes and found no remaining Critical or High issues in scope.

## Verification evidence

1. Regression tests first reproduced mixed signal times, missing limits, too few industries, definition/graph failures, materialization cache corruption, forged parent values, label timing/alignment errors and target allocation changes caused by signal IDs.
2. The live stock and ETF end-to-end tests reported `2 passed`. A separate ETF test rejected missing status when no explicit limit rule was available. The final Python suite with real credentials reported `164 passed`, zero skipped. No token literal appeared in the scan. The external credential file had mode 0600.
3. Ruff, strict mypy (55 source files), Cargo fmt, Clippy, workspace tests and doc-tests passed. Wheel, sdist and Cargo workspace packaging passed. In a clean virtual environment the wheel imported `trademaster.factors` and the stable strategy package, and `tm-factor list` returned 11 definitions.
4. Factor CLI describe/graph smoke tests passed. IC, RankIC, quantile returns, spread and turnover matched hand calculations and could be persisted.
5. No local documentation link was missing. Strategy/factor CLI help matched README parameters. Structural SVG and rsvg checks passed. The complete 4000px diagram and crops of the title, research, output and legend received a visual check.

## Changed files

- `specs/INDEX.md`
- `specs/20260823-m7-factor-management-strategy-refactor.md`
- `AGENTS.md`
- `docs/factor-management.md`
- `docs/architecture/overview.md`
- `docs/architecture/trademaster-v1-architecture.svg`
- `python/trademaster/data/research.py`
- `python/trademaster/data/__init__.py`
- `python/trademaster/factors/`
- `python/trademaster/e2e/__init__.py`
- `python/trademaster/e2e/real_tushare.py`
- `python/trademaster/strategies/AGENTS.md`
- `python/trademaster/strategies/industry_fundamental/`
- `python/trademaster/strategies/industry_fundamental_top5/`
- `crates/tm-engine/src/lib.rs`
- `crates/tm-engine/tests/scheduled_strategy.rs`
- `tests/test_factor_management.py`
- `tests/test_factor_evaluation.py`
- `tests/test_factor_cli.py`
- `tests/test_fundamental_managed_factors.py`
- `tests/test_industry_fundamental_top5.py`
- `tests/test_fundamental_strategy_data_cache.py`
- `tests/test_e2e_orchestrator.py`
- `tests/test_real_tushare_e2e.py`
- `pyproject.toml`
- `artifacts/strategies/industry-fundamental-managed-top1-final-v2{,-replay}/`
- `artifacts/strategies/industry-fundamental-managed-top10-industries-top1-final-v2{,-replay}/`

## Open questions

None. The default migration retained both strategies' economic conventions and the real data cache.
