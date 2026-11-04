# Backtest performance review

Three independent reviews examined the Python strategy and result API, the Rust daily loop, and market loading and replay at commit `5dc86ce`. The measurements below used the optimized Rust extension, Python 3.12 and a Ryzen 7 8845H under WSL2. The ETF workload uses 3,644 sessions, 14 cash distributions and daily strategy calls. Each timed run on an open snapshot starts with a new account. [Benchmark inputs and reference results](../examples/broad-etf.md)

## Real data

| Experiment | Original | Prototype | What was timed |
|---|---:|---:|---|
| Skip settlement audit records on opens with no lots to unlock, SSE 50 timing | 62.21 ms | 57.96 ms | Full Python SDK run on an open snapshot, detailed ledger, 11 paired runs |
| Same change, buy and hold the ETF | 46.98 ms | 35.22 ms | Same market and settings |
| Read feature columns directly, ETF snapshot | 66.40 ms | 48.85 ms | Opening and verifying the snapshot, nine paired runs |
| Same change, original single-stock snapshot | 76.03 ms | 68.69 ms | Same loading procedure |
| Update the three MACD averages as new prices arrive | 5.43 ms | 2.23 ms | Real 98-session five-stock snapshot, original strategy versus the new README implementation, detailed SDK result, 35 paired runs |

Each experiment compared one change with its own baseline. Both implementations ran on the same inputs in alternating order after warm-up. The settlement and MACD comparisons matched the full saved targets and ledger. The loading experiment also checked prices and as-of features for both markets. Short five-stock runs did not give a consistent timing improvement for the settlement change. The figures are separate measurements of particular workloads.

**Settlement.** The snapshot loop requests a ledger effect at every opening after the first. Even an opening with no unlock builds a full account snapshot and hashes it before returning an empty effect. The buy-and-hold run had 3,641 such openings and the timing run had 3,569. The prototype skipped this step only on an uneventful opening in the snapshot path and retained the backward-time check. Keep the event runner’s audit records, T+1 availability, dividend timing and partial-run diagnostics when implementing this change. [Daily loop](../../crates/tm-engine/src/snapshot/mod.rs) · [Lot settlement](../../crates/tm-engine/src/lib.rs)

**Snapshot loading.** The loader sorts the feature table and builds a Python dictionary for every row. Isolated on the ETF, sorting took 0.18 ms while sorting and converting all rows took 20.33 ms. Verifying both file hashes took 0.49 ms. Reading the required columns directly and retaining timezone-aware publication times improved the first run. Repeated strategies already share the opened market. [Feature loading](../../python/trademaster/backtest/snapshot.py)

**Strategies and results.** The daily MACD change is implemented in the README example and the default snapshot demo. The original strategy revisited all prior closes to calculate each new target. The incremental calculation uses the first complete window for each EMA and restarts after a missing price. For a single precomputed buy-and-hold target over 3,644 sessions, the summary-mode SDK took 26.92 ms. Reusing an already-converted raw result and measuring Python result construction alone took 6.66 ms. A single pass over daily valuations is a candidate for a separate experiment. Preserve Decimal amounts and incomplete-run summaries. [Strategy calculation](../../python/trademaster/strategies/demo.py) · [Result construction](../../python/trademaster/backtest/__init__.py)

## Broad portfolios and the event runner

After allocating whole lots, the Rust allocator scans the remaining buy candidates to find which should receive the next affordable lot. In a two-day synthetic test with 1,000 equal-weight buy candidates, the first allocation left cash for 500 more lots. The scan checked 501,000 candidates. A priority queue ordered by remaining shortfall and instrument ID reduced the median detailed Rust session from 69.90 to 1.27 ms across 11 pairs. All opening orders were blocked to isolate sizing. The JSON results matched in this case and 48 additional generated cases. With 200 candidates and about 10% of buys allowed, runtime was 2.03 versus 0.61 ms. When all 200 buys were allowed, it was 32.40 versus 33.93 ms, with overlapping samples. The priority queue matters most when allocation takes a large share of runtime. [Lot allocation](../../crates/tm-engine/src/snapshot/sizing.rs)

On a synthetic 252-session, 5,000-symbol calendar with constant listings, constructing the Python decision views took 99.9 ms. Requesting membership at every close increased that to 145.5 ms. Reusing the ordered universe and membership set between listing changes could help broad-universe strategies. This measures view construction alone. [Decision views](../../python/trademaster/backtest/snapshot.py)

The separate saved-event runner validates symbols and trading dates against sorted lists using linear searches. In an archived 201-stock run, checking 169,393 bars required 16,775,758 symbol comparisons. A binary search can use the ordering already validated at input. This is a count of comparisons in the original algorithm. In a separate ten-industry archive the event loop would also copy 35.24 million historical bar records while preparing inputs for 893 events. A scheduled strategy that only consumes the current bar could use a borrowed history view or skip that copy. End-to-end replay speed remains to be measured. [Event loop](../../crates/tm-engine/src/lib.rs) · [Event request decoder](../../crates/tm-runner/src/lib.rs)
