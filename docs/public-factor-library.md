# A-share public factor catalog

How does TradeMaster incorporate published broker factor libraries without letting ambiguous operator definitions, missing point-in-time (PIT) inputs, or proprietary report licensing slip into production backtests? Every formula disclosed in the Huatai Securities multi-factor series and Guotai Junan (GTJA) Alpha191 enters the repository first as a content-addressed `FactorCandidateRecord`—recording its source publication, `metadata-only` licensing terms, typed input dependencies, and open data or semantic blockers. Only candidates whose local implementations match pinned reference outputs can link to an executable `FactorDefinition` and emit Parquet values.

The built-in catalog indexes **8 sources, 7 collections, and 460 candidates**. Eleven reviewed candidates currently run as managed definitions—`huatai53.size.log_total_market_value@1` and `gtja191.alpha{014,015,018,020,031,034,046,053,058,088}@1`—while seven pre-existing fundamental definitions (five backed by fields in the legacy ten-year strategy cache and two measuring single-quarter growth) are explicitly tagged `implemented_variant` to document where they depart from Huatai's published formulas.

## Collections

| Collection | Candidates | Coverage or outstanding requirement |
| --- | ---: | --- |
| Huatai 2020 style factors (nine categories) | 53 | Complete formula and dependency metadata; input readiness splits into the 14 / 36 / 3 groups below. |
| Huatai fund flows | 50 | `blocked_data` pending Wind order-size tiers, aggressor direction, and intraday session slices. |
| Huatai financial quality | 51 | Cataloged; promotion requires typed statement line items, announcement timestamps, and a PIT restatement policy. |
| Huatai analyst consensus | 19 | `blocked_data` pending licensed historical consensus snapshots. |
| Huatai historical percentiles | 86 | 86 parameter combinations enumerated in the published table (preserving the report summary's `reported_count=89` in metadata). |
| Huatai structural risk model | 10 | `risk_model_only`; factor exposures intended for risk modeling and return attribution rather than standalone alpha ranking. |
| Guotai Junan Alpha191 | 191 | Continuous IDs `1–191`, each recording its verified DolphinDB input signature and semantic review state. |

Huatai's separate technical-factor survey replicates WorldQuant Alpha101 on A-shares and is counted once in the catalog.

## Huatai 53 data requirements

| Input category | Count | Factors |
| --- | ---: | --- |
| Present in the legacy ten-year strategy cache | 14 | `EP`, `BP`, `DP`, `ROE_q`, `grossprofitmargin_q`, `size`, four return horizons, and four return volatilities. |
| Available by extending Tushare ingestion and canonical PIT derivations | 36 | Remaining valuation, growth, quality, and leverage ratios, eight turnover-weighted returns, and four turnover factors. |
| Requires explicit benchmark and regression contracts | 3 | `beta`, `HAlpha`, and `resvol`. |

Whereas the legacy ten-year strategy cache (446 selected stocks across 20 semiannual snapshots) served only for formula verification, unbiased A-share IC estimation requires the [20-year full-universe PIT panel](full-a-factor-research.md). Six definitional differences separate TradeMaster's pre-existing `implemented_variant` fundamentals from Huatai 53:

- **Earnings yield (`EP`):** Huatai defines `EP = 1 / pe_ttm` across all observations, whereas `fundamental.earnings_yield@1` filters out non-positive PE ratios.
- **Dividend yield (`DP`):** Tushare reports `dv_ttm` in percentage points; matching Huatai's raw decimal ratio requires dividing by 100.
- **Quarterly growth (`Sales_G_q` / `Profit_G_q`):** Local `q_sales_yoy` and `q_profit_yoy` measure single-quarter year-on-year growth, whereas Huatai uses the latest disclosed year-to-date period.
- **Operating cash flow ratio:** Huatai's `operationcashflowratio` divides operating cash flow by net profit; local `ocf_to_or` divides by operating revenue.
- **Leverage:** Local `debt_to_assets` does not match any of the five leverage definitions in Huatai 53.
- **Industry neutralization:** Huatai grouped stocks by historical CITIC industry; applying a static contemporary SW2021 map to past dates introduces look-ahead industry reassignment.

## GTJA Alpha191 data requirements

Input signatures for `Alpha001`–`Alpha191` were audited against a pinned Apache-2.0 DolphinDB reference commit:

| Input category | Count | Coverage |
| --- | ---: | --- |
| Directly constructible from cached OHLCV and `adj_factor` | 146 | All required raw fields already exist in the 446-stock union cache. |
| Derivable once VWAP is defined | 40 | `vwap_cny_per_share = amount * 10 / volume`, scaled by the same adjustment factor as OHLC. |
| Requires benchmark index series | 4 | `Alpha075`, `149`, `181`, and `182`. |
| Requires Fama-French factor-return series | 1 | `Alpha030` consumes `MKT`, `SMB`, and `HML` (omitted from the upstream DolphinDB README summary table). |

Formulas with unresolved operator or typesetting ambiguities remain gated under `blocked_semantics` until each carries a parsed source AST, approved errata, versioned operator semantics, and golden test vectors—covering regression window alignment in `Alpha021`, `WMA` weighting in `027`, three-factor regression inputs in `030`, recursive `SELF` state in `143`, `SUMAC` and parenthesis placement in `165`/`183`, dimensional mismatches and parentheses in `181`, and garbled PDF typesetting in `190`.

The ten promoted GTJA definitions (`alpha014/015/018/020/031/034/046/053/058/088@1`) depend exclusively on `daily_bars.open/close` and `adj_factors.adj_factor`—requiring no `RANK`, `SMA`, `VWAP`, benchmark, regression, or recursive operators. Rather than trusting vendor-specific `adjusted_close` conventions, each formula computes `hfq_price = raw_price * adj_factor` under the `gtja-reviewed-price/v1` price contract, assigns explicit output units (`hfq_price_difference` for `Alpha014`, alongside dimensionless ratios and percentages), and fixes ex-ante `direction = 0` so empirical signs are measured out-of-the-box during evaluation.

## Catalog status

| Field or value | Meaning |
| --- | --- |
| `implementation_status=implemented` | Verified against reference examples; a linked `factor_id@version` is registered in the managed catalog. |
| `implemented_variant` | A related local definition exists, with every formula or unit divergence documented on the candidate record. |
| `ready` | Formula semantics and data contracts are resolved; implementation is pending. |
| `blocked_data` | Required market/fundamental series, typed schemas, or PIT timestamps are unavailable. |
| `blocked_semantics` | Operator conventions, parenthesis placement, regression specs, recursion, or PDF errata remain open. |
| `risk_model_only` | Structural risk-model exposure or attribution factor, excluded from standalone alpha materialization. |
| `data_availability=current_cache_partial` | Required columns exist locally for a subset of stocks rather than the full historical A-share universe. |
| `derived_from_current` | Deterministically computable from existing fields (such as Tushare `VWAP`). |
| `provider_extension` | Available from Tushare endpoints, pending formal M1 schema contracts and historical coverage audits. |
| `benchmark_contract_required` | Requires a pinned benchmark index ID, return convention, and rolling regression specification. |

Setting `distribution_status=metadata-only` restricts repository redistribution to bibliographic citations and structured dependency metadata while original broker publications retain their copyrights; TradeMaster validates numerical parity against the pinned Apache-2.0 DolphinDB revision and executes its own clean-room implementations locally.

## Store and query the catalog

Catalog records are stored as immutable, content-addressed JSON files and indexed in DuckDB for fast filtering:

```text
<factor-root>/library/
  catalog.duckdb
  manifest.json
  sources/<source_id>/<record_sha>.json
  collections/<collection_id>/<record_sha>.json
  candidates/<candidate_id>/<record_sha>.json
```

`manifest.json` lists every source, collection, and candidate alongside its relative path and SHA-256 digest. Whenever the library opens, TradeMaster cross-checks file hashes, DuckDB rows, dependency indexes, and linked `FactorDefinition` entries—raising an error immediately if any record is missing, modified, or collides on an identifier.

Substitute your factor store directory for `<factor-root>` to sync and query the catalog:

```bash
tm-factor --root <factor-root> library-sync
tm-factor --root <factor-root> library-status
tm-factor --root <factor-root> library-list \
  --collection gtja-alpha191-2017.06.15 \
  --status implemented \
  --dependency daily_bars.close
tm-factor --root <factor-root> library-list \
  --collection huatai-53-2020.06.02 \
  --data-availability current_cache_partial
tm-factor --root <factor-root> library-describe huatai53.value.ep
```

`library-list` filters the 460 candidates by collection, review status, data availability, or column dependency, while `library-describe` prints a single candidate's citation, input contract, open blockers, variant notes, and content hash.

## Requirements for promoting more candidates

1. Extend the formal M1 data schema with `pe_ttm`, `pb`, `ps_ttm`, `dv_ttm`, and typed PIT financial-statement line items.
2. Expose a dedicated research view with back-adjusted OHLCV and VWAP while keeping raw unadjusted prices in the execution engine.
3. Version canonical operator contracts for rolling windows, cross-sectional ranks, correlations, OLS regressions, `SMA`, and linear decay.
4. Persist the raw source AST, reviewed AST, approved errata, and golden reference vectors alongside every promoted candidate.
5. Verify full-universe historical bars, turnover, trading states, and PIT industry intervals before interpreting IC or quantile spreads on an unbiased A-share sample.
