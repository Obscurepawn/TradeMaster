---
name: trademaster-backtest
description: Implement and test TradeMaster A-share, ETF or US-equity strategies, import historical data, replay a backtest, or inspect its HTML report.
---

# TradeMaster backtesting

Work from the TradeMaster repository root. Read the nearest `AGENTS.md` for files you change and consult the [spec index](../../specs/INDEX.md) for changes to public behavior. Install the SDK, its Rust extension and test dependencies with `uv sync --locked --extra dev`. The [repository map](../../docs/reference/repository.md) identifies each package and its tests.

For a US-stock strategy, follow the [US-stock walkthrough](../../README.md#backtest-a-us-stock-strategy) to run the synthetic example or an existing data snapshot. For historical observations, read [US-stock data preparation](references/data-sources.md#prepare-a-us-stock-data-snapshot). Use the [trading-rule contract](../../docs/reference/snapshot-backtest.md#configurable-trading-rules) to select execution settings and check the model's limits.

## Implement a strategy

Put reusable Python signal logic in `python/trademaster/strategies/`. A daily snapshot strategy exposes `on_close(day)`. Rust calls it after valuing the account at each scheduled close. This example targets 80% in a stock when its adjusted close is higher than five sessions ago.

```python
from decimal import Decimal

from trademaster.backtest import BacktestDay, Snapshot, backtest


class FiveSessionMomentum:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def on_close(self, day: BacktestDay) -> dict[str, Decimal] | None:
        prices = day.closes(self.symbol, adjusted=True, limit=6)
        if len(prices) < 6 or prices[0] is None or prices[-1] is None:
            return None
        return {self.symbol: Decimal("0.80")} if prices[-1] > prices[0] else {}


market = Snapshot.open("artifacts/demo-snapshot/snapshot")
result = backtest(market, FiveSessionMomentum("600036.SH"), schedule="daily_close",
                  initial_cash=100_000, detail=True)
result.save("artifacts/five-session-momentum")
```

Replay this strategy with `uv run tm-replay artifacts/five-session-momentum/request.json`. Use `detail=True` to inspect its orders and fills. For repeated trials, reuse the open snapshot and use the default summary mode when you only need daily equity and totals.

- Supply the **whole target portfolio**. Give each weight as a nonnegative value with at most eight decimal places. The weights sum to at most one and leave the remainder in cash. Omitted holdings have target weight zero. `{}` targets cash. `None` keeps the current instruction, including any remaining attempts to fill it.
- For cross-sectional selection use `schedule="week_end"`, rank `day.instruments` as listed on that date, and break ties by instrument ID. `day.scores(name)` reads features published by the decision cutoff. Bound price history with `limit` and handle missing observations and indicator warm-up. [MACD and full-A examples](../../README.md#write-a-strategy)
- Read actual holdings from `day.account.quantity(symbol)` and recent fills from `day.fills`. Rust sizes orders from prior marks and available cash, then checks trading permissions at the next eligible open. Rust owns lot rounding, configured share availability, fees and the ledger. A blocked sale can leave a stock in the account across several callbacks. Sale proceeds fund new purchases from the next open. [Account-aware strategy](../../specs/examples/blocked_rotation.py) · [Execution rules](../../docs/reference/snapshot-backtest.md#configurable-trading-rules)

For equal weights, divide the portfolio in units of 0.00000001. A raw `1 / 3` has more precision than the SDK accepts. This example rounds each share down, then gives one extra unit to each of the first symbols in sorted order until the remainder is used. Three symbols receive `0.33333334`, `0.33333333` and `0.33333333`, totaling exactly one.

```python
from decimal import Decimal


def equal_weights(selected: list[str]) -> dict[str, Decimal]:
    symbols = sorted(set(selected))
    if not symbols:
        return {}
    unit = Decimal("0.00000001")
    units, remainder = divmod(100_000_000, len(symbols))
    weights = dict.fromkeys(symbols, units * unit)
    for symbol in symbols[:remainder]:
        weights[symbol] += unit
    return weights
```

Test a signal on a small price sequence that crosses its threshold. Then run it through `backtest` and check the decision date, first eligible fill and closing account. Use a fresh instance for each run of a stateful strategy. The following example waits until A has sold before targeting B. It creates its own data.

```bash
uv run python specs/examples/blocked_rotation.py --strategy after-sale
uv run tm-replay artifacts/after-sale/request.json
```

The example buys 3,600 shares of B after the sale of A and ends with CNY 19,990 cash. Replay recalculates the ledger and prints JSON that you can compare with the saved `result.json`. Keep the request and snapshot in the same relative locations. Use `result.equity` for amounts in the data snapshot's currency and `result.raw["first_issue"]` to locate an incomplete run. [Snapshot API and units](../../docs/reference/snapshot-backtest.md) · [SDK tests](../../tests/test_snapshot_backtest.py)

## Connect historical data

To create `artifacts/demo-snapshot/snapshot` for the strategy above, follow the five-stock walkthrough. For a vendor CSV, Parquet file or provider for point-in-time queries, follow the data source reference. Keep acquisition outside `on_close` so several strategies can reuse the same inputs. [Five-stock walkthrough](../../docs/examples/tushare-strategies.md#run-the-examples) · [Data sources](references/data-sources.md)

For IndexOne prices or company financials, use the [IndexOne source](../../docs/reference/indexone.md)
and retain its `ResearchObjectEvidence` when preparing a data snapshot. The guide
defines credentials, cache reuse, supported queries and publication-time limits.

## View an HTML backtest report

For an existing **event** run, open its `report.html` in a browser. The charts work offline. Check the run dates, benchmark and return, then use the drawdown and trading-cost charts to explain the result. For the five-stock TuShare examples, the weekly momentum report is `artifacts/demo-strategies/cross-sectional-momentum/report.html`. This command verifies the report and its ledger against the saved bundle.

```bash
uv run python - <<'PY'
from pathlib import Path
from trademaster.e2e import verify_e2e_bundle

run = Path("artifacts/demo-strategies/cross-sectional-momentum")
verify_e2e_bundle(run / "bundle-manifest.json")
print((run / "report.html").resolve().as_uri())
PY
```

To generate these reports, run the event command with your cache directory and plan ID. Share the HTML path for the requested strategy. [Five-stock setup and event command](../../docs/examples/tushare-strategies.md#run-the-examples)

A **snapshot** strategy’s `result.save(...)` writes `request.json` and `result.json`. For that run, show its equity, fills and rejection reasons from its own ledger. The event runner sizes orders at the open and produces a separate performance record. [Report example](../../README.md#backtest-reports) · [Same-data comparison](../../docs/examples/rebalance-comparison.md)
