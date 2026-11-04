# M9 · Twenty years of A-share factor research and code review

- Status: complete
- Owner: Codex
- Updated: 2026-08-23

## Goal and data scope

Build a content-addressed research dataset from 2006-08-23 through the most recent completed trading session. Include CNY stocks ever listed in Shanghai, Shenzhen and Beijing during the period, including stocks since delisted. Use that historical universe to compute point-in-time (PIT) factor exposures, forward-return labels, and full-universe quality reports. After the data and evaluation work, run one independent review each of architecture, engineering, and factor/strategy behavior. Fix findings supported by evidence.

“All A-shares” describes the coverage required for factor research. News, broker research text, ticks and limit-order books, derivatives, analyst consensus, and unlicensed proprietary flow data are outside scope.

| Source | Coverage |
| --- | --- |
| `stock_basic` | All L/D/P/G statuses, with listing and delisting dates for historical membership. |
| Trading calendars | SSE and SZSE sessions. Tushare returned no independent BSE calendar. From the BSE launch, use a versioned mapping to SSE sessions and mark its coverage as derived. |
| Market bars | Stock-level daily OHLCV, amount, pre_close and same-day `adj_factor`. |
| `daily_basic` | Turnover, total and free-float market cap, PE/PB/PS, and dividend yield. |
| `stk_limit`, `suspend_d`, `stock_st` | Trading states within the history available from the provider. |
| Financials | `fina_indicator`, announcement dates, reporting periods and revision fields required by managed fundamentals. |
| Benchmarks and industries | CSI All Share, CSI 300, SSE 50 and STAR 50 among the benchmarks. Report actual coverage of historical SW2021 membership. A recent static classification cannot fill a historical gap. |

## Time, identity and coverage

- Fix the request start at `2006-08-23`. Resolve the end from the Tushare calendar to the latest completed session at runtime and persist it in the immutable run manifest.
- The default data root is `data/research/full-a-20y` and can be overridden. Read the Tushare token through the environment. Only the shell may load a local token file. Keep credentials out of logs, manifests and Parquet.
- Store raw responses and derived data in Parquet. DuckDB catalogs requests, coverage and task state, and supports discovery. A stable content hash identifies each resumable download plan. Skip verified requests on replay and resume from the first unfinished task after failure.
- Resolve the active historical universe by `list_date <= session <= delist_date`. Preserve suspended sessions in the grid and account for missing bars using suspension or status evidence. Join tradable daily observations to same-session adjustment factors and record any gaps.
- Validate sparse endpoints such as `daily_basic` against their real semantics. Financial records must satisfy `ann_date/known_at <= factor event`. Later revisions cannot backfill earlier events.
- Distinguish successful acquisitions, insufficient permissions, unavailable source history and genuine gaps in coverage. A successful provider response alone cannot establish coverage.

## Factors, labels and metrics

The first public-factor set comprises Huatai Size and ten unambiguous GTJA formulas. Evaluate the seven existing `implemented_variant` definitions and other fundamental atomic factors separately once typed PIT inputs are available. Keep Huatai's original definitions distinct from variants. GTJA definitions use backward-adjusted (HFQ) prices computed as `raw_price * adj_factor`. Execution market data uses raw prices. A close-derived signal forms after the close on T. Its forward return starts no earlier than an eligible open or close on T+1. Evaluation identity binds the selected horizons, which default to 1, 5, 20, 60, 120 and 252 trading sessions.

The target evaluation retained coverage, Pearson IC, RankIC, ICIR, RankICIR, frequency of positive IC, quantile returns, top-minus-bottom spread and top-group turnover. Additional diagnostics in scope were:

- Valid-date share, distribution of cross-sectional sample sizes, and shares of missing, infinite and constant cross sections.
- IC t-statistics including Newey-West, IC skew and kurtosis, stability by year and market regime.
- Quantile monotonicity, spread win rate, volatility and Sharpe, cumulative spread, maximum drawdown and recovery time.
- Within-industry RankIC, industry exposure, Size correlations and market-cap bucket exposures.
- Robustness by listing board, ST/suspension/price-limit status, listing age and market cap. Quantile turnover, tradable coverage, participation as a share of traded value, capacity proxies and cost sensitivity.
- Raw, industry-neutral and size-neutral views where historical classifications and PIT evidence support them. The review below records the remaining blocked breakdowns.

Persist immutable Parquet/JSON and self-contained HTML/Markdown. Each output binds the factor definition, data snapshot, universe policy, label alignment and complete configuration hash.

## Proposed public interfaces at the start of M9

| Interface | Responsibility |
| --- | --- |
| `FullAResearchConfig` | Date range, data root, endpoints, batching, rate limits, retries, universe and evaluation settings. |
| `DownloadTask` / `DownloadPlanManifest` | Stable task identity, endpoint, parameters, expected coverage and state. |
| `FullAResearchDownloader.plan/status/run/verify` | Plan, resume, acquire and audit coverage. |
| `FactorResearchPanelBuilder` | Build the PIT universe, HFQ market and fundamental panels, and labels from cached data. |
| `ExtendedFactorEvaluator` or an extension to the existing evaluator | Add statistics and group diagnostics while preserving v1 artifact loading. |

CLI proposal: `tm-research-full-a plan|download|status|verify|evaluate|report`.

## Milestones

| Milestone | Owner | Status | Evidence |
| --- | --- | --- | --- |
| M9.1 Data scope, task planning, status and coverage | Codex | completed | Initial plan covered 5,843 instruments, 4,860 sessions and 52,551 tasks. |
| M9.2 Cache-first downloader and CLI | Codex | completed | 8 focused tests. Bootstrap cache probe made 0 provider calls. |
| M9.3 Acquire 20 years of data and audit coverage | Codex | completed | 52,563/52,563 tasks, zero-call probe and full verification. |
| M9.4 Full-universe panels, labels and extended evaluation | Codex | completed | Real evaluations of 11 public factors and 10 fundamental factors. |
| M9.5 Full-universe reports | Codex | completed | Two verified standalone reports. |
| M9.6 Independent code review | Fresh reviewers | completed | Three reviews with 28 findings and dispositions. |
| M9.7 Fix evidenced problems and run project checks | Codex | completed | Rebuilt panels and reports, 248 Python tests and Rust/package checks. |

## Acceptance criteria

- [x] Include both listed and delisted A-shares in the 20-year historical universe, with reproducible start and end resolution.
- [x] Every required endpoint has a deterministic plan, resumable state, content hash and coverage report.
- [x] Query acquisition, verified-empty outcomes and gaps separately for bars, adjustments, daily indicators, financials, status, benchmarks and industries.
- [x] A cache probe after the real download makes no provider requests.
- [x] Every executable factor produces a full-universe evaluation artifact. Industry factors with insufficient inputs have a machine-readable blocker.
- [x] The evaluator covers return statistics, stability, Size neutrality, industry and market-cap exposure, turnover and capacity. Execution-state breakdowns that require Rust are explicitly blocked.
- [x] Reports explain historical-universe construction, PIT availability, industry-history gaps, microcap exposure and cost sensitivity.
- [x] At least three fresh-context reviewers independently cover architecture, engineering and factor/strategy behavior.
- [x] Record accept/reject/defer and evidence for every finding. Implement and regression-test accepted fixes within their recorded scope.
- [x] Python, Rust, packaging, real-cache and E2E checks pass, and the code, AGENTS guides, docs and specifications agree.

## Decisions and starting evidence

Fetch `daily/adj_factor/daily_basic` and trading status as whole-market slices by session, following Tushare's full-market guidance. Extending the end date then adds only new session tasks. Fetch financial reports over long date ranges per stock. Where the provider cannot establish coverage, retain a blocker.

M8 supplied 460 candidates and 22 managed definitions, including 11 executable original public factors. The ten-year strategy cache held daily bars for the union of 446 previously selected stocks. Its engineering results cannot establish full-universe coverage or performance. Complete the new data and evaluation before one review round. At review time, the first public and fundamental reports became diagnostic artifacts because of issues with PIT timing, windows and evaluation statistics. The later rebuild and its results are recorded below.

## Test-driven verification during development

All entries in this section were recorded on 2026-08-23. Test counts refer to the particular stage and suite.

| Area | Failure that established the requirement | Implemented behavior and evidence |
| --- | --- | --- |
| Planner, store, downloader and CLI | `uv run pytest -q tests/test_full_a_research.py` initially failed at collection because `trademaster.research` did not exist. Tests specified a survivorship-aware universe, deterministic plan, content hashes, DuckDB resume, zero provider calls after completion, and rejection of corrupt plans. | `8 passed`, plus Ruff and strict Mypy. Source evidence must exist. Recheck Parquet hashes, rows and fields, and verify all objects in a completed plan. |
| Forward labels and factor evaluation | `tests/test_factor_evaluation_v2.py` failed at collection before the module existed. It specified next-session close and exact horizons, explicit untradable-entry reasons, the PIT universe denominator, factor/label/joint coverage, IC/RankIC, HAC, quantile monotonicity, spread, Size, capacity and full-key validation. | Four hand-calculated tests passed. Dense-session labels, invalid reasons, ordinary/Newey-West t-statistics, quantiles, spread, turnover, drawdown, within-industry RankIC, log-size correlation and a 1% traded-value capacity proxy form the small-table oracle. The 20-year production path uses DuckDB partitions and never calls `to_pylist()` on the full panel. |
| Content-addressed factor reports | Required replayable report contents and tamper detection. | Report store and adjacent evaluation tests: `11 passed`. Ruff, Mypy and compileall passed. Content-address the summary, event, quantile, correlation, HTML, Markdown and manifest. Represent non-finite values as null/N/A. Embed Plotly and reject modified objects or manifests. |
| Market panel | Required a batch path from completed-task evidence to a dense session grid. | DuckDB/Arrow builder and adjacent tests: `20 passed`. Full Python suite at this stage: `195 passed, 2 credential skips`. Ruff and strict Mypy passed. Start BSE coverage on 2021-11-15, preserve suspended and missing-bar rows, compute 11 factors on full windows before selecting month-ends, and generate 1/5/20/60/120/252 labels. Manifest binds source, definition, config, code and output hashes. |
| Streaming evaluator | Required agreement with the in-memory oracle on a fixed fixture and 10 random seeds. | `17 passed`, plus Ruff, Mypy and compileall. DuckDB checks keys, duplicates and bounds, computes event IC/RankIC, quantiles, industry/Size, turnover, capacity and all pair correlations. Python reads only event aggregates. |
| Fundamental PIT panel | Required a full-universe month-end builder and reproducible as-of selection. | `18 passed` focused and adjacent. Full Python then: `205 passed, 2 credential skips`. Ruff and Mypy passed. The initial version selected a revision deterministically under `ann_date<=event` and `end_date<=event`, produced nine atomic factors and the global composite, and agreed with managed factors within 1e-12. Missing industry intervals produce a machine-readable blocker. Changing a future announcement leaves earlier exposures unchanged. The subsequent review tightened date-only and revision rules. |
| Report identity, paths and venue | Missing input-manifest binding: `4 failed`. Legacy cache path traversal: `6 failed, 1 passed`. Industry mapping: `2 failed`. The new `.BJ` venue test failed at collection. | Manifest binding: `4 passed`. Identifier and root-containment checks: `8 passed`. Historical `in_date/out_date` resolution: `16 passed`. BSE venue: `1 passed`. |
| Download identity and leases | `6 failed, 9 passed`. Wrong request evidence/metadata was accepted. Atomic claims, leases, typed blockers and acquisition outcomes were missing. | Focused, panel and fundamental tests: `24 passed`. Two concurrent connections produced one winning task claimant. |
| PIT, finite values and parity | Three failures reproduced uncounted NaN adjustment gaps, missing availability sessions for announcements, and unknown revisions changing historical values. | `8 passed` focused, `40 passed` across full-A, panel, fundamental and evaluator tests. All 11 factors agreed row by row between Python and DuckDB, including nulls and suspensions. |
| Provider errors | New typed exceptions initially failed to import. One additional test exposed incorrect network-error classification. | `11 passed` focused, `27 passed` with full-A and client tests. Insufficient permission or points blocks immediately. Authentication, invalid and unknown failures stop. Only explicit transient errors receive bounded backoff. Wrapped failures omit raw provider messages. |
| Evaluation corrections | Direction initially raised `extra_forbidden`. The old overlapping 60-session spread was `0.01833`. A pooled pairwise Pearson value of `0.4186` should have been 0 across events. Capacity multiplied by 1000 twice. | Assign stocks to quantiles on the observation date, before joining future returns. The oracle and DuckDB agree on per-horizon coverage/invalid reasons, definition direction, CNY capacity, event-level correlation and N/A for overlapping statistics. Focused and report tests: `20 passed`. |
| Size diagnostics | `size_neutral_rank_ic` was missing. Then the old 13-column DuckDB output failed against the new 17-column schema. | Added cross-sectional size-residual RankIC and PIT size-tercile exposures of the top portfolio: `23 passed`. Use the actual minimum spacing in the dense panel rather than 252/12. A spacing=18 fixture retains the h5 spread and reports h20 as N/A. Evaluator/streaming/report: `26 passed`. |
| Reconciliation idempotence | Recomputing the real target plan yielded the same SHA, but the CLI attempted inheritance and raised `download plan inheritance requires different plans`. The regression test initially failed to import. | Same-plan registration: `1 passed`. The original command returned the same plan, `inherited_completed_tasks=0`, `provider_calls=0` and 52,563 completed tasks. |

## Review dispositions

Each reviewer made one independent read-only pass. Findings with the same cause share a fix. This table records the dispositions at triage. Later sections give the completed runs and checks. The [full review](../docs/reviews/20260823-m9-full-a-quality-review.md) retains all 28 findings and their IDs.

| Finding | Severity | Disposition | Action at triage |
| --- | --- | --- | --- |
| Weak binding across task, request, evidence and Parquet | High | accept/complete | Canonical request/metadata, typed snapshot and regression coverage implemented. |
| Same-day financial availability and undated revisions backfilled history | High | accept/complete | Next eligible session and original-only revisions. Real rebuild underway. |
| Managed Python and dense-session SQL windows disagreed | High | accept/complete | All 11 full formulas covered by dense-session parity tests. |
| Non-finite prices, adjustments or returns could be valid | High | accept/complete | End-to-end `isfinite` rule plus NaN/Inf regression tests. |
| Quantile membership depended on future label availability | High | accept/complete | Ex-ante groups with per-horizon coverage and invalid reasons. |
| Compounding overlapping horizons distorted drawdown and Sharpe | High | accept/complete | Use the actual minimum spacing. Suppress overlapping spread statistics as N/A. |
| Capacity multiplied by 1000 twice and ignored direction | High/Medium | accept/complete | CNY unit checks, definition direction and config identity. |
| Report identity omitted panel/fundamental manifests | Medium | accept | Report identity v3 binds input manifests. Real rebuild pending at triage. |
| Missing formal fundamental-report CLI | Medium | accept | Added `evaluate --factor-manifest-kind fundamental`. E2E pending at triage. |
| Legacy cache path traversal | Medium | accept | Verified identifier and root-containment checks. |
| Static legacy industry backfill and incorrect Beijing venue | High/Medium | accept/complete | Fixed PIT intervals and BSE mapping. The formal probe stopped at a real coverage gap. Prior conclusions withdrawn. |
| Legacy market assembly made nearly quadratic scans | Medium | accept | Replaced repeated scans with one grouping pass. Regression and performance checks pending at triage. |
| Provider-neutral store still owned by compatibility strategy | Medium | accept/defer | Moved the protocol to `trademaster.data`. Store migration needs a compatibility milestone. |
| No Rust events for raw-price corporate actions or dividends | High | accept/defer | Relabeled old reports as continuous total-return-price approximations. An exact account needs its own engine contract and TDD. |
| Hong Kong/US market policy and publication watermark | Medium | accept/defer | Recorded for a cross-market milestone. M9 uses the A-share policy. |
| Incomplete size-neutral, bucket and state robustness checks | Medium | accept/partial | Added size-neutral RankIC and top-portfolio size-tercile exposures. ST, board, age and price-limit breakdowns remain blocked on schema and Rust support. |

The formal Top1 PIT probe stopped before selection and Rust execution at `2016-11-07 / 000009.SZ` because the historical industry interval was missing. Evidence: `artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md`. The old Top1/Top10 return artifacts remain diagnostic records. Their conclusions were withdrawn.

## Real download evidence

The final plan was `plan_sha256=6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`. The initial plan required nonempty `stk_limit` for 2006, before the provider had history, and stopped. The revised plan recorded empty dates as source-history coverage gaps and reverified objects before inheriting completed evidence from 14,586 tasks with identical hashes. The final download status distinguishes verified emptiness from proof of the historical cause.

| Checkpoint | Evidence |
| --- | --- |
| Bootstrap identities | `stock_basic`: L=5,549, D=339, P=0, G=0, total 5,888. The initial CNY Shanghai/Shenzhen/Beijing universe overlapping the period contained 5,843 stocks. It excluded 45 delisted before the interval. |
| Calendars | SSE and SZSE agreed on 4,860 trading sessions from 2006-08-23 to 2026-08-21. The BSE endpoint returned 0 rows. Applied the specified versioned SSE-derived policy. |
| Initial plan | 52,551 tasks. Bootstrap made eight real provider requests, including two pages for each of the two long calendars. `download --phase bootstrap` subsequently completed six tasks with zero provider calls. |
| Market phase | 14,586 completed tasks including six bootstrap tasks, zero failures. Repeating the phase gave `provider_calls=0`. |
| Identity reconciliation | Three codes had historical daily bars but no current `stock_basic` record. Record an inferred lifecycle explicitly from each code's first and last market sessions. Eight planned historical delisted or special codes had no Tushare daily history. These are recorded in reconciliation and source-history blockers. |
| Final plan after reconciliation | 5,846 instruments and 52,563 tasks. Reverified the objects and inherited 29,166 completed market, status and bootstrap tasks. A market plus status cache probe made zero provider calls. |

All three market tables cover 4,860 sessions across 2006-08-23 to 2026-08-21. There were zero duplicate `ts_code+trade_date` keys. `daily` held 15,461,567 rows across 5,838 codes, `adj_factor` held 16,204,207 across 5,842, and `daily_basic` held 15,370,739 across 5,838.

The anti-join `daily ANTI adj_factor=24` consisted entirely of the old 600018.SH series from 2006-08-23 to 09-25, before its official `stock_basic` listing date. The active PIT grid excludes it. For `daily ANTI daily_basic=90,829`, most unmatched rows came from the historical Beijing/NEEQ segment, with 13 Shenzhen rows. The panel starts BSE coverage at 2021-11-15 and reports the remaining gaps.

## Rebuilt public-factor panel and evaluation

- **Rebuilt public-factor panel manifest** `9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89`
- **Materialization** `2279319feb5a1bd9864407524bcab73051fe98097c82f09501189de28e2ef9b0`

The dense PIT panel covers 15,882,389 rows, 5,846 instruments and 789,359 month-end observation keys. Of 8,682,949 factor rows, 8,154,780 were valid. Of 4,736,154 labels, 4,354,957 were valid. There were 15,370,049 active bar rows, zero active bar–adjustment gaps and 20 bar–daily_basic gaps. Evidence identified 473,924 explicit suspensions. Same-day adjustment continuity indicated 52,523 more. Another 4,963 missing bars remained unexplained.

- **11-factor report ID** `413067aee36089e6f3295c5165e1f069217a71c481436295d8411512ea29b927`
- **Report manifest** `5d0a8013a0e2086e6dcb10af1ee84499c75034048f75a7bcb6ed82eb109407f6`

The report contains 15,562 event metrics, 77,810 quantile metrics and 55 factor-pair correlations. For the 20-session horizon, illustrative raw/size-neutral RankIC pairs were Alpha046 `0.0546/0.0562`, Alpha034 `0.0450/0.0466`, Alpha088 `-0.0690/-0.0675` and Size `-0.0668/0.0074`. Market-cap exposure accounted for almost all the raw Size effect. With direction=-1, every top-portfolio stock fell in the small-cap tercile. Its capacity proxy was about CNY 4.75 million, with top turnover of about 0.11. For the technical factors, top turnover was about 0.74–0.81.

Rank correlations computed per event were `-1.0000` for Alpha031/034, `0.9583` for Alpha034/046 and `0.5575` for Alpha058/088. Here `observation_count=240` counts valid months. The minimum month-end spacing was 14 sessions. The 20/60/120/252 horizons overlap, so their cumulative spread, Sharpe, drawdown and recovery are N/A. The 1/5-session non-overlapping spread diagnostics remain available. Verification checked hashes, schemas and row counts of all six objects. The standalone HTML was about 5.8 MB.

## Complete acquisition and fundamental evaluation

All 52,563 tasks completed, with 52,439 object-acquired and 124 verified-empty. Pending, running, failed and blocked task counts were all zero. With a real token, the complete-plan cache probe made zero requests. Full verification checked each task/evidence/Parquet request SHA, object hash, metadata and row count.

The data root `data/research/full-a-20y` occupied about 2.7 GB. No `ann_date` values were missing in the four financial datasets.

| Dataset | Rows | Instruments |
| --- | ---: | ---: |
| balancesheet | 436,266 | 5,842 |
| cashflow | 388,256 | 5,841 |
| fina_indicator | 563,732 | 5,845 |
| income | 383,254 | 5,842 |

- **Fundamental manifest** `2f71fc7d9e081d7368ddbd0af8d0adf076b39e6a24ae03c9618afa29a1f2b2a2`
- **Materialization** `5acb27c7e6fe0157763a42e6878d28f09cf5fde6eb90292fab77f7e57b0209e9`
- **Report ID** `eddc6bef5097db84e1cb7d337d401bf241ec478536a0bdcf208aa0d52fb9a761`
- **Report manifest** `93c82636da316a441ef67743e8966cee1ff7b578f575614a410f6334733542d2`

The fundamental panel contains 7,893,590 factor rows across nine atomic factors and the global composite. The industry composite remained blocked by 15,013 interval gaps. The 20-session raw/size-neutral RankIC pairs were book yield `0.0555/0.0590`, dividend `0.0382/0.0499`, earnings `0.0335/0.0578` and global composite `0.0279/0.0459`.

Next-session announcement availability and the original-only revision policy reduced observations with financial inputs to 776,656. Of the global composite values valid both before and after review, 613,223 changed. Its raw RankIC fell from 0.0315 to 0.0279. Top turnover for valid fundamentals ranged from about 0.04–0.16, well below the price/volume factors.

## Final verification at M9 close

- Full-plan cache probe with a real token: `provider_calls=0`. The download catalog recorded 52,563 completed, 52,439 object-acquired and 124 verified-empty. All other terminal counts were zero.
- `tm-research-full-a verify` checked request identity, Parquet metadata, object hashes and row counts for all 52,563 objects. The real `reconcile-universe` returned the same plan idempotently with `inherited_completed_tasks=0` and zero provider calls.
- Using the installed wheel, `verify-report` rechecked the input manifests and six objects in each public/fundamental report.
- `uv run pytest -q` with a real token gave `248 passed in 95.97s`. Ruff passed repository-wide. Format checks passed on the 27 files in M9 scope, strict Mypy passed on `44 source files`, and compileall passed. There were also 27 preexisting out-of-scope formatting differences left untouched.
- `cargo fmt --check`, workspace Clippy with `-D warnings` and workspace tests passed. `uv build` produced the sdist and wheel. A separate virtual environment resolved 28 dependencies and ran status, reconcile and both report verifications from the installed wheel.
- Code fences and relative links passed static checks for the eight affected documentation and index files.

## Changed files

- `specs/INDEX.md`
- `specs/20260823-m9-full-a-20y-factor-evaluation-quality.md`
- `python/trademaster/research/AGENTS.md`
- `python/trademaster/research/__init__.py`
- `python/trademaster/research/__main__.py`
- `python/trademaster/research/full_a.py`
- `python/trademaster/research/factor_evaluation.py`
- `python/trademaster/research/factor_evaluation_duckdb.py`
- `python/trademaster/research/factor_report.py`
- `python/trademaster/research/full_a_panel.py`
- `python/trademaster/research/full_a_fundamental.py`
- `python/trademaster/research/tushare.py`
- `python/trademaster/data/research.py`
- `python/trademaster/strategies/industry_fundamental_top5/AGENTS.md`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/data.py`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `tests/test_full_a_research.py`
- `tests/test_factor_evaluation_v2.py`
- `tests/test_factor_evaluation_duckdb.py`
- `tests/test_factor_report_v2.py`
- `tests/test_full_a_panel.py`
- `tests/test_full_a_fundamental.py`
- `tests/test_research_tushare_client.py`
- `tests/test_fundamental_strategy_data_cache.py`
- `tests/test_industry_fundamental_top5.py`
- `docs/full-a-factor-research.md`
- `docs/factor-management.md`
- `docs/reviews/20260823-m9-full-a-quality-review.md`
- `artifacts/strategies/REVIEW_NOTICE.md`
- `artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md`
- `artifacts/research/full-a-20y/FINAL_REPORT.md`
- `pyproject.toml`


## Deferred follow-ups

- Move the compatibility `StrategyDataCache` implementation into the provider-neutral data layer while preserving the existing cache adapter.
- Add a `MarketResearchPolicy` for Hong Kong and US timezone, publication watermark, currency/unit and symbol codec.
- Extend evaluation inputs and robustness tables for ST status, board, listing age and price limits.
- Add Rust corporate-action events for share splits, consolidations and cash dividends. Then rebuild exact fundamental-strategy execution returns.
