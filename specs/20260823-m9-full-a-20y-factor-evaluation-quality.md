# M9: Twenty-Year All-A-Share Factor Research Pipeline and Quality Audit

**Status:** Completed | **Date:** 2026-08-23 | **Scope:** Survivorship-free 20-year A-share acquisition, dense PIT panels, streaming DuckDB factor evaluation, and cross-system audit (`trademaster.research`, `tm-research-full-a`)

## Motivation and Data Scope

Milestone M9 constructs a survivorship-free, content-addressed research dataset spanning twenty years of Chinese A-shares (`2006-08-23` through `2026-08-21`, resolved from the Tushare Pro calendar at plan creation) across all CNY stocks ever listed in Shanghai (`SSE`), Shenzhen (`SZSE`), and Beijing (`BSE`)—including subsequently delisted issuers—and evaluates both M8 public technical/size factors and M7 fundamental factors across the complete universe. Following initial panel evaluation, three independent read-only audits (architecture, engineering, and quantitative/PIT methodology) reviewed the entire repository, driving targeted fixes and a full clean rebuild of the panels and reports.

Intraday ticks, order books, news/research text, derivatives, unlicensed proprietary flow feeds, and analyst consensus forecasts remain outside scope.

| Provider Endpoint / Dataset | Research Coverage Rules |
|---|---|
| `stock_basic` | All `L/D/P/G` listing statuses with historical `list_date` and `delist_date` membership bounds. |
| Trading calendars (`trade_cal`) | `SSE` and `SZSE` sessions (4,860 sessions). Because Tushare returned zero rows for an independent `BSE` calendar, `BSE` sessions from `2021-11-15` onward map deterministically to `SSE` sessions and are flagged as derived coverage. |
| Market bars & adjustments | Whole-market session slices of `daily` (OHLCV, `amount`, `pre_close`) joined to same-session `adj_factor`. |
| Valuation & turnover (`daily_basic`) | Turnover, total and free-float market capitalization, `PE`/`PB`/`PS`, and dividend yield validated against sparse historical coverage. |
| Trading states (`stk_limit`, `suspend_d`, `stock_st`) | Daily price limits, trading suspensions, and ST status within available provider history (recording pre-history empty responses as source-history gaps rather than transport failures). |
| Financial statements | Per-stock histories of `balancesheet`, `cashflow`, `income`, and `fina_indicator` restricted to original filings (`update_flag = 0`) available at the next trading session after `ann_date`. |
| Benchmarks & industries | CSI All Share, CSI 300, SSE 50, and STAR 50 index series, plus `SW2021` historical `in_date`/`out_date` intervals (failing closed on historical gaps rather than backfilling current static classifications). |

## Architecture, Causality, and Evaluation Diagnostics

### Resumable Acquisition and PIT Panel Construction

- **Resumable plan & task leases (`FullAResearchConfig`, `DownloadPlanManifest`, `FullAResearchDownloader`):** Market/status endpoints are planned as whole-market daily slices (so extending the end date appends only new sessions), while financial statements are planned per stock under `data/research/full-a-20y`. DuckDB tracks atomic task claims/leases, verified-empty outcomes, typed permission/history blockers, and Parquet SHA-256 digests (`tm-research-full-a plan|download|status|verify|reconcile-universe|evaluate|report`).
- **Active historical grid (`FactorResearchPanelBuilder`):** Resolves active membership via `list_date <= session <= delist_date`, preserves suspended sessions on a dense grid, computes backward-adjusted (`HFQ = raw_price * adj_factor`) rolling windows across all daily sessions before sampling month-end cross-sections, enforces finite-value (`isfinite`) guards end to end, and aligns close-derived signals on session `T` to forward return labels starting no earlier than an eligible `T+1` session across horizons `1, 5, 20, 60, 120, 252`.

### Streaming DuckDB Factor Evaluation (`ExtendedFactorEvaluator`)

To evaluate ~15.9M panel rows without materializing Python row lists (`to_pylist()`), DuckDB validates primary keys and computes cross-sectional metrics per observation date while Python aggregates event-level summaries into content-addressed Parquet, JSON, Markdown, and standalone Plotly HTML bundles:

- **Information coefficient & stability:** Coverage ratios (factor, label, joint, and untradable-entry breakdowns), Pearson IC, Spearman RankIC, ICIR, RankICIR, positive-IC hit rate, ordinary and Newey-West HAC t-statistics, skewness, kurtosis, annual/regime slices, cross-sectional log-size correlation, within-industry RankIC, and cross-sectional **size-neutral RankIC**.
- **Ex-ante quantile portfolios & non-overlapping discipline:** Stocks are assigned to quantiles strictly ex-ante on the observation date *before* joining future return labels. Compounding overlapping multi-month forward returns double-counts intervening sessions and distorts volatility, Sharpe, and drawdown; the evaluator inspects the actual minimum observation spacing in the panel (14 sessions for month-end sampling) and suppresses cumulative spread, Sharpe, drawdown, and recovery metrics as `N/A` whenever the horizon exceeds minimum spacing (`20/60/120/252` on monthly panels, while retaining `1` and `5`).
- **Turnover & capacity:** Reports top-quantile turnover, PIT size-tercile exposures of the top portfolio, and a 1%-of-traded-value CNY capacity proxy honoring factor sign (`direction`). Execution-state breakdowns requiring full Rust order matching (such as board-specific price-limit fills) remain explicitly marked as out-of-scope for pure factor panels.

## Milestones and Cross-System Review Dispositions

| Subtask | Status | Verification Summary |
|---|---|---|
| M9.1: Resumable download planner, catalog leases, and coverage schema | Completed | Initial plan covering 5,843 instruments, 4,860 sessions, and 52,551 tasks |
| M9.2: Cache-first `FullAResearchDownloader` and `tm-research-full-a` CLI | Completed | 8 focused suites; bootstrap cache probe made `0` provider calls |
| M9.3: 20-year Tushare Pro acquisition, reconciliation, and audit | Completed | 5,846 reconciled instruments; 52,563/52,563 verified tasks (`provider_calls=0` on replay) |
| M9.4: Dense PIT market/fundamental panels and streaming DuckDB evaluator | Completed | Parity verified against in-memory Python oracle across fixed fixtures and 10 random seeds |
| M9.5: Content-addressed full-universe HTML/Markdown factor reports | Completed | Standalone reports with embedded Plotly and manifest tamper checks |
| M9.6: Three-domain independent review (architecture, engineering, quant/PIT) | Completed | 28 findings documented and triaged in [M9 Quality Review](../docs/reviews/20260823-m9-full-a-quality-review.md) |
| M9.7: Defect remediation, full panel/report rebuild, and release verification | Completed | Rebuilt public & fundamental bundles; 248 Python tests and all Rust/wheel checks passed |

### Consolidated Dispositions Across All 28 Audit Findings

| Audit Finding | Severity | Disposition | Resolution Implemented |
|---|---|---|
| Weak binding across download task, request, evidence, and Parquet | High | Accepted & Fixed | Bound canonical request digests, Parquet metadata, and typed snapshot checks across all 52,563 objects. |
| Same-day financial `ann_date` availability and undated restatements | High | Accepted & Fixed | Enforced next-trading-session announcement availability and `update_flag = 0` original filings; rebuilt fundamental panel. |
| Disagreement between Python managed rolling windows and SQL dense windows | High | Accepted & Fixed | Established exact row-by-row parity across all 11 public factors (including nulls and suspensions) between Python and DuckDB. |
| Non-finite (`NaN`/`Inf`) prices, adjustments, or returns treated as valid | High | Accepted & Fixed | Enforced `isfinite` validation end to end with adversarial `NaN`/`Inf` regression suites. |
| Quantile assignment conditioned on future return-label availability | High | Accepted & Fixed | Assigned quantiles ex-ante on the factor observation date with per-horizon invalid-label accounting. |
| Compounding overlapping forward horizons distorted Sharpe and drawdown | High | Accepted & Fixed | Computed actual minimum event spacing and reported `N/A` for overlapping cumulative spread statistics. |
| Capacity proxy multiplied by `1,000` twice and ignored factor `direction` | High / Medium | Accepted & Fixed | Corrected Tushare `amount` (`CNY 1,000`) conversion to CNY, honored `direction`, and bound config hashes. |
| Static legacy industry backfill and missing `.BJ` (`BSE`) venue handling | High / Medium | Accepted & Fixed | Enforced historical `in_date`/`out_date` intervals and `.BJ` venue mapping; withdrew prior Top-1/Top-10 PIT claims (`artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md` halted cleanly at `2016-11-07 / 000009.SZ`). |
| Report v2 identity omitted upstream market/fundamental panel manifests | Medium | Accepted & Fixed | Upgraded report identity (v3) and `verify-report` to bind and recheck upstream panel manifests. |
| Missing CLI path for fundamental panel evaluation | Medium | Accepted & Fixed | Added `tm-research-full-a evaluate --factor-manifest-kind fundamental`. |
| Path traversal risk in legacy strategy cache identifiers | Medium | Accepted & Fixed | Enforced strict identifier validation and directory root-containment checks. |
| Near-quadratic repeated scans during legacy market assembly | Medium | Accepted & Fixed | Replaced repeated table scans with a single indexed grouping pass. |
| Reconciliation CLI rejected idempotent re-registration of identical plans | Medium | Accepted & Fixed | Made same-plan `reconcile-universe` idempotent (`inherited_completed_tasks=0`, `provider_calls=0`). |
| Provider-neutral store protocol resided in compatibility strategy module | Medium | Partially Deferred | Promoted `ResearchDataSource` protocol to `trademaster.data`; legacy cache layout retained for compatibility. |
| Raw-price corporate-action and cash-dividend accounting in Rust | High | Deferred | Relabeled legacy QFQ strategy reports as total-return approximations (cash-dividend accounting subsequently implemented in [Code Architecture Refactoring](20261104-code-architecture-refactor.md) and [Broad ETF Benchmark](20261105-broad-etf-benchmark.md)). |
| Overseas (`HK`/`US`) market research watermarks & execution-state slices | Medium | Partially Deferred | Added size-neutral RankIC and size-tercile exposures; deferred cross-market policies and Rust-dependent board/limit slices to later specs. |

## Empirical Acquisition and Rebuilt Evaluation Results

### Twenty-Year Data Acquisition (`data/research/full-a-20y`, ~2.7 GB)

- **Final download plan (`6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`):** `stock_basic` returned 5,888 listings (`L=5,549`, `D=339`, `P=0`, `G=0`), yielding 5,843 stocks active during `2006-08-23..2026-08-21` (excluding 45 delisted earlier) plus 3 historical codes present in `daily` bars without current `stock_basic` rows (**5,846 reconciled instruments**; 8 special/delisted codes without Tushare bar history recorded as source-history blockers). All **52,563 tasks** completed (`52,439` Parquet-acquired, `124` verified-empty pre-history slices, `0` failed/blocked, `0` duplicate `ts_code + trade_date` keys).
- **Row counts & anti-join audit:** Across 4,860 sessions, `daily` stored `15,461,567` rows (5,838 codes), `adj_factor` `16,204,207` rows (5,842 codes), and `daily_basic` `15,370,739` rows (5,838 codes). `daily ANTI adj_factor = 24` rows came exclusively from pre-listing `600018.SH` (`2006-08-23..2006-09-25`, excluded by active `list_date`), while `daily ANTI daily_basic = 90,829` rows reflected pre-`2021-11-15` Beijing/NEEQ trading plus 13 Shenzhen rows. Financial tables contained zero missing `ann_date` values across `balancesheet` (`436,266` rows / 5,842 stocks), `cashflow` (`388,256` / 5,841), `fina_indicator` (`563,732` / 5,845), and `income` (`383,254` / 5,842).

### Rebuilt Public-Factor Panel (11 Factors)

- **Digests:** Panel manifest `9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89` · Materialization `2279319feb5a1bd9864407524bcab73051fe98097c82f09501189de28e2ef9b0` · Report ID `413067aee36089e6f3295c5165e1f069217a71c481436295d8411512ea29b927` · Report manifest `5d0a8013a0e2086e6dcb10af1ee84499c75034048f75a7bcb6ed82eb109407f6`.
- **Coverage & RankIC:** Spans `15,882,389` dense panel rows, `5,846` instruments, and `789,359` month-end observation keys (`240` valid monthly cross-sections; `8,154,780 / 8,682,949` valid factor rows; `4,354,957 / 4,736,154` valid labels; `15,370,049` active bar rows; `473,924` explicit suspensions + `52,523` adjustment-continuity suspensions + `4,963` unexplained bar gaps; `0` active bar–adjustment gaps; `20` bar–`daily_basic` gaps). Produced `15,562` event metrics, `77,810` quantile metrics, and `55` pairwise correlations (~5.8 MB standalone HTML).
- **20-session findings:** Raw vs. size-neutral RankIC was `0.0546 / 0.0562` (`Alpha046`), `0.0450 / 0.0466` (`Alpha034`), `-0.0690 / -0.0675` (`Alpha088`), and `-0.0668 / 0.0074` (Huatai `Size`, confirming that market-cap tilt explains almost all raw Size RankIC, with 100% small-cap tercile concentration under `direction=-1`, ~`CNY 4.75M` 1%-ADV capacity proxy, and `~0.11` top turnover vs. `0.74–0.81` for short-horizon technical factors). Per-event rank correlations highlighted collinear pairs (`Alpha031`/`Alpha034`: `-1.0000`; `Alpha034`/`Alpha046`: `0.9583`; `Alpha058`/`Alpha088`: `0.5575`).

### Rebuilt Fundamental Panel (9 Atomic Factors + Global Composite)

- **Digests:** Panel manifest `2f71fc7d9e081d7368ddbd0af8d0adf076b39e6a24ae03c9618afa29a1f2b2a2` · Materialization `5acb27c7e6fe0157763a42e6878d28f09cf5fde6eb90292fab77f7e57b0209e9` · Report ID `eddc6bef5097db84e1cb7d337d401bf241ec478536a0bdcf208aa0d52fb9a761` · Report manifest `93c82636da316a441ef67743e8966cee1ff7b578f575614a410f6334733542d2`.
- **Coverage & PIT impact:** Spans `7,893,590` factor rows across the nine atomic indicators and global composite (while the within-industry composite remained explicitly blocked by `15,013` historical `SW2021` interval gaps). Enforcing next-session announcement availability and `update_flag = 0` retained `776,656` financial observations and altered `613,223` previously valid global-composite scores, reducing 20-session raw global-composite RankIC from `0.0315` to `0.0279` (`0.0459` size-neutral), alongside book yield `0.0555 / 0.0590`, dividend yield `0.0382 / 0.0499`, and earnings yield `0.0335 / 0.0578` with low monthly top-quantile turnover (`0.04–0.16`).

## Final Verification Record

- **Cache & report integrity:** Full-plan live-token probe executed with `provider_calls=0`; `tm-research-full-a verify` validated request hashes, Parquet metadata, SHA-256 digests, and row counts across all 52,563 objects; `verify-report` from an isolated wheel installation verified all six artifacts and upstream manifests in both reports.
- **Test & toolchain checks:** `uv run pytest -q` passed **248 tests in `95.97s`** (with live Tushare token), alongside repository-wide Ruff lint, Ruff format checks on all 27 M9 files, `compileall`, strict `mypy` (44 source files), `cargo fmt --check`, `cargo clippy --workspace -D warnings`, Rust workspace tests, `uv build`, and static fence/link checks across all modified documentation.

## Changed Files

- `specs/INDEX.md`, `specs/20260823-m9-full-a-20y-factor-evaluation-quality.md`, `pyproject.toml`
- `python/trademaster/research/` (`AGENTS.md`, `__init__.py`, `__main__.py`, `full_a.py`, `factor_evaluation.py`, `factor_evaluation_duckdb.py`, `factor_report.py`, `full_a_panel.py`, `full_a_fundamental.py`, `tushare.py`)
- `python/trademaster/data/research.py`
- `python/trademaster/strategies/industry_fundamental_top5/` (`AGENTS.md`, `README.md`, `__main__.py`, `data.py`, `real.py`)
- `tests/test_full_a_research.py`, `tests/test_factor_evaluation_v2.py`, `tests/test_factor_evaluation_duckdb.py`, `tests/test_factor_report_v2.py`, `tests/test_full_a_panel.py`, `tests/test_full_a_fundamental.py`, `tests/test_research_tushare_client.py`, `tests/test_fundamental_strategy_data_cache.py`, `tests/test_industry_fundamental_top5.py`
- `docs/full-a-factor-research.md`, `docs/factor-management.md`, `docs/reviews/20260823-m9-full-a-quality-review.md`
- `artifacts/strategies/REVIEW_NOTICE.md`, `artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md`, `artifacts/research/full-a-20y/FINAL_REPORT.md`
