# Trading the SSE 50 ETF on an index signal

How do you backtest a signal computed on a non-tradable benchmark index while executing orders, tracking FIFO lots, and collecting 15 years of cash distributions on a separate exchange-traded fund? Spanning 3,644 Shanghai sessions from January 4, 2011 through December 31, 2025, the [README benchmark](../../README.md#fifteen-year-benchmark) tracks two CNY 100,000 accounts that observe daily closes on the SSE 50 index (`000016.SH`) and trade the ChinaAMC SSE 50 ETF (`510050.SH`, listed in 2005)—reconciling raw ETF fills and all 14 historical cash dividends in under 100 ms end-to-end. [Fund description and listing date](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2025-05-30/510050_20250530_2LH9.pdf)

## Prepare and run

From the repository root, run `uv sync --locked` and export `TUSHARE_TOKEN` in your shell to build the local data snapshot once; all subsequent benchmark and replay runs execute offline against the saved market, strategy, and cost assumptions.

```bash
uv run python -m trademaster.e2e.long_history prepare
uv run python -m trademaster.e2e.long_history run --check docs/examples/broad-etf-reference.json
uv run tm-replay artifacts/broad-etf/sma200/request.json > replay.json
```

The data snapshot bundles the SSE trading calendar, daily `000016.SH` index closes, unadjusted `510050.SH` bars, opening side permissions, and the fund's cash dividend schedule. Each decision observes the snapshot as of the 15:00 Shanghai exchange close (Tushare archives the index value later that evening, well before the next session's open), and the preparation step cross-checks ETF previous-close references and adjustment factors against the published distribution schedule. [Index quotes](https://tushare.pro/document/1?doc_id=95) · [Fund distributions](https://tushare.pro/document/2?doc_id=120)

`510050.SH` trades in 100-unit lots on a CNY 0.001 price tick. To establish opening buy and sell permissions, the loader computes 10% price limits from the exchange reference close rounded to CNY 0.001: Tushare provides three-decimal limits on 632 sessions and cent-rounded limits on 952 sessions, while the exchange rule supplies the remaining 2,060 sessions. [ETF price limits](https://tushare.pro/document/2?doc_id=491) · [ETF trading rules](https://www.sse.com.cn/disclosure/fund/announcement/c/2021-01-22/510050_20210123_6.pdf)

## Orders, distributions and valuation

After a 200-session warm-up on `000016.SH`, the timing strategy compares each day's index close against the arithmetic mean of the latest 200 index closes (inclusive), submitting a 100% `510050.SH` target when the index crosses above its moving average and a 100% cash target when it crosses at or below it. The buy-and-hold benchmark submits a single 100% ETF target at the first post-warm-up close under identical execution rules and fees.

Before inspecting the next eligible open, Rust sets aside any unallocated cash reserve and finalizes whole-lot order quantities from the prior ETF closing mark and settled cash, budgeting buys at a 2% price cap above the prior close plus commission. At the open, the engine blocks buys at the upper limit and sells at the lower limit, enforces the 2% buy cap, and retries unfilled targets across up to three opens. Small eligible orders fill in full at the opening price (modeling partial fills or queue priority would require opening-auction depth data), paying a 0.03% commission rounded up to the cent with a CNY 5 minimum and zero stamp duty, transfer fees, or slippage.

Cash dividends on raw ETF prices follow a three-step accrual schedule across the 14 distributions in the window: units held at the record-date close earn the distribution, the unpaid receivable enters account equity on the ex-dividend date, and the payment settles into cash after the pay-date open—becoming available to fund new orders from the next trading session. In December 2025, for example, `510050.SH` recorded holders at the December 16 close, went ex-dividend on December 17, and paid CNY 0.080 per unit on December 22. Neither strategy sweeps idle dividend cash until a new signal triggers rebalancing. [Fund distribution history](https://fund.chinaamc.com/product/fundLishifenhong.do?fundcode=510050) · [December 2025 announcement](https://www.chinaamc.com/upload/resources/file/2025/12/09/442028.pdf)

## Follow a trade through the account

When `000016.SH` first closed above its 200-day average after warm-up on Friday, February 24, 2012, Python requested 100% weight in `510050.SH`. Because pre-open sizing reserves buffer cash up to the 2% buy cap, Monday, February 27 filled 53,200 units at CNY 1.847 (CNY 29.48 commission), and the unspent cash freed by filling below the cap funded another 900 units on Tuesday at CNY 1.842 (CNY 5 commission)—illustrating how a single target batch can span consecutive openings as realized fill prices become known.

By the final close on December 31, 2025, the timing strategy held 63,700 ETF units and CNY 5,188.82 in cash after collecting CNY 23,896.90 across eight distributions, while buy-and-hold held 55,600 units and CNY 36,311.55 in cash—including CNY 36,140 across all 14 distributions—with every accrued dividend settled by period end. [Daily closing equity](broad-etf-equity.svg) · [Cumulative P&L](broad-etf-pnl.svg)

## Reproducing the benchmark

Each benchmark run writes `benchmark.json` (snapshot hash, native extension checksum, strategy summaries, and wall-clock timings), `equity.csv` (daily closing equity for both accounts), and per-strategy subdirectories containing replayable `request.json` files and full Rust ledgers. Cold runs include opening the data snapshot and indexing the market, whereas warm repeats reuse the in-memory snapshot with a fresh account; output hashing, disk serialization, and `tm-replay` checks run outside the timer.

On an AMD Ryzen 7 8845H (WSL2, Python 3.12.3), median cold runtime across nine runs was 97.5 ms for the 200-day timing strategy and 93.0 ms for buy-and-hold, dropping to 51.8 ms and 43.1 ms once the snapshot was already open. Passing `--check` validates the snapshot, index features, and both complete ledgers against [`broad-etf-reference.json`](broad-etf-reference.json); for suspension and stock-fee regression checks on a single equity, see the [`000702.SZ` fifteen-year benchmark](long-history.md).
