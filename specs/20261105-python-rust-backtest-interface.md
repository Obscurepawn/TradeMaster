# Python and Rust Native Backtest Interface

**Status:** Completed | **Date:** 2026-11-05 | **Scope:** In-process PyO3 extension, account-aware `on_close` callbacks, and unified `tm-replay` (`trademaster.backtest`, `tm-python`, `tm-engine`, `tm-runner`) | **Updates:** [Code Architecture Refactoring: Daily Snapshot SDK and Rust Portfolio Sizing](20261104-code-architecture-refactor.md)

## Motivation and Context

In the initial `2026-11-04` snapshot runner and earlier M6 event bundles, Python collected every strategy decision across the sample before launching a separate Rust subprocess over JSON stdin/stdout. Consequently, a scheduled Python strategy could not inspect whether prior orders had actually filled, been blocked at a price limit, or left cash uninvested when choosing its next target.

This specification replaces the subprocess boundary with an in-process PyO3 native extension (`tm-python`, exposed as `trademaster._native`) compiled into the `trademaster` wheel via `maturin`. Rust drives the trading calendar session by session and invokes Python's `on_close(day)` callback at each scheduled, valued close with read-only access to the live account and intervening fills ([Current SDK](../python/trademaster/backtest/__init__.py) · [Current daily engine](../crates/tm-engine/src/snapshot/mod.rs)).

## Account-Aware Strategy API

Strategies run via `backtest(snapshot, strategy=..., schedule=..., initial_account=..., policy=..., detail=...)` and implement `on_close(day: BacktestDay) -> dict[str, Decimal] | None`. Calling `snapshot.day(session)` outside a backtest continues to return the pure market `DecisionDay` view, whereas scheduled callbacks during a run receive `BacktestDay`:

| `BacktestDay` Attribute | Observable Data at `decision_at` |
|---|---|
| `day.session`, `day.closes(...)`, `day.scores(...)` | Current trading date, historical prices, and point-in-time features published at or before the session's `decision_at` cutoff. |
| `day.account` | Closing cash and total equity as `Decimal` in account currency; `quantity(symbol)` and `sellable_quantity(symbol)` return integer share holdings at this close (`0` when unheld). |
| `day.fills` | Tuple of fills executed since the previous callback (or since run start on the first callback) in execution order, recording session, instrument, side, quantity, price, fees, and target batch ID. |

Both `day.account` and `day.fills` are populated even when `detail=False` (which controls only whether full order/position histories are retained in the returned `BacktestResult`). Returning a symbol-to-weight mapping replaces the active target portfolio, `{}` targets all cash, and `None` leaves any active target running under its remaining retry attempts.

### Example: Waiting for a Blocked Sale to Clear

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

Executed on the [four-session fixture](examples/blocked_rotation.py) (`--strategy after-sale`), `SwitchAfterSale` requests `{}` at Friday's close. When `A` is blocked at Monday's lower limit, Monday's close callback still observes `8,000` shares of `A` and returns `None`. After Tuesday's opening sale clears, Tuesday's close callback observes `0` shares of `A` and `CNY 91,995` cash, emitting `{"B": Decimal("0.80")}`, which Rust fills at Wednesday's open (`3,600` shares of `B`, `CNY 91,990` final equity). Persisting both the Friday `{}` batch and Tuesday `80% B` batch distinguishes this stateful execution trace from an unconditional Friday rotation.

## Native Call Flow and Data Ownership

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

- **Clear ownership across FFI:** Python owns strategy state, point-in-time feature Parquet views, and public `Decimal`/dataclass result wrappers; Rust (`tm-engine` via [PyO3](https://pyo3.rs/main/) and [maturin](https://www.maturin.rs/project_layout.html)) owns `PreparedMarket`, `SnapshotSession`, order sizing, cash reservation, opening execution, fees, and `LotLedger`.
- **Zero-copy index reuse & exact wire values:** Opening a **data snapshot** builds and caches `PreparedMarket` indexes once inside the extension (since [Compact Market Rows and Streaming Snapshot Loading](20261126-snapshot-compact-market-rows.md), `Market.open(path, sha256)` streams and verifies the market file in Rust and the prepared market is a compact columnar store); repeated `backtest()` calls reuse that prepared market with isolated account and target state. Weights and monetary amounts cross the native boundary as checked scaled integers, while saved replay bundles retain canonical JSON serialization.
- **Precomputed targets, error propagation, and unified `tm-replay`:** Passing `targets=` executes the entire run in one native call with zero Python callbacks while honoring explicit batch eligibility dates (final-close callback decisions record eligibility on the first session after the sample). Missing valuation quotes return `insufficient_data`, while Python exceptions inside `on_close` abort the Rust loop immediately and propagate the original Python traceback. Both event and snapshot JSON requests replay in process through `tm-replay REQUEST.json`, retiring the standalone `tm-runner` subprocess binary and `--runner` CLI flags.

## Migration Steps and Verification Record

| Migration Step | Architectural Change | Verification Evidence |
|---|---|---|
| 1. Stateful Rust `SnapshotSession` | Extracted `PreparedMarket` and step-by-step `SnapshotSession` operations (advance trading day, register dated target) underneath batch execution. | Exact ledger agreement on the four-session fixture and all three 98-session Tushare snapshot baselines. |
| 2. Mixed Python/Rust wheel (`tm-python`) | Built `trademaster._native` via `maturin`, routed `backtest()` callbacks and `targets=` tables in process, and removed `runner=` subprocess arguments. | Verified callback counts, `SwitchAfterSale` two-batch trace, Python traceback propagation, and run isolation in [Python acceptance tests](../tests/test_snapshot_backtest.py) and [Rust ledger tests](../crates/tm-engine/tests/snapshot_backtest.rs). |
| 3. In-process event runs & `tm-replay` | Exposed `tm-runner` request execution through the native extension and added the `tm-replay` console script dispatching by schema ID. | Byte-for-byte reproduction of archived event results, HTML reports, bundle manifests, and older formats ([event replay reference](../docs/reference/execution.md#older-event-archives)). |
| 4. Subprocess interface retirement | Removed stdin/stdout runner binaries, `--runner` flags, and subprocess launches across SDK, demos, and comparison harnesses. | Confirmed zero subprocess engine invocations across the installed wheel, [timing benchmarks](../docs/examples/rebalance-comparison.md#python-sdk-before-and-after-the-native-extension), and CLI entry points. |

### End-to-End Release Verification

- **Clean wheel & real-data benchmarks:** Installing the Linux wheel in an isolated virtual environment executed both modes of the [blocked rotation example](examples/blocked_rotation.py), the [README](../README.md#write-a-strategy) daily MACD and weekly cross-sectional strategies, all three 98-session Tushare demos (reproducing archived event HTML bytes and full snapshot ledgers), the 30-session [weekly all-A-share example](../docs/examples/all-a-momentum.md) (matching independent raw-data weight calculations across all 49 fills), and both fifteen-year stock and ETF benchmarks.
- **Suite totals:** Passed **302 offline Python tests** (3 live Tushare network tests skipped) and **56 Rust workspace tests**.
