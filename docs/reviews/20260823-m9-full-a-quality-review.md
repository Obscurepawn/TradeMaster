# M9 all-A-share research review and findings

- **Review date** 2026-08-23
- **Scope** Twenty years of A-share market and financial data, the point-in-time (PIT) dense session panel, factor evaluation, reporting, and the legacy industry-fundamental strategy.

What methodological, data-integrity, and systems defects surfaced when auditing TradeMaster's 20-year A-share factor pipeline—and which gaps remain bounded by upstream provider coverage? Three reviewers independently conducted a single-pass, read-only audit with fresh context, after which the engineering team resolved accepted defects via test-driven fixes without reopening multi-round review loops. Remediations eliminated same-day filing look-ahead (`A-01`/`F-02`), survivorship bias in quantile assignment (`F-03`), compounded overlapping return horizons (`F-04`), a 1,000× double-scaling bug in CNY capacity (`F-05`), and backfilled industry classifications (`F-09`)—triggering a full rebuild of the 20-year factor panels and formal withdrawal of pre-audit strategy performance claims.

## Architecture

| ID | Severity | Finding | Disposition | Outcome |
| --- | --- | --- | --- | --- |
| A-01 | High | Date-only financial announcements entered cross-sections at the close on their filing date (`ann_date`). | accept/complete | Enforced `date_only_next_eligible_session` and rebuilt the full 20-year fundamental panel. |
| A-02 | High | Download tasks could accept cached Parquet files produced by a different request. | accept/complete | Cross-checked canonical request SHA-256 across task records, catalog evidence, and Parquet metadata. |
| A-03 | High | Verification checked file hashes only; the downloader had no state for blocked or absent provider history. | accept/partial | Recorded explicit acquisition outcomes separating `source_history_absent` from `permission_blocked`; the 124 pre-existing zero-row files remain classified as `verified_empty` because their original upstream reasons were not captured at ingest. |
| A-04 | Medium | Emitted reports did not cryptographically bind the market panel and factor materializations they evaluated. | accept/complete | Introduced `v3` report identity binding market and factor manifest SHA-256 digests, verified automatically before CLI evaluation. |
| A-05 | Medium | No CLI path existed to evaluate and verify fundamental factor panels. | accept/complete | Added `evaluate --factor-manifest-kind fundamental` and `verify-report`. |
| A-06 | Medium | Panel reconciliation globbed shared raw directories and could run against incomplete download plans. | accept/complete | Restricted inputs to typed, completed evidence bound to the target plan SHA-256, halting if any task is incomplete. |
| A-07 | Medium | Research workflows imported the legacy strategy cache layer. | accept/partial-defer | Extracted the provider protocol into `trademaster.data`; migrating the legacy CLI cache adapter is deferred to a compatibility milestone. |
| A-08 | Medium | Intraday publication watermarks were absent on the latest session, and A-share market conventions were hard-coded. | accept/defer | Pinned explicit A-share session rules for M9; multi-market clocks, currencies, and symbol codecs (HK/US) belong in a dedicated market-policy specification. |

## Engineering

| ID | Severity | Finding | Disposition | Outcome |
| --- | --- | --- | --- | --- |
| E-01 | High | Request identity was not strictly validated across cache layers. | duplicate A-02 | Resolved under `A-02`. |
| E-02 | High | `NaN` or `Inf` adjustment factors and forward returns could pass validity filters. | accept/complete | Enforced finite-or-null invariants (`is_valid => finite`); the rebuilt 20-year full-universe panel contained zero non-finite valid rows. |
| E-03 | High | Hand-written DuckDB SQL window expressions risked drifting from Python `FactorDefinition` implementations. | accept/complete | Added row-by-row Python/DuckDB parity tests across all 11 public factors, covering nulls and suspended sessions on dense session grids. |
| E-04 | Medium | Permission denials, invalid parameters, and transient network failures shared one retry path. | accept/complete | Permission errors transition immediately to `permission_blocked`; credential, schema, and unknown errors halt; bounded exponential backoff applies exclusively to transient transport failures. |
| E-05 | Medium | Concurrent download workers could claim and overwrite the same task. | accept/complete | Added transactional task claims, owner-and-expiry leases, expired-lease reclamation, and write rejection for stale leaseholders. |
| E-06 | Medium | `status` eagerly imported heavy analytics modules and deserialized all 52,563 task payloads into memory. | accept/complete | Switched `status` to aggregate directly in DuckDB and lazy-load imports, cutting peak RSS from ~485 MB to ~162 MB. |
| E-07 | Medium | Market and fundamental panel builders queried private catalog tables with duplicated SQL. | accept/complete | Consolidated both builders behind the typed `completed_evidence_snapshot` API. |
| E-08 | Medium | Unvalidated endpoint and dataset names could traverse outside the cache root. | accept/complete | Enforced identifier allowlists, canonical root-containment checks, filesystem-root rejection, and path-traversal regression tests. |
| E-09 | Medium | Legacy market-data assembly scanned price and adjustment histories in near-quadratic time. | accept/complete | Indexed adjustment intervals and price series in a single pass, reducing assembly complexity to `O(rows)`. |
| E-10 | Medium | Out-of-core streaming tests asserted against SQL source strings rather than bounded-memory execution. | accept/complete | Verified real Parquet workloads under a strict 128 MB DuckDB memory cap with disk spilling enabled. |

## Factors and strategies

| ID | Severity | Finding | Disposition | Outcome |
| --- | --- | --- | --- | --- |
| F-01 | High | GTJA Python rolling windows skipped missing bars while DuckDB windows counted dense trading sessions. | accept/complete | Standardized M9 on dense trading-session windows with verified parity across all 11 factors; pre-M9 sparse-cache runs are excluded from M9 evidence. |
| F-02 | High | Same-day `ann_date` alignment and undated financial restatements introduced look-ahead bias. | accept/complete | Enforced next-session availability (`date_only_next_eligible_session`) and restricted undated revisions to initial `update_flag=0` filings. |
| F-03 | High | Quantile buckets conditioned on valid future returns (survivorship bias) and ignored execution constraints. | accept/partial | Assigned cross-sectional quantiles, turnover, and capacity strictly from information available on the observation date prior to label joins; price limits, delistings, and delayed exits remain measured via Rust replay. |
| F-04 | High | Multi-month forward spreads were compounded across monthly observations as if non-overlapping. | accept/complete | Enforced the empirical 14-session minimum month-end spacing, marking cumulative spread, Sharpe, drawdown, and recovery `N/A` for overlapping 20-session and longer horizons. |
| F-05 | High | CNY capacity multiplied Tushare traded value (`amount`) by 1,000 twice. | accept/complete | Scaled `amount` once into CNY, revising direction-adjusted Size top-quantile capacity from ~CNY 58.8 billion to ~CNY 4.75 million. |
| F-06 | Medium | `FactorEvaluator` ignored the economic `direction` declared on `FactorDefinition`. | accept/complete | Bound `direction` into the evaluation config hash: IC/RankIC retain raw signs while top-quantile membership, spread, turnover, and capacity follow economic direction. |
| F-07 | Medium | Cross-factor correlations pooled stock-date rows across all twenty years. | accept/complete | Computed cross-sectional correlations within each observation date first and averaged equally across valid events. |
| F-08 | Medium | Size-neutral alpha and microstructure subgroup diagnostics were incomplete. | accept/partial | Added cross-sectional size-neutral RankIC and top-quantile market-cap tercile shares; ST, board, listing-age, and price-limit exit breakdowns await dated status schemas and Rust replay. |
| F-09 | High | The legacy industry-fundamental strategy backfilled missing historical SW2021 intervals with contemporary assignments. | accept/complete | Required verified PIT membership intervals by default; the formal 10-year Top1 audit halted at the first gap (`2016-11-07`), and pre-audit strategy conclusions were withdrawn. |
| F-10 | Medium | Legacy strategy runs passed synthetic forward-adjusted (`QFQ`) prices to Rust and misrouted `.BJ` symbols. | accept/partial-defer | Fixed `.BJ` Beijing Stock Exchange routing and relabeled QFQ event runs as research approximations; exact raw-price corporate-action accounting was subsequently delivered in the snapshot ledger milestone. |

## Local evidence after the fixes

Re-running the end-to-end pipelines after remediation produced the following local audit artifacts (stored under Git-ignored `artifacts/`):

| Evidence | Local path |
| --- | --- |
| All-A-share research index | `artifacts/research/full-a-20y/FINAL_REPORT.md` |
| Report for 11 public factors | `artifacts/research/full-a-20y/public-factor-report/` |
| Report for 10 fundamental factors | `artifacts/research/full-a-20y/fundamental-factor-report/` |
| Withdrawal notice for earlier strategy reports | `artifacts/strategies/REVIEW_NOTICE.md` |
| Historical interval blocking the formal Top1 PIT run | `artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md` |
