# Broad ETF execution benchmark

**Implemented and verified November 5, 2026.** Use an exchange-traded fund in the README to show how a close-time index signal becomes an order, a fill and an account value. The original fifteen-year single-stock workload remains available as a ledger regression.

Both strategies start with CNY 100,000 on January 4, 2011. Observe the SSE 50 price index (`000016.SH`) at its close. Starting at the 200th index close, target the ChinaAMC SSE 50 ETF (`510050.SH`) when the index is above its rolling average and cash otherwise. The control targets the same fund once at the end of the common warm-up. Use actual ETF opens for matching and raw closes for valuation. Use 100-unit lots, a CNY 0.001 price tick, T+1 sellability and the same fixed commission for both accounts. Hold the cash distributions in the account until the strategy issues another target.

For each cash distribution, determine the units entitled at the record close. Include the receivable in equity on the ex-date and credit cash after opening trades on the payment date. Match observed adjustment-factor changes and changes in the published previous close to the distributions. The benchmark should reproduce the original stock workload when selected separately.

Verify all scheduled decisions against index closes, every fill against ETF opening prices and every cash, equity and dividend balance against a second calculation. Measure complete runs with and without reopening the snapshot, and publish a reference for deterministic replay.

## Implementation and verification

The [ETF loader](../python/trademaster/e2e/broad_etf_data.py) prepares 3,644 trading sessions and 14 fund distributions. The [strategy and benchmark](../python/trademaster/e2e/long_history.py) run both accounts on the same saved market. The [cash dividend API](../docs/reference/snapshot-backtest.md#cash-dividends) and [Rust dividend ledger](../crates/tm-engine/src/snapshot/dividends.rs) handle entitlements, opening budgets and payment. The [run guide](../docs/examples/broad-etf.md) records the data sources, trading assumptions and timing results.

The initial tests exposed missing ETF inputs, a missing dividend API and an index close carried forward from an earlier session. The final tests cover exact-date index observations, provider limit precision, duplicate distributions, selling on the ex-date, a payment beyond the last close, and same-day ex-date and payment. The full offline test run passed 329 Python tests and 58 Rust tests. Three separate network tests were skipped in the offline run.

The public preparation command reproduced the reference snapshot from TuShare. A second calculation from the source quotes matched every decision, fee, cash balance, dividend receivable and closing valuation. The timing strategy generated 79 targets, made 113 fills and ended at CNY 202,977.32. The control issued one target, made two fills and ended at CNY 208,949.55. An installed release wheel reproduced both [ETF ledger digests](../docs/examples/broad-etf-reference.json) and both [original stock digests](../docs/examples/long-history-reference.json). The saved ETF request also reproduced the full ledger through the replay command.

The [README P&L chart](../docs/examples/broad-etf-pnl.svg) plots each daily closing account value less the initial CNY 100,000. The [HTML report preview](../README.md#backtest-reports) comes from the separate January–May 2024 weekly momentum example.
