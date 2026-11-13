# Fifteen years on one stock

How do we verify that backtest engine refactors preserve exact A-share lot sizing, stamp duty, and suspension valuation over a multi-decade horizon? This regression benchmark runs a 200-session moving-average strategy and a buy-and-hold control through the Rust FIFO ledger across 3,644 Shenzhen trading sessions (January 4, 2011 through December 31, 2025) on `000702.SZ`—selected because all seven missing daily bars have documented trading suspensions and its adjustment factor (`2.553`) remained constant over all fifteen years.

While the README highlights the [SSE 50 ETF benchmark](broad-etf.md) to demonstrate cross-instrument index signals and cash distributions, this single-stock workload pins deterministic order sequences and daily equity hashes for engine regression testing.

## Reproduce the run

From the repository root, install the workspace with `uv sync --locked` and export `TUSHARE_TOKEN` once to materialize the local data snapshot; all subsequent benchmark runs and replays execute offline.

```bash
uv run python -m trademaster.e2e.long_history prepare --workload stock
uv run python -m trademaster.e2e.long_history run --workload stock --check docs/examples/long-history-reference.json
uv run tm-replay artifacts/long-history/sma200/request.json > replay.json
```

Artifacts default to `artifacts/long-history/` (override with `--snapshot` for the market directory or `--output` on `run` for results). If you already have a local all-A-share research panel, pass `--panel-manifest PATH` to `prepare` instead of querying Tushare; the checked-in reference was built from panel manifest `9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89`. Input preparation validates the Shenzhen calendar (including January 5, 2026 as the first post-sample session), raw daily quotes, adjustment-factor continuity, opening price limits, suspension dates, and special-treatment (ST) history. [Tushare daily quotes](https://tushare.pro/document/1?doc_id=27) · [Price limits](https://tushare.pro/document/2?doc_id=183) · [Suspensions](https://tushare.pro/document/2?doc_id=214) · [Special-treatment history](https://tushare.pro/document/2?doc_id=397)

## What each strategy trades

Both accounts start with CNY 100,000 in cash and observe the data snapshot as of each trading day's exchange close. Decisions begin on November 1, 2011—the session on which `000702.SZ` accumulates 200 published closes—with orders first eligible at the next session's open:

- **200-quote moving average (`sma200`):** Compares today's close against the arithmetic mean of the latest 200 quoted closes (inclusive), emitting a new target only on regime flips—100% stock strictly above the mean and 100% cash at or below it. On suspended sessions, the ledger marks holdings at the carried-forward close while the strategy waits for the next published quote.
- **Buy and hold (`buy_and_hold`):** Submits a single 100% stock target at the November 1, 2011 close for execution starting at the November 2 open.

Both runs enforce 100-share lots, a 0.03% per-fill commission (CNY 5 minimum), 0.05% seller stamp duty, zero Shenzhen transfer fee or slippage, a 102% buy-price cap relative to the prior close, and up to three opening attempts per target. Under those fixed rules, the moving-average account finished in cash at CNY 23,600.03 after paying CNY 5,995.97 in cumulative trading costs, whereas buy-and-hold finished with 17,300 shares valued at CNY 115,564 plus CNY 416.02 in cash (paying CNY 33.98 across two fills). [Strategy and timing code](../../python/trademaster/e2e/long_history.py) · [Input preparation](../../python/trademaster/e2e/long_history_data.py) · [Execution rules](../reference/snapshot-backtest.md) · [Original daily equity chart](long-history-equity.svg)

## Reading the measurements

Each run writes wall-clock timings and integrity results to `benchmark.json`, daily closing equity for both accounts to `equity.csv`, and replayable requests plus detailed ledgers under `sma200/` and `buy_and_hold/`. Cold runs time snapshot loading, verification, market indexing, and feature setup alongside execution; warm repeats reuse the open data snapshot with a fresh account, timing the full cycle of Python callbacks, Rust matching, and Python result construction in milliseconds.

Passing `--check` verifies the snapshot ID and the cryptographic hashes of each strategy's dated targets and complete Rust ledger against [`long-history-reference.json`](long-history-reference.json), isolating engine throughput changes from any unintended drift in accounting.
