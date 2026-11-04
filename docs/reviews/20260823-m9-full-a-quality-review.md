# M9 all-A-share research review and findings

- **Review date** 2026-08-23
- **Scope** Twenty years of A-share data, the point-in-time (PIT) panel, factor evaluation, reporting, and the legacy fundamental strategy.

Three reviewers independently performed one bounded, read-only review with fresh context. The team then used test-driven fixes without reopening successive review rounds. The disposition below distinguishes completed work from remaining data or implementation limits.

## Architecture

| ID | Severity | Finding | Disposition | Outcome |
| --- | --- | --- | --- | --- |
| A-01 | High | Date-only financial announcements became available at the close on their announcement date. | accept/complete | Adopted `date_only_next_eligible_session` and rebuilt the real fundamental panel. |
| A-02 | High | A download task could accept evidence belonging to another request. | accept/complete | Cross-check the canonical request SHA on the task, evidence and Parquet metadata. |
| A-03 | High | Verification proved only object integrity. The pipeline had no path to produce a blocked outcome. | accept/partial | The downloader now records acquisition outcomes and distinguishes missing source history from denied access. The 124 historical empty objects can only be classified as `verified_empty`. Their original source-history reasons are unknown. |
| A-04 | Medium | Reports lacked a link to the actual input panel and materialization. | accept/complete | Report identity v3 binds the market and factor input manifest SHAs. The CLI re-verifies inputs first. |
| A-05 | Medium | The repository lacked a CLI path for fundamental reports. | accept/complete | Added `evaluate --factor-manifest-kind fundamental` and `verify-report`. |
| A-06 | Medium | Reconciliation scanned a shared raw-file glob and could run on an incomplete plan. | accept/complete | Use only typed, completed daily evidence from the target plan. Stop if any task is missing. |
| A-07 | Medium | Research depended on the legacy strategy cache. | accept/partial-defer | The provider protocol moved to `trademaster.data`. Migrating the CLI cache adapter requires a compatibility milestone. |
| A-08 | Medium | The latest session lacked a publication watermark. Market policy was hard-coded. | accept/defer | M9 fixes the A-share policy. Hong Kong and US market clocks, currencies and symbol codecs belong in a later market-policy specification. |

## Engineering

| ID | Severity | Finding | Disposition | Outcome |
| --- | --- | --- | --- | --- |
| E-01 | High | Request identity was not strictly validated. | duplicate A-02 | Closed under A-02. |
| E-02 | High | NaN/Inf adjustments and returns could count as valid. | accept/complete | Enforced finite-or-null and `is_valid => finite`. The real full-universe run had zero non-finite valid rows. |
| E-03 | High | Handwritten SQL could diverge from managed factor definitions. | accept/complete | Row-by-row Python/DuckDB parity covers all 11 factors, nulls and suspended sessions. The M9 input contract uses dense trading sessions. |
| E-04 | Medium | Permission, parameter and transient errors were handled alike. | accept/complete | Permission errors block immediately. Credential, invalid and unknown errors stop. Only explicit transient errors receive bounded backoff. |
| E-05 | Medium | Workers could claim the same task. | accept/complete | Transactional claims, owner and expiry leases, reclamation after expiry, and rejection of writes by workers that lost their lease. |
| E-06 | Medium | Status eagerly imported modules and deserialized 52,563 tasks. | accept/complete | Query the catalog directly and defer heavy imports. Measured peak memory fell from about 485 MB to 162 MB. |
| E-07 | Medium | Two builders queried private tables and duplicated logic. | accept/complete | Both use the typed `completed_evidence_snapshot` API. |
| E-08 | Medium | Endpoint and dataset paths could escape the legacy cache root. | accept/complete | Restricted identifiers, enforced root containment, rejected filesystem-root paths, and added regression tests. |
| E-09 | Medium | Legacy market-data assembly used nearly quadratic scans. | accept/complete | Group adjustment endpoints and price histories in one pass, giving $O(rows)$ time. |
| E-10 | Medium | Out-of-core tests inspected source strings. | accept/complete | Tested real Parquet with a 128 MB DuckDB memory limit and a spill directory. |

## Factors and strategies

| ID | Severity | Finding | Disposition | Outcome |
| --- | --- | --- | --- | --- |
| F-01 | High | GTJA's Python windows compressed missing bars while the SQL windows used dense sessions. | accept/complete | M9 requires dense sessions and parity for all 11 factors. Earlier cache results are excluded from M9 evidence. |
| F-02 | High | Same-day announcements and undated revisions introduced look-ahead. | accept/complete | Announcements first become available in the next session. If the revision time is unknown, only `update_flag=0` is eligible. |
| F-03 | High | Quantile membership depended on future labels. Quantile returns did not establish executable A-share returns. | accept/partial | Assign stocks to groups before joining return labels. Calculate turnover and capacity from the membership and prices available on that date. Price limits, delistings and delayed exits require Rust strategy replay. |
| F-04 | High | Overlapping horizons were compounded as independent returns. | accept/complete | Use the actual minimum observation spacing of 14 sessions. Overlapping 20-session and longer spread, Sharpe, drawdown and recovery statistics are N/A. |
| F-05 | High | Capacity multiplied the amount by 1000 twice. | accept/complete | Read entry amount directly in CNY. Direction-adjusted Size capacity was about CNY 4.75 million. The old estimate was about CNY 58.8 billion. |
| F-06 | Medium | Evaluation ignored direction from the definition. | accept/complete | Direction enters the config hash. IC stays raw. Top selection, spread, turnover and capacity follow direction. |
| F-07 | Medium | Factor correlations pooled observations across years. | accept/complete | Calculate correlations for each event and average them equally. The observation count records valid events. |
| F-08 | Medium | Size neutrality and robustness across size buckets and trading states were incomplete. | accept/partial | Added size-neutral RankIC and top-portfolio size-tercile exposure. ST, board, listing-age and price-limit breakdowns still require schema/Rust work. |
| F-09 | High | The legacy industry strategy backfilled earlier dates with future industry classifications. | accept/complete | Historical intervals are required by default. The formal Top1 probe stopped before selection when it encountered the first gap, and the prior report conclusions were withdrawn. |
| F-10 | Medium | Rust received synthetic QFQ prices and `.BJ` mapped to the wrong venue. | accept/partial-defer | Fixed the BSE venue and relabeled older reports as research approximations. Exact cash-dividend and corporate-action accounting requires a separate Rust milestone. |

## Local evidence after the fixes

The real end-to-end runs produced the following artifacts under the Git-ignored `artifacts/` directory.

| Evidence | Local path |
| --- | --- |
| All-A-share research index | `artifacts/research/full-a-20y/FINAL_REPORT.md` |
| Report for 11 public factors | `artifacts/research/full-a-20y/public-factor-report/` |
| Report for 10 fundamental factors | `artifacts/research/full-a-20y/fundamental-factor-report/` |
| Withdrawal notice for earlier strategy reports | `artifacts/strategies/REVIEW_NOTICE.md` |
| Historical interval blocking the formal Top1 PIT run | `artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md` |
