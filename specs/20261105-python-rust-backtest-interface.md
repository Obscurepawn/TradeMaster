# Python and Rust backtest interface

**Implemented and verified November 5, 2026.** Python loads the Rust engine as a native extension. At a scheduled close a strategy can use the cash, holdings and fills from the same run when it chooses its next target. Earlier releases collected all decisions before starting a Rust process. [Current SDK](../python/trademaster/backtest/__init__.py) · [Current daily engine](../crates/tm-engine/src/snapshot/mod.rs)

## Strategy API

Use `backtest(snapshot, strategy=..., schedule=..., initial_account=..., policy=..., detail=...)` and define `on_close(day)`. The SDK calls this method once per scheduled close. At a scheduled close, the SDK passes a `BacktestDay` with the existing `DecisionDay` market interface and two additional fields. Calling `snapshot.day(session)` outside a run still returns the ordinary market view.

| Value | What the strategy can read |
| --- | --- |
| `day.session`, `day.closes(...)`, `day.scores(...)` | The trading date, price history and published features through the decision cutoff. |
| `day.account` | Cash and closing equity in currency units as `Decimal`. `quantity(symbol)` and `sellable_quantity(symbol)` return integer shares at this close, including zero for an unheld symbol. |
| `day.fills` | Fills since the previous callback, in execution order. The first callback includes fills since the run began. Each fill identifies its session, instrument, side, quantity, execution price, fees and target batch. |

Account and fill values describe a particular close. A strategy can keep them for later comparison. Both fields are available with `detail=False`. The detail flag controls the history kept in the final report. Returning a mapping sets the complete target, `{}` requests cash, and `None` lets the current target continue under its existing retry policy.

This strategy buys B only after a sale of A has actually cleared the position. It issues each target once.

```python
from decimal import Decimal

from trademaster.backtest import BacktestDay


class SwitchAfterSale:
    def __init__(self):
        self.sell_requested = False
        self.buy_requested = False

    def on_close(self, day: BacktestDay):
        if day.account.quantity("A") > 0:
            if not self.sell_requested:
                self.sell_requested = True
                return {}
        elif not self.buy_requested:
            self.buy_requested = True
            return {"B": Decimal("0.80")}
        return None
```

With the [four-session fixture](examples/blocked_rotation.py), the strategy requests cash on Friday. A cannot sell on Monday, so the callback still sees 8,000 shares and returns `None`. On Tuesday the sale clears. The callback sees zero shares of A and CNY 91,995 in cash, then requests 80% B. On Wednesday Rust buys 3,600 shares of B. The Friday and Tuesday targets in the saved request distinguish this strategy from one that requested B immediately on Friday.

## Calls and data ownership

```text
backtest(snapshot, strategy=...)          backtest(snapshot, targets=...)
             |                                         |
             +----------- Python native extension -----+
                                      |
                         Rust runs each trading session
                         settle -> size -> match -> mark
                                      |
                         scheduled decision close?
                         strategy input: call Python with day, account, fills
                                         receive complete weights or None
                         table input:    use the recorded target for this date
                                      |
                         next eligible open -> ... -> BacktestResult
```

The Python SDK owns the strategy, the point-in-time feature view and the public result types. A small PyO3 extension connects it to `tm-engine`. The Rust engine owns the session cursor, pending targets, order quantities, matching, fees and ledger. The extension holds the verified market data and its indexes for reuse by backtests on the same open snapshot. Each run gets a fresh account, target history and result. [PyO3](https://pyo3.rs/main/) · [Mixed Python and Rust packages](https://www.maturin.rs/project_layout.html)

Python starts one native run. Rust advances through every session and calls back once at each scheduled, successfully valued close. The callback receives the session identifier and read-only account and fill data. The SDK joins these to the existing Python market view for that date. The public account converts money on access and looks up share quantities in the stored positions. The callback returns only the new target weights. Historical prices and the ledger stay with their owners. Weights and money cross the native boundary as checked, scaled integers. JSON remains the format for saved requests and results.

The Rust loop registers a callback target for the next open. A final-close target is recorded with its eligibility date beyond the sample. Precomputed `targets=` enter the same engine in a single call with zero strategy callbacks and retain explicit eligibility dates. An incomplete valuation ends the run with the existing `insufficient_data` result. A Python strategy exception stops the run and propagates its traceback to the caller.

## Migration and acceptance

| Step | Change | Acceptance |
| --- | --- | --- |
| 1. Separate the daily calculation from its caller | Extract a Rust session with an operation to process a trading day and one to register a dated target. Drive the current batch entry point through it. | The four-session ledger and all three saved TuShare snapshot results match the current engine, including fills, fees, cash and daily equity. |
| 2. Ship the extension | Build a mixed Python and Rust wheel with maturin. Route snapshot callbacks and precomputed target tables through the native extension. Remove the snapshot SDK's `runner=` argument and command lookup. | Install the wheel in a clean environment and run the three strategies plus the account-dependent example. Check callback counts, the two decision dates in the example, exception propagation and independent accounts on repeated runs. Verify package schemas and console entry points. |
| 3. Migrate event runs and replay | Call the existing Rust event calculation in process from the event workflow, report verification, demos and comparison tools. Offer `tm-replay REQUEST.json` as a Python command over the extension. Select the calculation by the saved schema ID. | Replay existing event and snapshot requests. Compare the archived event result bytes, snapshot result contents and event bundle hashes. Resolve market files relative to the saved request as before. |
| 4. Remove the process interface | Retire the stdin/stdout runner, Python command injection, `--runner` options and binary lookup after migrating their callers. Update installation, examples, diagrams and command references. | Run the SDK, demos, reports and replay from the installed wheel with no runner binary. Check the package for subprocess launches of the engine. |

The [SDK timing comparison](../docs/examples/rebalance-comparison.md#python-sdk-before-and-after-the-native-extension) records all three strategies in summary and detail mode. The comparison includes opening a new snapshot, a repeat on the same snapshot, callback counts and time in Python strategy code. Each result digest covers both dated targets and the complete Rust ledger.

The first Python regression failed on `day.account` in the original SDK. The first Rust session test failed because `PreparedMarket` and `SnapshotSession` did not exist. The [Python acceptance tests](../tests/test_snapshot_backtest.py) and [Rust ledger tests](../crates/tm-engine/tests/snapshot_backtest.rs) now exercise actual account-dependent decisions, partial fills, weekly callbacks, independent runs and saved replay. The [blocked rotation example](examples/blocked_rotation.py) accepts `--strategy after-sale`. Its Friday and Tuesday targets produce the 3,600-share B holding and CNY 91,990 final equity shown above.


A clean Linux wheel installation ran both four-session examples and all three TuShare strategies. Its event results, report inputs, HTML and bundle manifests matched the three original demo archives byte for byte. Its snapshot results matched the original full ledgers. The offline suite passed 302 Python tests and 56 Rust tests. Three live provider tests were skipped. The [event replay reference](../docs/reference/execution.md#older-event-archives) describes two older archive formats checked separately.

## Close callback verification

The SDK binds `strategy.on_close` once per run. The tests check daily and weekly decisions, delivery of intervening fills, propagation of a strategy error and a fresh account on the next run. Dated weights still enter through `targets=`. The [README](../README.md#write-a-strategy) shows a daily MACD strategy and weekly cross-sectional stock selection. The [blocked-sale example](examples/blocked_rotation.py) demonstrates a strategy that waits for the account to confirm a sale.

The [weekly all-A example](../docs/examples/all-a-momentum.md) ran on 30 real sessions. Both sets of ten target weights matched an independent calculation from the raw inputs. The saved request reproduced all 49 fills and the full closing ledger. Existing 98-session demo runs and the fifteen-year ETF and stock benchmarks reproduced their targets and ledgers after migrating the strategy hook.
