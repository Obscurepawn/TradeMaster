# Fifteen-year backtest benchmark

**Implemented and verified November 5, 2026.** The [single-stock benchmark](../docs/examples/long-history.md) measures a daily Python strategy on a fixed historical workload. The [broad ETF example](20261105-broad-etf-benchmark.md) is the README workload. The [run guide](../docs/examples/long-history.md) gives the data requirements, accounting assumptions and replay commands.

Use the Shenzhen trading calendar from 2011 through 2025 and the TuShare history for `000702.SZ`. This stock has both long price history and explicit suspension records. Its adjustment factor is constant during the sample, so the ledger can use the original share counts and prices across all fifteen years.

Compare a 200-close moving-average rule with a single buy-and-hold target. Both accounts start with CNY 100,000, use the same warm-up and trading assumptions, and submit their first eligible order after a decision close. Include a suspended day in the trading calendar and exclude the missing quote from the average. Record dates, costs, closing equity, maximum drawdown, target counts and fill counts. Time fresh and repeated runs separately.

Acceptance requires matching input identities from the provider and the local research panel, agreement with an independent calculation of the decision dates, replay of the saved requests, and repeated results with identical digests. The expected ledger is a correctness check. Runtime is a measurement of the machine that produced it.


## Verification

The initial tests failed because the data loader and benchmark harness were missing. Eight focused tests now cover the provider inputs, missing quotes, opening restrictions, warm-up, equality at the moving average, repeated results and reference mismatches. A direct TuShare download and the local research panel produced byte-identical manifests and market files. The [expected results](../docs/examples/long-history-reference.json) match both independently prepared inputs and a run from a clean wheel installation.

An independent rolling-price calculation matched all 164 moving-average targets. Reconstructing both accounts from their fills, fees and raw closes matched every daily valuation and maximum drawdown. The offline Python suite passed 310 tests. Three separate provider-network tests were skipped in that run.
