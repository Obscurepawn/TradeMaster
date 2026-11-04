# Twenty years of A-share factor research

The M9 study evaluates public price factors and fundamental factors across historical A-share listings. After three independent reviews on 2026-08-23, the team rebuilt the factor panels and reports and withdrew the earlier conclusions. The earlier reports remain available for comparison. [Review and dispositions](reviews/20260823-m9-full-a-quality-review.md)

## Universe and coverage

M9 requests data through the latest completed trading day. The recorded plan covered **2006-08-23 through 2026-08-21**: 4,860 sessions, 5,846 CNY stocks listed in Shanghai, Shenzhen or Beijing, and 52,563 download tasks. The universe uses all L/D/P/G statuses from `stock_basic` and expands each stock across its effective listing and delisting dates. For three historical codes absent from the current master, the plan inferred listing intervals from observed market data.

**Plan SHA-256** `6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`

Coverage includes the stock master, calendars, OHLCV, adjustment factors, daily valuations/market cap/turnover, price limits, suspensions, ST status, financial indicators, the three financial statements, common benchmarks and available SW2021 industry history. News, options, ticks and unlicensed proprietary flow data are outside scope.

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

Tushare responses go into Parquet. `catalog.duckdb` indexes them by request, and `research.duckdb` tracks download progress. Before marking a task complete, the downloader checks the file, hash, row count and fields. It matches the request hash in the task, the cache record and the Parquet metadata. If the process stops between writing a file and marking the task complete, the next run picks up the existing file.

Default data root:

```text
data/research/full-a-20y/
  raw/<endpoint>/<content-sha>.parquet
  catalog.duckdb
  research.duckdb
  plans/<plan-sha>/manifest.json
```

## Plan, download and verify

Set `TUSHARE_TOKEN` before running `plan` or `download`. The planner opens a Tushare client even when the stock list and calendars are already cached. The token stays in the process environment. The example uses the recorded M9 plan hash. For a new run, use the hash printed by `plan`.

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

Download phases are `bootstrap`, `market`, `status`, `statement`, `industry` and `benchmark`. To run a small batch, set `--max-tasks N` and repeat the command to resume. Once all phases finish, `verify` checks every object. In the status output, `object_acquired` means rows were saved and `verified_empty` means the request returned zero rows. Confirmed gaps in source history and denied access have separate statuses, `source_history_absent` and `permission_blocked`.

## Materialize panels and evaluate factors

Replace the angle-bracketed plan and manifest references below with the values printed by the preceding commands.

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

Each v3 report records the manifest hashes for its market panel and factor inputs. The CLI verifies the input files before evaluation. Use `verify-report` to check the resulting report files.

The dense panel retains each trading session for each active stock, including suspensions and missing bars. Thus `lag(5)` means five **market sessions**. Factors use the full session windows before sampling month-end observations.

For a factor observed at the close of T, `next_session_close` measures the return from the close of the next trading session to the close `h` sessions later. If entry is blocked, a price is missing or the sample ends too soon, the evaluator keeps the label row and records the reason.

## Managed factors

The first public report covers `huatai53.size.log_total_market_value@1` and `gtja191.alpha014/015/018/020/031/034/046/053/058/088@1`. The fundamental PIT builder also produces nine existing atomic factors and `fundamental.composite.global@1`.

Tushare reports a filing's date in `ann_date`. The factor builder first uses the filing in the next eligible session. When the publication time of a revision is unknown, it uses the original `update_flag=0` record. The manifest identifies the policies as `date_only_next_eligible_session` and `original_only_without_revision_known_at`. Each observation records `financial_known_at_session`.

The builder computes `fundamental.composite.industry_relative@1` when every stock has one industry assignment on its observation date. A missing or ambiguous assignment produces `historical_industry_membership_interval_gap` in the manifest.

## Metric definitions

| Metric | Interpretation |
| --- | --- |
| Universe coverage | Expected number of instrument × observation-date keys in the PIT universe. |
| Factor / Label / Joint coverage | Fractions with a valid factor, valid label or both. Each denominator is the PIT universe. |
| Horizon label coverage / invalid reason | Report label coverage, joint coverage and invalid reasons separately for each horizon. |
| IC / RankIC | Pearson or rank correlation between raw factor values and forward returns. |
| ICIR / RankICIR | Mean IC divided by its time-series standard deviation, without implicit annualization. |
| IC t-stat | Significance of the mean under independent, identically distributed observations. |
| Newey-West t-stat | Accounts for heteroskedasticity and serial correlation. Use this when reading overlapping horizons. |
| Positive IC ratio | Fraction of observation periods with IC greater than zero. |
| Quantile monotonicity | Correlation between the quantile number and its average return. |
| Top-Bottom spread | Forward return of the economically top group minus the bottom group, using the definition's direction. IC and RankIC retain the raw sign. |
| Direction | `-1` puts low raw values in the top group. `0/+1` puts high values in the top group. |
| Spread Sharpe / drawdown | The shortest gap between observed month-ends was 14 sessions. For overlapping 20/60/120/252-session returns, cumulative spread, Sharpe, drawdown and recovery are N/A. |
| Recovery events | Longest run of observations below the previous high-water mark. |
| Top turnover | Change in top-group membership between consecutive observations. |
| Size correlation | Cross-sectional correlation of the factor with `log(total_mv)`. |
| Size-neutral RankIC | Regress `raw factor ~ 1 + log(total_mv)` cross-sectionally. Compute the RankIC between the residuals and future returns. |
| Top cap tercile shares | Fractions of top-group members in the small, middle and large current-cap terciles of the full PIT universe. |
| Industry RankIC | Within-industry RankIC where historical PIT membership exists. Otherwise N/A. |
| Capacity proxy | Approximate AUM = the smallest traded amount among top-group stocks × participation rate × number of stocks. The proxy uses reported traded value and omits depth, impact, commissions, stamp duty, slippage and price-limit queues. |
| Factor correlation | Calculate Pearson/Spearman for each event, then weight valid events equally. The count is the number of events. Use it to identify redundant factors. |

The evaluator assigns each stock to a quantile **before** joining future returns. A stock that later delists stays in its original group. To measure what a portfolio could actually trade, replay the targets through Rust, which applies price limits and records delayed exits.

## Known data limits

- Tushare returned no independent Beijing (BSE) calendar. Since 2021-11-15, BSE sessions use a versioned SSE-derived mapping.
- SW2021 dates industry membership with `in_date/out_date`. Some historical intervals since 2006 are missing, so industry-neutral metrics for those observations are unavailable.
- `stock_st` advertises history from 2000. Coverage still requires an audit by day.
- HFQ = `raw_price * adj_factor` is for factor research. Execution and trading constraints use raw market prices.
- Direction comes from the managed definition and enters the evaluation config hash. It is set independently of full-sample returns: `-1` for Size and `0` for the ten GTJA factors.
- The remaining breakdowns cover ST status, board, listing age and price-limit entries/exits. Producing them requires dated status histories and a replay of delayed exits.

## Results from the M9 run

The local index is `artifacts/research/full-a-20y/FINAL_REPORT.md`. Generated `artifacts/` files are excluded from Git. The final download marked 52,563/52,563 tasks complete, with 52,439 `object_acquired`, 124 `verified_empty` and 0 failed/blocked. With a real token, a full cache probe made zero provider calls. Verification rechecked the task/evidence/Parquet request SHA, object SHA, metadata and row counts. Raw data occupied about 2.7 GB.

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

Almost all of Size's raw effect was market-cap exposure. The top group for technical factors turned over about 0.74–0.81, compared with about 0.11 for direction-adjusted Size. Its capacity proxy was about CNY 4.75 million. Per-event rank correlations indicated redundancy: Alpha031/034 was -1.0000, Alpha034/046 was 0.9583 and Alpha058/088 was 0.5575.

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

The stricter PIT policy reduced global composite raw RankIC from 0.0315 to 0.0279. For the nine atomic fundamentals, about 170,000–180,000 exposures valid under both policies changed value. The global composite changed in 613,223 jointly valid cases. Top-group turnover was about 0.04–0.16, with capacity proxies of roughly CNY 16–65 million. The builder skipped the industry-relative composite because 15,013 historical industry intervals were missing.

Across 15,882,389 active session rows the real panel had 15,370,049 bars. Zero same-day bars lacked `adj_factor`, and 20 lacked `daily_basic`. Explicit suspensions accounted for 473,924 rows and continuity of adjustments identified 52,523 more. Another 4,963 missing bars lacked explanatory evidence. There were zero non-finite values among valid factors or forward returns.
