# Three strategies on the same Tushare history

Three strategies start with CNY 100,000 each and use the same 98 TuShare trading sessions from January 2 through May 31, 2024. Each makes decisions at the close. The results below use the **event runner**, which sizes target weights at an eligible open. The **snapshot runner** can replay the same decisions, sizing orders from the previous close and reserving cash before the open. [Compare the two execution paths](rebalance-comparison.md)

| Strategy | Decision |
|---|---|
| Weekly cross-sectional momentum | On the last trading day of the week, rank five stocks by their 20-session returns. Target 50% in each of the top two and exit the rest. Break ties by instrument ID. |
| Single-stock moving average | For China Merchants Bank (`600036.SH`), enter when the 5-session simple moving average crosses above the 20-session average. Exit on a downward cross. |
| Single-stock MACD | For the same stock, subtract the 26-session exponential moving average from the 12-session average. Enter when this line crosses above its 9-session signal average. Exit when it crosses below. Seed the price averages with 12 or 26 closes and the signal average with the first nine MACD values. |

The fixed candidate list is `000001.SZ`, `000725.SZ`, `000977.SZ`, `600036.SH` and `600050.SH`. Signals use the close multiplied by that day's adjustment factor. Fills and valuations use the original prices. The window includes the initial indicator warm-up, during which the account stays in cash. These examples use the stock fees configured in TradeMaster and zero slippage.

## Run the examples

Run the commands from the repository root in the same shell. Install the package and its Rust extension first.

```bash
uv sync --locked --extra dev
```

**New cache.** Set `TUSHARE_TOKEN` in your shell environment. Planning fetches stock listings and the exchange calendar. The download fetches market and trading-status data by session across the market, then the demo selects its five stocks locally. Include June 3 so the calendar has the next eligible open after the final May 31 decision.

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

If the download is interrupted, rerun the download command with the same plan ID. Other research phases can stay pending. Each demo runner checks its required inputs before executing.

**Existing cache.** Skip planning and downloading if you already have the required sessions and market and status records. Set `DATA_ROOT` to that cache directory and `PLAN` to its saved plan ID. For the original twenty-year cache used for the published results, the values are `data/research/full-a-20y` and `6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`.

**Run both engines offline.** Keep the same `DATA_ROOT` and `PLAN` for both commands. Use separate output directories for the snapshot and the HTML reports. Choose fresh output directories when switching input data.

```bash
uv run python -m trademaster.e2e.demo_strategies --engine snapshot \
  --data-root "$DATA_ROOT" --plan "$PLAN" --output artifacts/demo-snapshot
uv run python -m trademaster.e2e.demo_strategies --engine events \
  --data-root "$DATA_ROOT" --plan "$PLAN" --output artifacts/demo-strategies
```

The loader reads the plan's trading calendar and verifies the saved TuShare daily bars, adjustment factors, price limits and suspension records. It stops on gaps in the selected stocks or a change in adjustment factor within the window. A split or dividend would require the corresponding adjustment to holdings or cash.

| Output directory | What to inspect |
|---|---|
| `artifacts/demo-strategies/<strategy>/` | The event runner saves the decisions, Rust request and result, and `report.html` with equity, costs and a benchmark. It replays the saved request and verifies the report before writing the three-strategy summary. |
| `artifacts/demo-snapshot/<strategy>/` | The snapshot runner saves `request.json` and `result.json` for each strategy. The shared data snapshot is in `artifacts/demo-snapshot/snapshot/`. Replay a saved strategy with `uv run tm-replay artifacts/demo-snapshot/macd/request.json`. |

Open `artifacts/demo-strategies/cross-sectional-momentum/report.html` in a browser for the weekly momentum report. Each engine also writes a `summary.json` in its output root. The [side-by-side comparison](rebalance-comparison.md) shows daily cash, equity and individual trades.

## Event-runner results for January through May 2024

| Strategy | Buys / sells | Costs (CNY) | Ending equity (CNY) | Net return | Max drawdown |
|---|---:|---:|---:|---:|---:|
| Weekly cross-sectional momentum | 16 / 15 | 696.06 | 104,666.94 | +4.67% | -8.81% |
| 5/20 moving average | 1 / 1 | 114.55 | 107,115.45 | +7.12% | -5.63% |
| 12/26/9 MACD | 2 / 2 | 224.31 | 106,825.69 | +6.83% | -4.24% |

Both timing strategies bought 3,000 shares of China Merchants Bank at CNY 32.34 on March 28. The moving-average strategy held until May 29. MACD sold on April 15, re-entered on April 16 and sold again on May 24. All three runs completed with zero rejected orders.

## Check the results

```bash
uv run pytest -q tests/test_demo_strategies.py tests/test_demo_data.py tests/test_demo_runner.py
TRADEMASTER_DEMO_DATA_ROOT="$DATA_ROOT" TRADEMASTER_DEMO_PLAN="$PLAN" \
  uv run pytest -q tests/test_tushare_demo_strategies.py
```

The end-to-end test checks the next-open timing, matches actual fills to opening prices, and reconciles the final cash balance from the fills and individual charges. When `TUSHARE_TOKEN` is set it also queries Tushare Pro for each stock and compares every daily open, high, low, close and trading volume with the Rust request.
