# TradeMaster factor management

Python defines, computes, stores and evaluates factors and generates signals. Rust receives exact signals and handles market rules, execution and accounting.

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

Parquet stores factor values. DuckDB indexes definitions, materializations and evaluations. The public factor library stores its records as content-addressed JSON and uses a separate DuckDB catalog for discovery. Only candidates linked to a `FactorDefinition` can produce values.

## Factor identity and storage

A `FactorDefinition` includes `factor_id/version`, typed dataset and factor dependencies, canonical parameter JSON and its SHA-256 hash, and the SHA-256 of the implementation module's source. It records the lookback, frequency, cross-sectional rules, units and direction. The `definition_sha256` covers all these fields. An ID/version identifies one definition. Increment the version when the implementation or parameters change to preserve prior results.

The default strategy data root contains the following factor files.

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

A `FactorMaterialization` records how to reproduce one result. It identifies the factor definition, its code and parameter hashes, and any parent materializations. For the data, it records the snapshot and source objects, the exact Arrow input table hash, universe and date range. It points to the output Parquet file. On a cache hit, the reader verifies that file's hash, schema, row count and metadata.

## Fundamental and public factors

The fundamental strategy registers 11 definitions.

| Group | Factors |
|---|---|
| Value | `earnings_yield`, `book_yield`, `dividend_yield` |
| Quality | `roe`, `gross_margin`, `ocf_to_revenue` |
| Growth | `sales_growth`, `profit_growth` |
| Safety | `debt_safety` |
| Composites | `fundamental.composite.industry_relative`, `fundamental.composite.global` |

Composite factors process each event time separately. Require PE and PB and at least five valid indicators out of nine before fitting the cross-section. Within the eligible universe, winsorize the raw values at 5%/95% and compute population z-scores. A missing optional indicator contributes zero. The other indicators keep their original weights.

The public suite adds 11 definitions covering Huatai Size and ten reviewed GTJA price/volume formulas. Each GTJA definition specifies raw OHLC, `adj_factor` and the HFQ (back-adjusted price) calculation. Units distinguish price differences, ratios and percentages. The CLI lists 22 managed definitions. Existing fundamental strategy bundles reference only the 11 they used, so extending the catalog leaves their artifact IDs intact. The public library describes all 460 candidates and their data requirements. [`public-factor-library.md`](public-factor-library.md)

## Evaluation

`FactorEvaluator` requires every forward-return row to identify its factor event, entry time, exit time, alignment rule and label snapshot ID. An ordered calendar of eligible entries verifies that entry occurs at the next eligible execution event after the factor observation. The Arrow label table has its own snapshot. Switching between open- and close-based entry conventions requires matching label data.

The evaluator reports coverage, Pearson IC and Spearman RankIC per period, IC mean and standard deviation, ICIR, the proportion of positive IC observations, returns by quantile, the top-minus-bottom return spread, and top-quantile turnover.

M9's all-A-share evaluation shows how many observations enter each horizon and why others drop out. It reports HAC statistics and spreads over non-overlapping periods. To examine exposures, it computes size-neutral and within-industry RankIC, groups stocks by their market-cap tercile at the time, and reports capacity in CNY and factor correlations at each event.

Assign stocks to quantiles before joining their future returns. The configuration hash includes the direction specified by the factor definition and the actual minimum number of trading sessions between observations.

The evaluator can save and catalog forward returns, IC, quantiles, the summary and the manifest. The manifest records the label snapshot and the eligible-entry calendar hash. Keep labels within evaluation so signal generation cannot read future returns.

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

Formal commands require an explicit version. They do not resolve an implicit `latest`.

## End-to-end verification

The fundamental strategy's `trademaster.e2e-bundle/v3` identifies the candidate input snapshots and Parquet files, 11 factor definitions, 220 materializations over 20 periods and their Parquet files, selection snapshots, signals, the Rust request and result, report inputs, HTML, strategy summary and Markdown report.

`index_member_all` supplies some `in_date/out_date` membership intervals. The M9 all-A-share audit still found 15,013 historical interval gaps. A formal strategy checks membership on the signal date and stops if it cannot establish it. The `static_latest_experiment` policy has a separate strategy identity because applying today's classifications to past dates does not establish PIT membership. The old M7 returns also used continuous QFQ prices to approximate corporate actions. Those artifacts are retained for diagnostics.

## Twenty-year all-A-share evaluation

M9 plans resumable downloads for the historical stock universe, builds a dense session panel, streams evaluation through DuckDB and writes factor research reports. Its price universe is independent of a strategy's post-selection `selected_union` cache. `tm-factor` manages individual definitions, materializations and v1 evaluation. `tm-research-full-a` orchestrates long-horizon, market-wide studies. [`full-a-factor-research.md`](full-a-factor-research.md)
