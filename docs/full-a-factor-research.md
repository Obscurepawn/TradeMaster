# Twenty years of A-share factor research

Do published technical and fundamental factors retain predictive power across the entire historical A-share market once survivorship bias, look-ahead filing dates, and market-cap exposure are removed? The M9 study answers that question across 20 years of point-in-time (PIT) Shanghai, Shenzhen, and Beijing listings. Following three independent engineering and methodology audits on 2026-08-23, the team remediated look-ahead filing alignment, rebuilt the market-wide factor panels and reports from scratch, and formally withdrew pre-audit conclusions (retaining earlier artifacts solely for audit comparison). Across the rebuilt 20-year sample, almost all of raw Size's apparent alpha (`RankIC -0.0668`) disappears after size neutralization (`0.0074`), whereas fundamental value yields strengthen (`Book yield` rising from `0.0555` to `0.0590`) and short-horizon GTJA price alphas persist (`Alpha046` at `0.0562`, `Alpha088` at `-0.0675`) alongside elevated turnover (`0.74–0.81`). [Review and dispositions](reviews/20260823-m9-full-a-quality-review.md)

## Universe and coverage

M9 plans data acquisition through the latest completed trading session. The published study spans **2006-08-23 through 2026-08-21**—covering 4,860 trading sessions, 5,846 CNY A-shares listed across Shanghai, Shenzhen, and Beijing, and 52,563 resumable Tushare download tasks. To prevent survivorship bias, the universe includes all `L/D/P/G` listing states from `stock_basic`, expanding each security strictly across its effective listing and delisting dates; for three historical tickers absent from the current master table, listing intervals were inferred from observed market bars.

**Plan SHA-256** `6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`

Acquired datasets cover the stock master, exchange calendars, raw OHLCV bars, adjustment factors, daily valuation and turnover metrics (`daily_basic`), opening price limits, trading suspensions, special-treatment (`ST`) history, financial indicators, all three financial statements, common equity benchmarks, and available SW2021 industry membership intervals. News feeds, options, intraday ticks, and unlicensed proprietary order-flow series remain outside scope.

## From Tushare to a report

```text
Tushare
  -> immutable endpoint Parquet + exact request hash
  -> DownloadPlanManifest + resumable DuckDB task state
  -> dynamic listing lifecycle x trading-session dense panel
  -> raw OHLC + adj_factor + daily_basic + market status
  -> public price factors / PIT fundamental factors
  -> next-session-close forward labels
  -> DuckDB out-of-core diagnostics
  -> content-addressed JSON/Parquet + standalone HTML/Markdown
```

Every Tushare response is written to immutable Parquet, indexed by canonical request hash in `catalog.duckdb`, and tracked in `research.duckdb`. Before committing a task, the downloader cross-checks file integrity, SHA-256 digest, row count, schema fields, and canonical request hash across the task record, cache catalog, and Parquet metadata; if a worker crashes between writing a Parquet file and updating DuckDB, the next run reconciles and reuses the saved file without re-querying the provider.

Default data root:

```text
data/research/full-a-20y/
  raw/<endpoint>/<content-sha>.parquet
  catalog.duckdb
  research.duckdb
  plans/<plan-sha>/manifest.json
```

## Plan, download and verify

Export `TUSHARE_TOKEN` in your shell before running `plan` or `download` (the planner initializes a Tushare client even when stock listings and calendars are already cached, keeping the credential strictly in process memory). The commands below reference the published M9 plan digest; substitute the SHA-256 printed by `plan` when starting a new window.

```bash
export TUSHARE_TOKEN="$(<~/.config/trademaster/tushare_token)"

tm-research-full-a --data-root data/research/full-a-20y plan \
  --start 2006-08-23 --end 2026-08-23

tm-research-full-a --data-root data/research/full-a-20y download \
  --plan-sha256 6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633 \
  --phase market

tm-research-full-a --data-root data/research/full-a-20y status \
  --plan-sha256 6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633

tm-research-full-a --data-root data/research/full-a-20y verify \
  --plan-sha256 6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633
```

Acquisition proceeds across six phases—`bootstrap`, `market`, `status`, `statement`, `industry`, and `benchmark`—and supports bounded batching via `--max-tasks N` for incremental resumption. Once all tasks complete, `verify` audits every stored object. In `status` output, `object_acquired` indicates non-empty Parquet evidence and `verified_empty` indicates a valid zero-row response, whereas confirmed upstream coverage gaps and credential limits surface explicitly as `source_history_absent` and `permission_blocked`.

## Materialize panels and evaluate factors

Substitute the plan and manifest digests printed by each preceding step into the angle-bracketed placeholders below:

```bash
tm-research-full-a --data-root data/research/full-a-20y materialize \
  --plan-sha256 <plan-sha> \
  --output-root artifacts/research/full-a-20y/panel \
  --observation-frequency month_end \
  --horizons 1,5,20,60,120,252

tm-research-full-a --data-root data/research/full-a-20y evaluate \
  --plan-sha256 <plan-sha> \
  --panel-output-root artifacts/research/full-a-20y/panel \
  --panel-manifest-sha256 <panel-manifest-sha> \
  --report-root artifacts/research/full-a-20y/report \
  --horizons 1,5,20,60,120,252

tm-research-full-a --data-root data/research/full-a-20y materialize-fundamental \
  --plan-sha256 <plan-sha> \
  --panel-output-root artifacts/research/full-a-20y/panel \
  --panel-manifest-sha256 <panel-manifest-sha> \
  --output-root artifacts/research/full-a-20y/fundamental-panel

tm-research-full-a --data-root data/research/full-a-20y evaluate \
  --plan-sha256 <plan-sha> \
  --panel-output-root artifacts/research/full-a-20y/panel \
  --panel-manifest-sha256 <panel-manifest-sha> \
  --factor-manifest-kind fundamental \
  --factor-output-root artifacts/research/full-a-20y/fundamental-panel \
  --factor-manifest-sha256 <fundamental-manifest-sha> \
  --report-root artifacts/research/full-a-20y/fundamental-factor-report

tm-research-full-a --data-root data/research/full-a-20y verify-report \
  --report-root artifacts/research/full-a-20y/fundamental-factor-report \
  --manifest <report-manifest-path>
```

Every `v3` research report binds the cryptographic manifest hashes of both its market panel and its factor inputs; `evaluate` verifies all upstream files before computing diagnostics, and `verify-report` re-checks the emitted report bundle offline.

Because the materialized panel forms a dense grid of active listings × exchange trading sessions—retaining suspended days and missing bars—a five-period lag (`lag(5)`) always spans five **market sessions** rather than five traded bars, and rolling technical windows evaluate across full daily history before sampling month-end observations. For a factor observed as of session `T`'s close, `next_session_close` measures the forward return from the close of `T + 1` to the close `h` sessions later; when entry is blocked, an endpoint price is missing, or the sample ends before `T + 1 + h`, the label row is preserved alongside its explicit dropout reason.

## Managed factors

The public price-factor report evaluates `huatai53.size.log_total_market_value@1` and `gtja191.alpha014/015/018/020/031/034/046/053/058/088@1`, while the PIT fundamental builder computes TradeMaster's nine atomic financial factors plus `fundamental.composite.global@1`.

Because Tushare's `ann_date` provides only a calendar date without an intraday timestamp, financial filings first enter the factor cross-section on the next eligible session (`date_only_next_eligible_session`), and undated restatements are ignored in favor of initial `update_flag=0` disclosures (`original_only_without_revision_known_at`), recording `financial_known_at_session` on every row. The builder computes `fundamental.composite.industry_relative@1` only when every candidate carries an unambiguous PIT industry assignment on the observation date; any unmapped interval records `historical_industry_membership_interval_gap` in the manifest.

## Metric definitions

| Metric | Interpretation |
| --- | --- |
| Universe coverage | Expected count of active instrument × observation-date keys in the PIT universe. |
| Factor / Label / Joint coverage | Shares of the PIT universe with a valid factor, a valid forward return, or both. |
| Horizon label coverage / invalid reason | Per-horizon valid label rate, joint coverage, and itemized dropout reasons. |
| IC / RankIC | Cross-sectional Pearson or Spearman rank correlation between raw factor scores and forward returns. |
| ICIR / RankICIR | Time-series mean IC divided by its standard deviation, reported without implicit annualization. |
| IC t-stat | t-statistic of mean IC under independent, identically distributed period assumptions. |
| Newey-West t-stat | Heteroskedasticity- and autocorrelation-consistent (HAC) t-statistic; preferred whenever forward horizons overlap. |
| Positive IC ratio | Fraction of observation dates with cross-sectional IC strictly greater than zero. |
| Quantile monotonicity | Rank correlation between quantile index and mean quantile forward return. |
| Top-Bottom spread | Mean forward return of the economically top quantile minus the bottom quantile (oriented by `Direction`, whereas IC and RankIC retain the raw factor sign). |
| Direction | `-1` places low raw values in the top quantile; `0/+1` places high raw values in the top quantile. |
| Spread Sharpe / drawdown | Reported only for non-overlapping horizons; because month-end observations sit as few as 14 sessions apart, compounding 20/60/120/252-session spreads would double-count sessions and is marked `N/A`. |
| Recovery events | Longest consecutive streak of non-overlapping observations below the prior high-water mark. |
| Top turnover | One-way membership turnover in the top quantile between consecutive observation dates. |
| Size correlation | Cross-sectional correlation between the factor and `log(total_mv)`. |
| Size-neutral RankIC | Spearman RankIC between forward returns and cross-sectional OLS residuals from `raw_factor ~ 1 + log(total_mv)`. |
| Top cap tercile shares | Proportions of top-quantile stocks falling into the small-, mid-, and large-cap terciles of the concurrent PIT universe. |
| Industry RankIC | Within-industry RankIC where complete PIT industry assignments exist; otherwise `N/A`. |
| Capacity proxy | Approximate CNY AUM = smallest daily traded value in the top quantile × participation rate × top-quantile stock count (omits order-book depth, market impact, fees, and price-limit queues). |
| Factor correlation | Equal-weighted average of per-event cross-sectional Pearson and Spearman correlations across valid observation dates, used to flag redundant signals. |

Stocks enter cross-sectional quantiles **before** joining forward returns, keeping any stock that subsequently halts or delists inside its assigned bucket. Because quantile spreads omit price limits, T+1 settlement, and delayed exits, tradable portfolio returns must always be measured through the Rust execution engine.

## Known data limits

- **Beijing Stock Exchange calendar:** Tushare returned no standalone BSE calendar; sessions since 2021-11-15 use a versioned SSE-derived calendar mapping.
- **SW2021 industry history:** `index_member_all` omits 15,013 historical `in_date/out_date` intervals since 2006, leaving within-industry metrics unavailable on affected dates.
- **Special-treatment (`stock_st`) history:** Although Tushare advertises coverage from 2000, day-by-day completeness still requires explicit verification before filtering strategies on historical ST flags.
- **Back-adjusted vs. tradable prices:** Back-adjusted prices (`HFQ = raw_price * adj_factor`) are restricted to continuous factor windows; order sizing, price limits, and ledger accounting require raw prices.
- **Ex-ante factor direction:** Economic direction comes from each `FactorDefinition` (`-1` for Size; `0` for the ten GTJA price alphas) and enters the evaluation configuration hash without peeking at in-sample returns.
- **Stratified microstructure breakdowns:** Disaggregating performance by ST state, exchange board, listing age, and price-limit exit delays requires dated status schemas paired with Rust execution replay.

## Results from the M9 run

The local summary index is generated at `artifacts/research/full-a-20y/FINAL_REPORT.md` (all `artifacts/` paths are Git-ignored). Acquisition completed all 52,563/52,563 planned tasks—recording 52,439 `object_acquired`, 124 `verified_empty`, and zero failed or blocked tasks (~2.7 GB of raw Parquet). A subsequent full-cache probe with a live token issued zero network calls, and `verify` confirmed every canonical request hash, content SHA-256, schema, and row count across tasks, catalog records, and Parquet files.

**Market panel manifest** `9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89`

**Fundamental manifest** `2f71fc7d9e081d7368ddbd0af8d0adf076b39e6a24ae03c9618afa29a1f2b2a2`

### Public price factors (11)

- **HTML** `artifacts/research/full-a-20y/public-factor-report/objects/b3/b372bf8d50215a26bdfde017845b4a387307fe6561c7b57948909b2151a4a0c5.html`
- **Markdown** `artifacts/research/full-a-20y/public-factor-report/objects/92/92bc256f85b9bfd71440aa795405df5842f1e15ce4a075c53a511d401740a1b6.md`
- **Report ID** `413067aee36089e6f3295c5165e1f069217a71c481436295d8411512ea29b927`
- **Report manifest** `5d0a8013a0e2086e6dcb10af1ee84499c75034048f75a7bcb6ed82eb109407f6`

| Factor | 20-session raw RankIC | Size-neutral RankIC |
| --- | ---: | ---: |
| Alpha046 | 0.0546 | 0.0562 |
| Alpha034 | 0.0450 | 0.0466 |
| Alpha088 | -0.0690 | -0.0675 |
| Size | -0.0668 | 0.0074 |

Residualizing against `log(total_mv)` collapsed Size's 20-session RankIC from `-0.0668` to `0.0074`, confirming that its raw spread reflects pure small-cap beta with a modest top-quantile capacity proxy of roughly CNY 4.75 million and low monthly turnover (~0.11). By contrast, the strongest GTJA price-reversal alphas retained their predictive power after size neutralization (`Alpha046` at `0.0562`, `Alpha088` at `-0.0675`) at the cost of `0.74–0.81` monthly top-quantile turnover and substantial cross-factor collinearity (`Alpha031`/`Alpha034` rank correlation `-1.0000`, `Alpha034`/`Alpha046` `0.9583`, and `Alpha058`/`Alpha088` `0.5575`).

### Fundamental factors (10)

- **HTML** `artifacts/research/full-a-20y/fundamental-factor-report/objects/50/5005791eabfcc7a6f2f0a93ffefe7003b9c670b7bf483553d4378a3a905cfbc0.html`
- **Markdown** `artifacts/research/full-a-20y/fundamental-factor-report/objects/2d/2d0ea21e0e5b743555bf783bbf52b1f629cf421a5e56c0a783681fea9bf4b86a.md`
- **Report ID** `eddc6bef5097db84e1cb7d337d401bf241ec478536a0bdcf208aa0d52fb9a761`
- **Report manifest** `93c82636da316a441ef67743e8966cee1ff7b578f575614a410f6334733542d2`

| Factor | 20-session raw RankIC | Size-neutral RankIC |
| --- | ---: | ---: |
| Book yield | 0.0555 | 0.0590 |
| Dividend yield | 0.0382 | 0.0499 |
| Earnings yield | 0.0335 | 0.0578 |
| Global composite | 0.0279 | 0.0459 |

Enforcing next-session filing availability and `update_flag=0` restatement filtering (`date_only_next_eligible_session` + `original_only_without_revision_known_at`) shifted roughly 170,000–180,000 jointly valid exposures per atomic factor and 613,223 rows in `fundamental.composite.global@1`, trimming the global composite's raw 20-session RankIC from `0.0315` to `0.0279` while raising its size-neutral RankIC to `0.0459`. Fundamental top quantiles exhibited low monthly turnover (`0.04–0.16`) and substantially deeper capacity proxies (roughly CNY 16–65 million); `fundamental.composite.industry_relative@1` was skipped because of the 15,013 missing historical SW2021 intervals.

Across all 15,882,389 active stock-session rows in the 20-year panel, 15,370,049 carried traded daily bars—with zero bars missing `adj_factor` and only 20 missing `daily_basic`. Of the remaining inactive rows, explicit suspension records explained 473,924 sessions and adjustment-factor continuity accounted for another 52,523, leaving 4,963 unexplained missing bars and zero `NaN` or infinite values among valid factors or forward-return labels.
