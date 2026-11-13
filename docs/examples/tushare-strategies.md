# Three strategies on the same Tushare history

How do you acquire a verified Tushare Pro market cache once and evaluate multiple cross-sectional and single-stock strategies against it offline? Starting with CNY 100,000 per account across 98 Tushare trading sessions (January 2 through May 31, 2024), this walkthrough runs three close-time strategies through both execution paths: the **event runner** (which sizes target weights at an eligible open and writes standalone HTML audit bundles) and the **snapshot runner** (which finalizes quantities from prior closes, reserves unallocated cash upfront, and writes portable requests against a shared data snapshot). [Compare the two execution paths](rebalance-comparison.md)

| Strategy | Decision |
|---|---|
| Weekly cross-sectional momentum | On the last trading day of the week, rank five stocks by their 20-session returns. Target 50% in each of the top two and exit the rest. Break ties by instrument ID. |
| Single-stock moving average | For China Merchants Bank (`600036.SH`), enter when the 5-session simple moving average crosses above the 20-session average. Exit on a downward cross. |
| Single-stock MACD | For the same stock, subtract the 26-session exponential moving average from the 12-session average. Enter when this line crosses above its 9-session signal average. Exit when it crosses below. Seed the price averages with 12 or 26 closes and the signal average with the first nine MACD values. |

All three strategies observe the candidate universe (`000001.SZ`, `000725.SZ`, `000977.SZ`, `600036.SH`, and `600050.SH`) as of each trading day's exchange close, with orders first eligible at the next session's open. Indicators are computed on back-adjusted closes (`close × adj_factor`), whereas order sizing, fills, and daily mark-to-market valuation use raw unadjusted prices under TradeMaster's default A-share stock fees and zero slippage. Each account stays in cash during its initial indicator warm-up window.

## Run the examples

Run all commands from the repository root in a single shell after installing the Python package and native Rust extension:

```bash
uv sync --locked --extra dev
```

**Build a new cache.** Export `TUSHARE_TOKEN` in your shell environment. `plan` fetches stock listings and the exchange calendar, and `download` acquires session-by-session market bars and trading-status tables across the market so the demo can slice its five stocks locally. Include `2024-06-03` so the calendar records the first eligible open following the final May 31 close:

```bash
DATA_ROOT=data/research/demo
PLAN=$(
  uv run tm-research-full-a --data-root "$DATA_ROOT" plan \
    --start 2024-01-02 --end 2024-06-03 --statement-start 2023-01-01 |
    uv run python -c 'import json, sys; print(json.load(sys.stdin)["plan_sha256"])'
)
printf 'Plan ID: %s\n' "$PLAN"
uv run tm-research-full-a --data-root "$DATA_ROOT" download \
  --plan-sha256 "$PLAN" --phase market --phase status
```

If an interruption stops `download`, re-run the same command with `$PLAN` to resume; unrelated research phases (`statement`, `industry`, `benchmark`) can remain pending, as each runner validates only the inputs it consumes.

**Reuse an existing cache.** If you already have a local cache covering those sessions, market bars, and status tables, skip planning and set `DATA_ROOT` and `PLAN` directly (for the 20-year research cache behind the published figures, use `DATA_ROOT=data/research/full-a-20y` and `PLAN=6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`).

**Execute both engines offline.** Run the snapshot and event paths against the same `DATA_ROOT` and `PLAN`, writing to separate fresh output directories:

```bash
uv run python -m trademaster.e2e.demo_strategies --engine snapshot \
  --data-root "$DATA_ROOT" --plan "$PLAN" --output artifacts/demo-snapshot
uv run python -m trademaster.e2e.demo_strategies --engine events \
  --data-root "$DATA_ROOT" --plan "$PLAN" --output artifacts/demo-strategies
```

Before executing, the loader verifies the plan's trading calendar alongside the cached Tushare daily bars, adjustment factors, price limits, and suspension records—halting on any missing bar among the five candidates or any intra-window change in `adj_factor` (since splits or cash dividends within the window would require explicit lot or cash-receivable adjustments).

| Output directory | What to inspect |
|---|---|
| `artifacts/demo-strategies/<strategy>/` | Event-runner decisions, Rust request and result, and standalone `report.html` (equity curve, costs, and benchmark), verified via request replay before `summary.json` is written. |
| `artifacts/demo-snapshot/<strategy>/` | Portable `request.json` and `result.json` referencing the shared data snapshot in `artifacts/demo-snapshot/snapshot/` (replayable via `uv run tm-replay artifacts/demo-snapshot/macd/request.json`). |

Open `artifacts/demo-strategies/cross-sectional-momentum/report.html` in a browser to inspect the weekly momentum tear sheet, or see the [side-by-side execution comparison](rebalance-comparison.md) for daily cash, equity, and fill-level attribution across both engines.

## Event-runner results for January through May 2024

| Strategy | Buys / sells | Costs (CNY) | Ending equity (CNY) | Net return | Max drawdown |
|---|---:|---:|---:|---:|---:|
| Weekly cross-sectional momentum | 16 / 15 | 696.06 | 104,666.94 | +4.67% | -8.81% |
| 5/20 moving average | 1 / 1 | 114.55 | 107,115.45 | +7.12% | -5.63% |
| 12/26/9 MACD | 2 / 2 | 224.31 | 106,825.69 | +6.83% | -4.24% |

Both single-stock strategies entered 3,000 shares of China Merchants Bank (`600036.SH`) at CNY 32.34 on March 28: the 5/20 moving average held continuously until exiting on May 29, whereas MACD exited on April 15, re-entered on April 16, and exited again on May 24. All three runs finished with zero rejected orders.

## Check the results

```bash
uv run pytest -q tests/test_demo_strategies.py tests/test_demo_data.py tests/test_demo_runner.py
TRADEMASTER_DEMO_DATA_ROOT="$DATA_ROOT" TRADEMASTER_DEMO_PLAN="$PLAN" \
  uv run pytest -q tests/test_tushare_demo_strategies.py
```

The end-to-end test suite verifies next-open execution timing, matches every simulated fill against recorded opening prices, and reconciles final cash from fills and itemized fees; when `TUSHARE_TOKEN` is exported, it also queries Tushare Pro live to cross-check every daily OHLCV bar inside the Rust request.
