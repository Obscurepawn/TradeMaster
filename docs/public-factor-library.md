# A-share public factor catalog

TradeMaster catalogs factors disclosed in the Huatai Securities research series and Guotai Junan (GTJA) Alpha191. A `FactorCandidateRecord` lists the source, formula reference, inputs, licensing, local implementation and any data or formula questions still open. Once a local implementation matches its reference examples, a `FactorDefinition` can produce factor values in Parquet.

The built-in catalog contains **8 sources, 7 collections and 460 candidates**. The first 11 executable definitions from these collections are `huatai53.size.log_total_market_value@1` and `gtja191.alpha{014,015,018,020,031,034,046,053,058,088}@1`. Seven existing fundamental definitions resemble Huatai 53 concepts but differ from the published definitions. Five already had inputs in the strategy cache. The other two measure single-quarter growth. All seven are classified as `implemented_variant`.

## Collections

| Collection | Candidates | Coverage or outstanding requirement |
| --- | ---: | --- |
| Huatai 2020 style factors (nine categories) | 53 | Formula and dependencies for every factor. Input coverage splits into the 14/36/3 groups below. |
| Huatai fund flows | 50 | `blocked_data` pending Wind order size, aggressor direction and intraday time segments. |
| Huatai financial quality | 51 | Registered. Requires typed statement line items, announcement dates and a PIT revision policy. |
| Huatai analyst consensus | 19 | `blocked_data` pending licensed historical consensus snapshots. |
| Huatai historical percentiles | 86 | The published parameter table shows 86 combinations. The catalog also preserves the report summary's `reported_count=89`. |
| Huatai structural risk model | 10 | `risk_model_only`. These exposures are for risk modeling and attribution. |
| Guotai Junan Alpha191 | 191 | Continuous IDs 1–191. Each entry records its DolphinDB input list and review status. |

Huatai's large-scale technical-factor study replicates WorldQuant Alpha101 on A-shares. The catalog counts that formula set once.

## Huatai 53 data requirements

| Input category | Count | Factors |
| --- | ---: | --- |
| Fields present in the ten-year strategy cache | 14 | EP, BP, DP, ROE_q, grossprofitmargin_q, size, four returns and four standard deviations. |
| Available by extending Tushare ingestion and defining canonical fields/PIT derivations | 36 | Remaining valuation, growth, quality and leverage factors, eight turnover-weighted returns, and four turnover measures. |
| Benchmark and regression conventions required | 3 | beta, HAlpha, resvol. |

The original strategy cache covered 446 previously selected stocks over ten years and 20 semiannual fundamental snapshots. It served to check formulas. The [full-universe study](full-a-factor-research.md) uses historical listings to estimate A-share IC.

The local and published definitions differ in units, denominators and reporting periods.

- Huatai defines `EP=1/pe_ttm` without a positive-PE filter. Local `fundamental.earnings_yield@1` removes negative PE.
- Tushare `dv_ttm` is a percentage. Divide by 100 to use a ratio as the raw Huatai DP exposure.
- Local `q_sales_yoy/q_profit_yoy` measures year-on-year change in one quarter. Huatai's `Sales_G_q/Profit_G_q` uses the most recently disclosed year-to-date period.
- Huatai `operationcashflowratio` divides operating cash flow by net profit. Local `ocf_to_or` divides it by operating revenue.
- Local `debt_to_assets` differs from all five Huatai 53 leverage definitions.
- Huatai used historical CITIC industries. Grouping past observations with a current SW2021 map instead assigns some stocks to an industry they joined later.

## GTJA Alpha191 data requirements

The catalog checked the input signatures of Alpha001–Alpha191 at a pinned DolphinDB commit.

| Input category | Count | Coverage |
| --- | ---: | --- |
| Constructible from OHLCV and adjustment factors in the strategy cache | 146 | The required raw fields exist for the 446-stock union. |
| VWAP derivable from available fields | 40 | `vwap_cny_per_share = amount * 10 / volume`, using the same adjustment scale as OHLC. |
| Need the index series used in the study | 4 | Alpha075, 149, 181 and 182. |
| Factor-return series required | 1 | Alpha030 also requires MKT, SMB and HML. The reference README's abbreviated input table omits these three series. |

For the remaining formulas, `blocked_semantics` records an open interpretation question. Each needs a parsed source formula (AST), approved corrections, versioned operator definitions and a reference example. Outstanding questions include the regression alignment in Alpha021, WMA in 027, FF3 in 030, recursive `SELF` in 143, `SUMAC` and parentheses in 165/183, dimensions and parentheses in 181, and damaged PDF typesetting in 190.

The ten implemented formulas use open and close prices. They need no RANK, SMA, VWAP, benchmark, regression or recursive operators. Each reads `daily_bars.open/close` and `adj_factors.adj_factor` and calculates `hfq_price = raw_price * adj_factor` under `gtja-reviewed-price/v1`. A vendor’s `adjusted_close` can follow a different adjustment convention. Alpha014 uses units of `hfq_price_difference`. Ratio and percentage factors carry their respective units. The library records direction as 0 for all ten. Their empirical direction is measured in the evaluation.

## Catalog status

| Field or value | Meaning |
| --- | --- |
| `implementation_status=implemented` | A corresponding `factor_id@version` can enter the managed registry. |
| `implemented_variant` | A local approximation exists and the record documents each known difference. |
| `ready` | Formula and inputs are understood. Implementation is pending. |
| `blocked_data` | Data, typed contracts or PIT evidence are missing. |
| `blocked_semantics` | Operators, parentheses, regression, recursion or proposed corrections remain unresolved. |
| `risk_model_only` | Risk exposure or attribution specification. |
| `data_availability=current_cache_partial` | The fields exist locally, with incomplete A-share coverage. |
| `derived_from_current` | Deterministically derivable from existing fields, e.g. Tushare VWAP. |
| `provider_extension` | Tushare offers fields or line items, but formal schemas or historical coverage remain incomplete. |
| `benchmark_contract_required` | Index identity, price conventions and regression policy must be defined. |

`distribution_status=metadata-only` limits distribution to references and catalog metadata. The broker reports retain their original licenses. TradeMaster checks expected values against a specific Apache-2.0 DolphinDB revision and runs the resulting definitions locally.

## Store and query the catalog

Each catalog record lives in a JSON file named by its content hash. DuckDB indexes those records for search.

```text
<factor-root>/library/
  catalog.duckdb
  manifest.json
  sources/<source_id>/<record_sha>.json
  collections/<collection_id>/<record_sha>.json
  candidates/<candidate_id>/<record_sha>.json
```

`manifest.json` enumerates every source, collection and candidate, including its path and hash. On load, TradeMaster reads this list and checks file hashes, DuckDB entries, dependency indexes and linked factor definitions. It reports an error if a record is missing, has changed, or shares an ID with a different record.

Replace `<factor-root>` in these commands with the factor store directory.

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

`library-list` returns stable IDs and status for the selected subset of the 460 records. `library-describe` returns the formula reference, inputs, blockers, variant differences and content hash for one candidate.

## Requirements for promoting more candidates

1. Add `pe_ttm/pb/ps_ttm/dv_ttm` and typed PIT financial indicators to the formal M1 schema.
2. Provide a research view with adjusted OHLCV/VWAP. Keep raw execution prices in the execution layer.
3. Version the rolling, rank, correlation, regression, SMA and decay operator conventions.
4. Retain the source AST, approved AST, errata and golden cases for each candidate.
5. Establish full-universe historical daily bars, daily turnover, stock states and industry membership before assessing IC or quantile returns on an unbiased sample.
