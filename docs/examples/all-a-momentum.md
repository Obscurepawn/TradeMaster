# Weekly momentum across the A-share market

The [README strategy](../../README.md#weekly-stock-selection-across-the-a-share-market) starts with CNY 1 million. At each week’s final close it ranks all A-shares then listed in Shanghai, Shenzhen and Beijing. Each stock’s score is its adjusted close divided by its adjusted close 20 trading sessions earlier, minus one. It buys up to ten stocks with a target of 10% each. A missing endpoint leaves that stock out of the ranking. The calendar includes suspension dates, so the return covers the same sessions for every stock.

## Run on the historical universe

From the repository root, build the [TuShare research panel](../full-a-factor-research.md) and use its published manifest. This example requires market data from July 3 through August 14, 2026. The prepared snapshot ends on August 13 and records August 14 as its next trading session. The default manifest is the one recorded in the research guide. For a separately materialized panel covering these dates, pass its manifest path to the first command with `--panel-manifest`.

```bash
uv run python specs/examples/prepare_all_a_snapshot.py
uv run python specs/examples/all_a_momentum.py artifacts/all-a-momentum/snapshot --output artifacts/all-a-momentum/run
uv run tm-replay artifacts/all-a-momentum/run/request.json
```

The [preparation script](../../specs/examples/prepare_all_a_snapshot.py) verifies the panel manifest and data hash. The snapshot includes the 5,551 stocks that were listed for at least one session in the sample. `day.instruments` follows the listing dates at each close. In the [strategy](../../specs/examples/all_a_momentum.py), `schedule="week_end"` controls the decision dates. Rust still processes prices, orders and balances every trading session. You can also pass a smaller CNY stock snapshot to the strategy command to test the same rule on a smaller universe.

## From selection to fills

| Decision close | Eligible stocks with both prices / listed | Stocks selected | First eligible open |
|---|---:|---:|---|
| July 31, 2026 | 5,506 / 5,534 | 10 | August 3 |
| August 7, 2026 | 5,513 / 5,539 | 10 | August 10 |

The first simulated fill bought 8,000 shares of `001258.SZ` at CNY 12.25 on August 3. Across the two rebalances there were 49 fills and 14 rejected orders. Eight orders exceeded the strategy run’s buy-price cap, five encountered suspensions and one buy encountered an opening at the upper limit. Both targets expired after three attempts before the account reached the requested weights. Closing equity on August 13 was CNY 1,151,755.88 and total trading costs were CNY 897.12. The saved request reproduces the complete ledger. An independent calculation from the original stock master, raw closes and adjustment factors matched every selected stock and weight on both dates.

The simulation uses adjusted closes for ranking and raw stock prices for fills and valuation. Opening permissions come from the recorded price limits and suspension evidence. All boards participate in ranking. For STAR Market and Beijing stocks, the snapshot marks the opening permission as unknown because the example does not model their order-size rules. A planned order that requires unknown opening evidence stops the run. The selected stocks had sufficient opening evidence in this sample. A missing or changed adjustment factor or an unexplained change in the raw previous-close reference flags a corporate-action gap. A holding with such a gap stops the run before its value is reported. The example uses the SDK’s default stock fees, zero slippage, a 2% buy-price cap and up to three opening attempts per target.
