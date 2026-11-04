# Semiannual fundamental strategy selecting up to five stocks per industry

Status: complete
Owner: Codex
Updated: 2026-08-12

## Strategy and report configuration

Rank stocks within each industry by a composite fundamental score. Select up to five per industry and equally weight all selected stocks. The standalone strategy accepts a run configuration, replays real Tushare data through the Rust event loop and produces the standard strategy report.

- **Universe.** Use the latest historical CSI 300 composition available at each rebalance. Keep `all_a_share` available as an extension. The historical experiment applies the current SW2021 level-one industry mapping to all dates because the source returned only current memberships.
- **Schedule.** Run from 2016-08-12 through 2026-08-11. Form signals at the close of the first trading day on or after the sixth of May and November and execute at the next trading session's open. Expect 20 rebalances.
- **Capital and sizing.** Start with 500,000 CNY. Rust converts equal target weights to quantities using NAV, opening prices and 100-share lots. Stocks that cannot be bought in one lot remain at zero. The report shows cash left over.
- **Benchmark and costs.** Use the CSI 300 price index `000300.SH`, commission of 3 bp with a 5 CNY minimum, 5 bp sell-side stamp duty, 0.1 bp SSE stock transfer fee and 5 bp slippage.

## Scoring

Within each SW2021 level-one industry, winsorize each indicator at 5%/95% and standardize it as a cross-sectional z-score. A missing value receives the neutral industry score of zero. Both `pe_ttm/pb` valuation fields must be positive, and a candidate needs at least five valid indicators.

| Group | Indicators and direction | Weight |
|---|---|---:|
| Value | Earnings yield `1/pe_ttm` 15%, book yield `1/pb` 10%, `dv_ttm` 10% | 35% |
| Quality | `roe` 15%, `grossprofit_margin` 10%, `ocf_to_or` 10% | 35% |
| Growth | `q_sales_yoy` 10%, `q_profit_yoy` 10% | 20% |
| Safety | Lower `debt_to_assets` scores higher, 10% | 10% |

Missing fields common in financial companies, such as `grossprofit_margin/roa`, incur no cross-industry penalty. Record each candidate's valid-field count in the selection artifact. Rank by descending score, then ascending `instrument_id`, and take `min(5, eligible_count)` per industry. The selected weights sum to exactly 1.00000000.

## Data, timing and execution

1. Read the token only from `TUSHARE_TOKEN`. Keep its value out of request keys, Parquet, DuckDB, snapshots and reports. Check the content-addressed Parquet cache for each Tushare request. DuckDB catalogs and queries the immutable Parquet objects.
2. A financial statement's `ann_date` must precede the signal date. Tushare does not provide the actual availability time of revisions. Use original versions with `update_flag=0` and exclude revisions with `update_flag=1`.
3. Use `daily_basic` from the previous trading day because the signal day's cross-section is published after the close. Use the latest historical `index_weight` available in each rebalance month for CSI 300 membership.
4. A live `index_member_all` probe returned current `is_new=Y` members and zero rows for `is_new=N`. This report uses the current SW2021 mapping throughout and discloses classification survivorship bias. Licensed historical industry data could replace that mapping.
5. Use `adj_factor` to normalize stock OHLC and limit prices to the period end. Continuous adjusted prices reduce spurious returns around corporate actions. Executing against these prices approximates distributions, splits and consolidations without accounting for each cash or stock movement.
6. Set the initial NAV to the cash account on 2016-08-12. Sample subsequent NAV and the benchmark on each calendar week's last trading day and annualize at 52 periods. Send the full trading calendar to Rust for next-open eligibility, exit retries and T+1. Weekly sampling does not generate rebalance orders.
7. Write separate Parquet files and snapshot manifests for every rebalance selection and for the complete market input. The bundle verifier rereads Parquet, replays Rust and rebuilds the report.

## Implementation and acceptance

| Task | Owner | Status | Evidence |
|---|---|---|---|
| Define scoring, selection and equal weighting | Codex | complete | 3 Python tests |
| Connect scheduled target weights to Rust | Codex | complete | 5 engine and 3 runner tests |
| Assemble strategy cache and PIT inputs | Codex | complete | 1,959 raw Parquet objects and DuckDB catalog |
| Run and report the real semiannual strategy | Codex | complete | 20 rebalances and matching replay hashes |
| Verify and document | Codex | complete | 142 Python tests and Python, Rust and package checks |

- [x] Hand calculations verify winsorization, z-scores, score direction and weights, Top5 ranking, tie-breaks and equal allocation.
- [x] At the open, Rust sizes the full target batch from current NAV, open prices and lot sizes, selling before buying. Rust alone updates fills, cash and positions.
- [x] Across ten years and 20 rebalances, exits use zero targets. A signal at the T close cannot execute before the T+1 open.
- [x] Tushare data is stored as Parquet with DuckDB-backed cache lookups. The second process made zero provider calls.
- [x] HTML shows strategy and benchmark returns, drawdowns, rolling risk, a monthly heat map, fees, industry and size exposures, and performance metrics. The credential scan found zero matches. Python, Rust, formatting, lint, typing and package checks passed.

## Decisions on 2026-08-12

An all-A-share Top5 portfolio would often hold more than 140 stocks. With 500,000 CNY and 100-share lots, many could not reach their target weights. The experiment therefore used historical CSI 300 constituents while keeping the selector and data interfaces independent of the universe. Rust calculated the actual holdings after costs, settlement rules and lot rounding.

The independent final review identified three High issues involving revised financials leaking into history, inconsistent application of the static industry mapping, and no retry after an exit failed. The fixes restrict financials to original versions, apply one static mapping and retry exits for 20 trading days. They superseded the earlier results.

## Results and verification

The final run produced 2,335 selection rows over 20 rebalances, 426 distinct stocks and 2,145 fills. Exit retries produced 383 rejections under trading restrictions. One residual holding, `601989.SH`, remained suspended and represented 0.91% of ending NAV. Total return was 81.04%, annualized return 6.23%, annualized volatility 13.64%, Sharpe 0.511 and maximum drawdown -27.43%. The CSI 300 price return was 41.57%. Ending cash was 21.06% of NAV and trading costs were 16,813.29 CNY.

The real cache held 1,959 request objects, including `fina_indicator=611`, `daily=446`, `adj_factor=446`, `daily_basic/index_weight=20` and `stk_limit=381`, plus calendar, industry and benchmark data. DuckDB verified the per-period Top5 and weight limits, original financial flags and announcement cutoffs. The 20 selection snapshots contained 2,335 rows with `financial_update_flag={'0'}`, zero future announcements, zero Top5 violations and zero weight-sum violations.

Extending the benchmark interval required one extra provider call for the final ten-year run. A fresh process using an invalid token replayed with zero calls. The four key files matched byte for byte, including request SHA `55202d...2d84`, result SHA `2d4a75...9346` and HTML SHA `195931...a2de`.

Failing tests preceded fixes to scoring, target-weight construction, weekly and adjusted-price helpers, report annualization at 52 periods, original financial selection, static industry mapping, zero-target exit retries and rejection counts. The following checks were recorded.

- `TUSHARE_TOKEN=<external-file> uv run pytest -q` returned 142 passed, 0 skipped.
- `uv run ruff check python tests` and `uv run mypy` passed.
- `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings` and `cargo test --workspace` passed.
- `uv build` and `cargo package --workspace --allow-dirty --no-verify` passed.
- The repository token scan had zero matches. External token file permissions were 0600.

## Changed files

- `specs/INDEX.md`
- `specs/20260812-industry-fundamental-top5.md`
- `python/trademaster/strategies/AGENTS.md`
- `python/trademaster/strategies/industry_fundamental_top5/`
- `python/trademaster/e2e/__init__.py`
- `python/trademaster/reporting/__init__.py`
- `crates/tm-engine/src/lib.rs`
- `crates/tm-engine/tests/scheduled_strategy.rs`
- `crates/tm-runner/src/lib.rs`
- `crates/tm-runner/tests/wire.rs`
- `tests/test_industry_fundamental_top5.py`
- `tests/test_fundamental_strategy_data_cache.py`
- `tests/test_reporting_report.py`

## Open questions

None. The universe, capital and date range remain run parameters independent of the strategy definition.
