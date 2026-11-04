# Fifteen years on one stock

This benchmark runs two Python strategies through the same Rust ledger on 3,644 Shenzhen trading sessions, January 4, 2011 through December 31, 2025. The fixed workload makes it possible to compare engine changes using the same orders and daily account values. The stock, `000702.SZ`, was chosen for its historical coverage. All seven missing bars have suspension records, and its adjustment factor stayed at 2.553 throughout the period.

The README uses the [SSE 50 ETF benchmark](broad-etf.md) to illustrate execution on a broad equity fund. This page keeps the original stock inputs and results for regression checks.

## Reproduce the run

From the repository root, install the package with `uv sync --locked`. With `TUSHARE_TOKEN` set in the shell, prepare the snapshot once. Subsequent benchmark runs use the saved files offline.

```bash
uv run python -m trademaster.e2e.long_history prepare --workload stock
uv run python -m trademaster.e2e.long_history run --workload stock --check docs/examples/long-history-reference.json
uv run tm-replay artifacts/long-history/sma200/request.json > replay.json
```

The default output is `artifacts/long-history/`. Set `--snapshot` on either command to choose another market directory, or set `--output` on `run` to choose another result directory.

A local full-market research panel can supply the same input. Pass `--panel-manifest PATH` to `prepare`. The published sample uses panel manifest `9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89`. Input preparation checks the calendar, quotes, adjustment factors, opening price limits, suspensions and special-treatment records. The calendar also supplies January 5, 2026 as the next session after the sample. [TuShare daily quotes](https://tushare.pro/document/1?doc_id=27) · [Price limits](https://tushare.pro/document/2?doc_id=183) · [Suspensions](https://tushare.pro/document/2?doc_id=214) · [Special-treatment history](https://tushare.pro/document/2?doc_id=397)

## What each strategy trades

Both accounts start with CNY 100,000 in cash. The first eligible decision close is November 1, 2011, when the stock has 200 published closes. The moving-average strategy compares the latest close to the arithmetic mean of the last 200 quoted closes, including today. Above the mean it targets 100% in the stock. At or below the mean it targets cash. It submits a target only when the signal changes. On suspended days, the account carries the last price and the strategy waits for the next quote.

The buy-and-hold strategy submits a single 100% target on the first eligible decision date. It first buys at the following open. Both strategies use whole lots of 100 and the same fixed costs throughout the sample. Commission is 0.03% per fill with a CNY 5 minimum. Sales incur 0.05% duty. Shenzhen transfers and slippage cost zero in this workload. A buy is capped at 102% of the previous close, and a target can be attempted at up to three opens. These fixed assumptions make the accounting comparable across runs. [Strategy and timing code](../../python/trademaster/e2e/long_history.py) · [Input preparation](../../python/trademaster/e2e/long_history_data.py) · [Execution rules](../reference/snapshot-backtest.md)

The moving-average account ended with CNY 23,600.03 in cash. It paid CNY 5,995.97 in trading costs. Buy-and-hold ended with 17,300 shares worth CNY 115,564 and CNY 416.02 in cash. Its two fills cost CNY 33.98. [Original daily equity chart](long-history-equity.svg)

## Reading the measurements

`benchmark.json` records the measured times and correctness checks. `equity.csv` contains the two account values for every trading day. The `sma200/` and `buy_and_hold/` directories each contain a replayable request and a detailed ledger. Opening a fresh snapshot includes reading, verifying and indexing the market and preparing the decision data. Repeated runs reuse the open snapshot and start with a fresh account. The benchmark times the complete daily backtest, including Python decisions, Rust matching and construction of the Python result.

The [checked-in reference](long-history-reference.json) identifies the snapshot and hashes each complete ledger together with its dated targets. The `--check` option compares these hashes and reports a mismatch. Times are in milliseconds and can be compared separately.
