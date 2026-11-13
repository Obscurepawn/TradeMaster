# TradeMaster factor management

How does TradeMaster ensure that every alpha factor used in research or live-rule backtesting is versioned, point-in-time (PIT) auditable, and strictly isolated from future return labels? Python owns factor governance, computation, immutable storage, cross-sectional evaluation, and portfolio signal generation, while the Rust runtime consumes exact target-weight signals to enforce A-share market rules, execution constraints, and FIFO accounting.

```text
PIT source snapshot
  -> governed public factor candidates/source records
  -> managed atomic factor definitions
  -> immutable factor Parquet
  -> grouped/global composite materializations
  -> sparse strategy selection
  -> target-weight signals
  -> Rust runtime
```

Factor exposures are persisted in immutable Parquet files, while DuckDB indexes factor definitions, materializations, and evaluations. Public broker formulas live alongside them as content-addressed JSON records indexed in a separate DuckDB discovery catalog; only candidates explicitly promoted and linked to a managed `FactorDefinition` can produce factor values.

## Factor identity and storage

Each `FactorDefinition` binds `factor_id/version`, typed dataset and upstream factor dependencies, canonical parameter JSON (and its SHA-256), and the SHA-256 of the implementation module's source code—together with its lookback window, observation frequency, cross-sectional standardization rules, physical units, and economic direction—into a single content-addressed `definition_sha256`. Because every `factor_id@version` maps one-to-one to a specific definition hash, any change to code or parameters requires incrementing `version`, keeping prior research artifacts permanently reproducible.

Under the default strategy data root, factor artifacts follow a content-addressed layout:

```text
factors/
  catalog.duckdb
  library/
    catalog.duckdb
    manifest.json
    sources/<source_id>/<record_sha>.json
    collections/<collection_id>/<record_sha>.json
    candidates/<candidate_id>/<record_sha>.json
  definitions/<factor_id>/<version>/<definition_sha>.json
  values/factor_id=<id>/factor_version=<version>/event_year=<year>/<sha>.parquet
  materializations/<materialization_id>/manifest.json
  evaluations/<evaluation_id>/
```

Each `FactorMaterialization` captures the full provenance needed to reproduce a computed panel: the `FactorDefinition` (with code and parameter hashes), any upstream parent materializations, the input data snapshot and source object hashes, the exact Arrow input table hash, the covered universe and date range, and the output Parquet path. On every cache hit, the reader re-verifies the Parquet file's SHA-256 hash, Arrow schema, row count, and embedded metadata before returning values.

## Fundamental and public factors

The built-in fundamental suite registers 11 managed definitions—nine atomic ratios and two cross-sectional composites:

| Group | Factors |
|---|---|
| Value | `earnings_yield`, `book_yield`, `dividend_yield` |
| Quality | `roe`, `gross_margin`, `ocf_to_revenue` |
| Growth | `sales_growth`, `profit_growth` |
| Safety | `debt_safety` |
| Composites | `fundamental.composite.industry_relative`, `fundamental.composite.global` |

Composite factors fit each rebalance timestamp independently across the eligible universe: a stock enters the cross-section only if it reports valid PE, valid PB, and at least five of the nine atomic indicators. Raw exposures across eligible stocks are winsorized at the 5th and 95th percentiles and standardized into population z-scores; missing optional indicators receive a neutral z-score of zero while available indicators retain their configured weights.

The public broker suite contributes 11 additional definitions—Huatai Size (`huatai53.size.log_total_market_value@1`) plus ten reviewed Guotai Junan (GTJA) price-and-volume formulas—bringing the managed CLI catalog to 22 definitions. Each GTJA definition declares explicit dependencies on raw OHLC bars and `adj_factor`, computes back-adjusted (`HFQ`) prices internally, and tags its output units to distinguish price differences, dimensionless ratios, and percentages. Because existing fundamental strategy bundles pin only the 11 definitions they consume, expanding the managed registry leaves prior bundle hashes untouched. All 460 cataloged public candidates and their data gates are documented in [`public-factor-library.md`](public-factor-library.md).

## Evaluation

To prevent look-ahead bias, `FactorEvaluator` requires every forward-return row to carry its factor observation timestamp, entry timestamp, exit timestamp, alignment convention, and label data snapshot ID, checking against an ordered calendar of eligible openings that entry occurs no earlier than the first tradable event after observation. Forward-return Arrow tables carry their own snapshot identity—so switching between open-to-open and close-to-close return conventions requires matching label manifests—and remain isolated inside the evaluation boundary where signal generators cannot read them.

Stocks are assigned to cross-sectional quantiles *before* joining forward returns so subsequent halts or delistings do not introduce survivorship bias, and the evaluation configuration hash binds both the factor's declared economic direction and the empirical minimum number of trading sessions between observations. Standard outputs cover cross-sectional coverage, per-period Pearson IC and Spearman RankIC, IC mean and standard deviation, ICIR, positive-IC hit rate, quantile returns, top-minus-bottom spread, and top-quantile turnover.

In the M9 market-wide study, evaluation extends these diagnostics with per-horizon dropout attribution, Newey-West HAC t-statistics and spreads restricted to non-overlapping horizons, size-neutral and within-industry RankIC, current market-cap tercile breakdowns, CNY capacity proxies, and per-event cross-factor rank correlations. Every evaluation run can persist and catalog its forward-return join, IC series, quantile returns, summary metrics, and content-addressed manifest (binding both the label snapshot and the eligible-entry calendar hash).

## CLI

```bash
tm-factor list
tm-factor describe fundamental.roe@1
tm-factor graph fundamental.composite.industry_relative@1
tm-factor --root <factor-root> verify <materialization-id>
tm-factor --root <factor-root> materialize <factor@version> ...
tm-factor --root <factor-root> evaluate <materialization-id> \
  --forward-returns <labels.parquet> \
  --label-snapshot <manifest.json> \
  --eligible-entry-times <times.json> ...
tm-factor --root <factor-root> library-sync
tm-factor --root <factor-root> library-status
tm-factor --root <factor-root> library-list --collection huatai-53-2020.06.02
tm-factor --root <factor-root> library-list --dependency daily_bars.close
tm-factor --root <factor-root> library-describe gtja191.alpha014
```

All formal CLI commands require an explicit `@version` suffix and refuse to resolve an implicit `latest`.

## End-to-end verification

A completed fundamental strategy run packages a self-verifying `trademaster.e2e-bundle/v3` manifest linking its candidate input data snapshots and Parquet files, all 11 `FactorDefinition` records, 220 `FactorMaterialization` artifacts across 20 semiannual rebalances, sparse selection snapshots, Arrow target-weight signals, the Rust execution request and result, and the rendered HTML, Markdown, and JSON strategy summaries.

Point-in-time industry classification relies on `in_date`/`out_date` intervals in `index_member_all`, where the M9 all-A-share audit uncovered 15,013 missing historical intervals. Formal strategy runs verify PIT membership on every signal date and halt immediately on a gap; the exploratory `static_latest_experiment` policy backfills missing intervals under a distinct strategy ID because projecting current industry assignments onto historical dates violates PIT discipline. Legacy M7 artifacts—which approximated corporate actions via continuous forward-adjusted (`QFQ`) prices—are retained strictly as historical diagnostics.

## Twenty-year all-A-share evaluation

While `tm-factor` manages individual factor definitions, materializations, and single-factor v1 evaluations, `tm-research-full-a` orchestrates twenty-year, market-wide studies—planning resumable Tushare downloads across the full historical listing lifecycle, building a dense session panel independent of any strategy's post-selection `selected_union` cache, streaming out-of-core diagnostics through DuckDB, and publishing standalone research reports. See [`full-a-factor-research.md`](full-a-factor-research.md) for the full workflow and empirical findings.
