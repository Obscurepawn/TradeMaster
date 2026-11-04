# Trading the SSE 50 ETF on an index signal

The [README benchmark](../../README.md#fifteen-year-benchmark) follows two CNY 100,000 accounts from January 4, 2011 through December 31, 2025. The SSE 50 index (`000016.SH`) provides the signal. ChinaAMC SSE 50 ETF (`510050.SH`), listed in 2005, provides the opening and closing prices and pays cash distributions to eligible holders. [Fund description and listing date](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2025-05-30/510050_20250530_2LH9.pdf)

## Prepare and run

From the repository root, run `uv sync --locked`. Set `TUSHARE_TOKEN` in the shell before preparing the data. Rerun the saved snapshot offline with the same market, strategy and assumptions.

```bash
uv run python -m trademaster.e2e.long_history prepare
uv run python -m trademaster.e2e.long_history run --check docs/examples/broad-etf-reference.json
uv run tm-replay artifacts/broad-etf/sma200/request.json > replay.json
```

The snapshot contains the SSE trading calendar, daily index closes, unadjusted ETF bars, opening permissions and the cash dividend schedule. The strategy uses the exchange close at 15:00 Shanghai time. TuShare archives the daily index value later that evening, before the next eligible opening. Preparation checks the dates and the ETF's previous closes and adjustment factors against the distribution history. [Index quotes](https://tushare.pro/document/1?doc_id=95) · [Fund distributions](https://tushare.pro/document/2?doc_id=120)

The ETF trades in lots of 100 units at a price tick of CNY 0.001. For opening permissions the loader calculates the 10% price limits from the exchange reference close and rounds to the ETF tick. TuShare supplies both limits at that precision for 632 sessions and at cent precision for another 952. For the other 2,060 sessions the exchange rule supplies the limits. [ETF price limits](https://tushare.pro/document/2?doc_id=491) · [ETF trading rules](https://www.sse.com.cn/disclosure/fund/announcement/c/2021-01-22/510050_20210123_6.pdf)

## Orders, distributions and valuation

The strategy compares the current index close with the arithmetic mean of the most recent 200 index closes, including today. After warm-up, a change to above the average requests 100% ETF. A change to at or below the average requests cash. Buy-and-hold submits one 100% ETF target at the first eligible decision close. Both use the same opening constraints and costs.

Rust sizes in whole lots using the previous ETF close and available cash. At the next eligible open it checks the permitted side, a 2% buy cap and the cash budget. A target can be attempted at up to three opens. The model blocks buys at the upper limit and sells at the lower limit. It assumes a small eligible order fills in full at the first reported trade price. Estimating partial fills or queue position would require opening-auction data. For both accounts, commission is 0.03% per fill, rounded up to the next cent with a CNY 5 minimum. Stamp duty, transfer fees and slippage are zero in this benchmark.

The fund publisher records 14 distributions in the sample. The holder at the record close earns the dividend. On the ex-date the account includes the amount receivable in equity. On the pay date it becomes cash after the open and can fund orders from the following session. For example, in December 2025 the fund recorded holders on December 16, went ex-dividend on December 17 and paid CNY 0.080 per unit on December 22. Both strategies retain dividend cash until a new target calls for trading. [Fund distribution history](https://fund.chinaamc.com/product/fundLishifenhong.do?fundcode=510050) · [December 2025 announcement](https://www.chinaamc.com/upload/resources/file/2025/12/09/442028.pdf)

## Follow a trade through the account

Friday, February 24, 2012 was the first close above the average after warm-up. Python requested 100% in the ETF. On Monday, February 27 the first order bought 53,200 units at CNY 1.847 with CNY 29.48 commission. On Tuesday the remaining cash funded a further 900 units at CNY 1.842 with CNY 5 commission. A single target can generate fills at more than one opening as each day's actual spend becomes known.

At the end of 2025 the timing strategy held 63,700 units and CNY 5,188.82 in cash. Across eight distributions it had received CNY 23,896.90. Buy-and-hold held 55,600 units and CNY 36,311.55, including CNY 36,140 in distributions from all fourteen payments. Both accounts had received every dividend due by the last close. [Daily closing equity](broad-etf-equity.svg) · [Cumulative P&L](broad-etf-pnl.svg)

## Reproducing the benchmark

`benchmark.json` contains the snapshot identity, native extension checksum, strategy outcomes and timings. `equity.csv` contains both accounts at every close. The two strategy directories hold the dated requests and full Rust ledgers. Each timed run uses a new account. The first timed run includes opening the snapshot and building the market index. A repeat reuses that open snapshot. Hashing the outputs, saving them and replaying the requests take place outside the timer.

On a Ryzen 7 8845H running WSL2 and Python 3.12.3, the median of nine first runs was 97.5 ms for the timing strategy and 93.0 ms for buy-and-hold. With the market already open, the medians were 51.8 and 43.1 ms, respectively. The [reference file](broad-etf-reference.json) identifies the market, the index features and the two complete ledgers.

Use the [single-stock regression](long-history.md) to compare changes against the original `000702.SZ` ledger, including its seven suspension dates.
