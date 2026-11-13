# From target weights to a daily account

Why do two execution models produce subtly different fills from identical target-weight signals, and what did moving TradeMaster's daily backtest loop into an in-process Rust extension gain in latency and memory? This guide traces how the **snapshot runner** converts close-time portfolio weights into multi-day opening orders—contrasting its pre-open cash reservation and T+1 cash availability against the legacy **event runner** on both a minimal blocked-sale rotation and a 98-session Tushare market sample, followed by process-versus-native SDK benchmarks.

At each scheduled decision close (the data snapshot's as-of cutoff), a strategy observes available prices and features and returns a complete dictionary of target weights. Before inspecting the next session's open, Rust first sets aside the unallocated cash reserve (`equity × (1 − total_target_weight)`, rounded up to the cent) and then finalizes whole-lot order quantities from the prior session's closing marks using only settled cash above that reserve. Sale proceeds never fund buys at the same open; they enter the buying budget only at the next trading session's open.

## A blocked sale

Run the four-session acceptance example directly without a market-data token:

```bash
uv sync --locked --extra dev
uv run python specs/examples/blocked_rotation.py
uv run tm-replay artifacts/blocked-rotation/request.json
```

Starting on Friday with 8,000 sellable shares of A (closing at CNY 10) and CNY 20,000 cash, the strategy returns `{"B": Decimal("0.80")}`—aiming to exit A, allocate 80% to B, and retain 20% in cash under a CNY 5 per-fill fee, a 2% buy-price cap above the previous close, and up to three opening attempts.

| Session | Execution | Cash (CNY) | Closing equity (CNY) |
|---|---|---:|---:|
| Friday | Register the target after the close. | 20,000 | 100,000 |
| Monday | A opens at its lower limit; the sale is rejected, and all CNY 20,000 cash is set aside for the 20% reserve. | 20,000 | 92,000 |
| Tuesday | Setting aside the 20% reserve (`CNY 18,400`) leaves a CNY 1,600 pre-open buy budget—short of one 100-share lot of B at its cap—while A sells at CNY 9. | 91,995 | 91,995 |
| Wednesday | Tuesday's sale proceeds enter the buy budget, funding 3,600 shares of B at CNY 20. | 19,990 | 91,990 |

The saved ledger records three submitted orders, two fills, Monday's lower-limit sell rejection on A, and the unplaced buy demand on B; inspect `attempts` in `result.json` to audit the exact cash reserve, buying budget, and lot shortfall at every open. [Executable example](../../specs/examples/blocked_rotation.py) · [Rust acceptance tests](../../crates/tm-engine/tests/snapshot_backtest.rs)

## Three strategies on the same market

To measure how pre-open cash reservation affects live market histories, both the legacy event runner and the daily snapshot runner executed the exact same dated target batches—17 weekly cross-sectional momentum targets, three 5/20 moving-average targets, and five 12/26/9 MACD targets—from CNY 100,000 initial capital across 98 Tushare trading sessions (January 2 through May 31, 2024).

| Strategy | Fills, event → snapshot | Ending equity, event → snapshot (CNY) | Change (CNY) | Total fees, event → snapshot (CNY) | Closes with a different equity |
|---|---:|---:|---:|---:|---:|
| Weekly cross-sectional momentum | 31 → 48 | 104,666.94 → 104,322.15 | −344.79 | 696.06 → 787.85 | 74 of 98 |
| 5/20 moving average | 2 → 2 | 107,115.45 → 107,115.45 | 0.00 | 114.55 → 114.55 | 0 of 98 |
| 12/26/9 MACD | 4 → 5 | 106,825.69 → 106,823.67 | −2.02 | 224.31 → 228.33 | 31 of 98 |

![Daily closing equity in CNY for the three snapshot strategies on the same 98 sessions.](rebalance-equity.svg)

Every divergence traces directly to same-open proceeds and price-cap budgeting:

- **Weekly cross-sectional momentum (`−CNY 344.79`):** On February 26, both engines sold 1,600 shares of `600036.SH` at the open. Whereas the event runner immediately recycled those proceeds to buy 5,000 shares of `000001.SZ` that same morning, the snapshot runner could fund only 100 shares from pre-open cash, buying 4,900 shares on February 27 and adjusting again on February 28—shifting interim exposure and adding CNY 91.79 in fees across the window. Its final Friday decision (May 31) fell on the last close of the sample with no in-window open left to trade.
- **Single-stock moving average (`CNY 0.00`) and MACD (`−CNY 2.02`):** Both strategies entered 3,000 shares of `600036.SH` on March 28, and the 5/20 moving average matched identically on every session. MACD diverged only on its April 16 re-entry: budgeting against the 2% buy cap limited the snapshot runner to 2,900 shares on April 16 plus a 100-share top-up on April 17 (versus 3,000 shares at once in the event runner). Buying those final 100 shares at CNY 32.69 rather than CNY 32.71 saved CNY 2.00 in principal, while the extra fill added CNY 4.02 in commission, netting −CNY 2.02.

## Python SDK before and after the native extension

Replacing subprocess JSON marshaling with the in-process PyO3 Rust extension preserved 100% bit-level parity across all six runs on the five-stock, 98-session Tushare snapshot—matching every dated target, fill price, missed-order reason, fee, and closing equity—while newly exposing the live closing account and recent fills to `on_close`.

The medians below (25 trials after one warm-up per mode on an AMD Ryzen 7 8845H under WSL2, Python 3.12.3, release Rust 1.97.1) read **process SDK → native SDK**. Cold runs (`First run incl. open`) overlap with `Snapshot open` because they include reading and indexing a fresh data snapshot; `Repeat` reuses that in-memory index with a fresh account, and `Strategy` isolates Python callback execution during repeats.

| Strategy and output | Callbacks | Snapshot open (ms) | First run incl. open (ms) | Repeat (ms) | Strategy (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Weekly momentum, detail | 21 | 5.90 → 6.16 | 10.58 → 8.97 | 4.68 → 2.54 | 0.14 → 0.24 |
| Weekly momentum, summary | 21 | 5.77 → 6.22 | 9.99 → 8.53 | 3.99 → 2.07 | 0.17 → 0.21 |
| Moving average, detail | 98 | 5.92 → 6.38 | 9.98 → 8.64 | 3.93 → 1.87 | 0.84 → 0.89 |
| Moving average, summary | 98 | 6.00 → 6.16 | 9.86 → 8.30 | 3.82 → 1.81 | 0.89 → 0.88 |
| MACD, detail | 98 | 6.00 → 6.16 | 11.22 → 9.99 | 5.10 → 3.24 | 1.93 → 2.03 |
| MACD, summary | 98 | 5.80 → 6.28 | 10.96 → 9.71 | 4.88 → 3.16 | 1.96 → 2.04 |

Building the Rust market index once at `Snapshot.open` saves roughly 2 ms on every repeated detailed run. Note that the historical benchmark above timed the original MACD callback, which recomputed exponential moving averages across the full visible price history at each close; the [current snapshot demo](../../README.md#write-a-strategy) updates those EMAs incrementally once per close.

While the superseded process-based SDK binary stayed in the original benchmark environment, the repository preserves the [timing script at commit `5dc86ce`](https://github.com/Obscurepawn/TradeMaster/blob/5dc86ce81f45de862c42c24c2e074187d623fcf5/specs/examples/profile_backtest.py). To profile the current native SDK locally, prepare the five-stock snapshot and run `profile_backtest.py`, which rotates strategy execution order and writes all timing samples plus target and ledger digests to `artifacts/native-timings.json`. [Prepare the snapshot](tushare-strategies.md#run-the-examples) · [Current timing script](../../specs/examples/profile_backtest.py)

```bash
uv run python specs/examples/profile_backtest.py artifacts/demo-snapshot/snapshot --repeats 25 --output artifacts/native-timings.json
```

## Historical command-line timings

Prior to the in-process Python extension, CLI benchmarks measured a single release runner subprocess from process spawn to JSON stdout across 15 rotated trials after one warm-up per mode (AMD Ryzen 7 8845H, Linux x86-64 under WSL2, Rust 1.97.1, Python 3.12.3; peak RSS measured in an isolated process per mode). Snapshot timings include manifest verification and market-file parsing, whereas event requests embed their market event stream directly; Tushare ingestion, Python strategy evaluation, and HTML rendering sit outside this timer.

| Strategy | Original event detail (ms) | Snapshot detail (ms) | Snapshot summary (ms) | Peak RSS in KiB, event → detail → summary |
|---|---:|---:|---:|---:|
| Weekly momentum | 23.03 | 3.48 | 3.11 | 9,020 → 3,800 → 3,516 |
| Moving average | 8.11 | 2.52 | 2.39 | 5,052 → 3,668 → 3,536 |
| MACD | 7.66 | 2.25 | 2.24 | 5,180 → 3,576 → 3,532 |

Every timed replay emitted identical bytes to its warm-up, and summary and detail modes agreed across all daily valuations, target outcomes, order and fill counts, turnover, and costs. Running the full end-to-end Python CLI commands across all three strategies into fresh output directories took **3.77 seconds** on the event path (which renders and verifies standalone HTML audit bundles) versus **2.71 seconds** on the snapshot path (which writes one shared data snapshot alongside portable requests and ledgers), with both peaking near 475 MiB RSS inside Python data preparation rather than Rust execution. In those historical runs, momentum and moving-average callbacks read up to 21 sessions while MACD scanned full visible history; when sweeping execution assumptions against fixed signals today, pass a precomputed target table to avoid re-running Python callbacks.

## Repeat the Tushare comparison

After preparing the five-stock cache and legacy event bundles (preserving `DATA_ROOT` and `PLAN` in your shell; the published run verified 392 source objects across 490 stock-days), run `compare_rebalance` to verify the event bundles, replay every request, confirm identical target inputs, and audit trade-level differences. Because the May 31 decision is first eligible on June 3, it remains pending after the final May 31 valuation. [Prepare the Tushare runs](tushare-strategies.md#run-the-examples)

```bash
uv run python -m trademaster.e2e.compare_rebalance \
  --data-root "$DATA_ROOT" --plan "$PLAN" \
  --legacy-root artifacts/demo-strategies --repeats 15
```

Under `artifacts/rebalance-comparison/`, `comparison.json` records the snapshot ID, engine metadata, cost rules, target outcomes, and timing samples, accompanied by four per-strategy files for trade-by-trade reconciliation:

| File | Contents |
|---|---|
| `trades.csv` | Shares, volume-weighted fill prices and costs for both paths, grouped by session, instrument and side. |
| `equity.csv` | Cash and equity for both paths at every close, plus the equity difference. |
| `request.json` | A portable request pointing to the shared snapshot in the parent directory. |
| `result.json` | Orders, fills, reasons for missed trades, fees, daily valuations and per-target attempts. |
