---
name: trademaster-backtest
description: Implement and test a TradeMaster strategy, connect TuShare or another historical data source, replay a backtest, or inspect its HTML report.
---

# TradeMaster backtesting

Work from the TradeMaster repository root. Read the nearest `AGENTS.md` for files you change and consult the [spec index](../../specs/INDEX.md) for changes to public behavior. Install the SDK, its Rust extension and test dependencies with `uv sync --locked --extra dev`. The [repository map](../../docs/reference/repository.md) identifies each package and its tests.

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

- Supply the **whole target portfolio**. Weights are nonnegative, total at most one, and leave the remainder in cash. Omitted holdings have target weight zero. `{}` targets cash. `None` keeps the current instruction, including any remaining attempts to fill it.
- For cross-sectional selection use `schedule="week_end"`, rank `day.instruments` as listed on that date, and break ties by instrument ID. `day.scores(name)` reads features published by the decision cutoff. Bound price history with `limit` and handle missing observations and indicator warm-up. [MACD and full-A examples](../../README.md#write-a-strategy)
- Read actual holdings from `day.account.quantity(symbol)` and recent fills from `day.fills`. Rust sizes orders from prior marks and available cash, then checks trading permissions at the next eligible open. Rust owns lot rounding, T+1, fees and the ledger. A blocked sale can leave a stock in the account across several callbacks. [Account-aware strategy](../../specs/examples/blocked_rotation.py) · [Execution rules](../../README.md#a-share-trading-rules)

Test a signal on a small price sequence that crosses its threshold. Then run it through `backtest` and check the decision date, first eligible fill and closing account. Use a fresh instance for each run of a stateful strategy. The following example waits until A has sold before targeting B. It creates its own data.

```bash
uv run python specs/examples/blocked_rotation.py --strategy after-sale
uv run tm-replay artifacts/after-sale/request.json
```

The example buys 3,600 shares of B after the sale of A and ends with CNY 19,990 cash. Replay recalculates the ledger and prints JSON that you can compare with the saved `result.json`. Keep the request and snapshot in the same relative locations. Use `result.equity` for CNY amounts and `result.raw["first_issue"]` to locate an incomplete run. [Snapshot API and units](../../docs/reference/snapshot-backtest.md) · [SDK tests](../../tests/test_snapshot_backtest.py)

## Connect historical data

For a TuShare download, a vendor CSV or Parquet import, or a reusable provider for point-in-time queries, follow [Data sources](references/data-sources.md). It includes the commands that prepare `artifacts/demo-snapshot/snapshot` for the strategy above. Keep acquisition outside `on_close` so several strategies can run against the same saved inputs.

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

To generate the demo reports from a prepared cache, use the `--engine events` command in [Data sources](references/data-sources.md#prepare-the-tushare-demo). When sharing a report, give the path to the HTML for the requested strategy.

A **snapshot** strategy’s `result.save(...)` writes `request.json` and `result.json`. For that run, show its equity, fills and rejection reasons from its own ledger. The event runner sizes orders at the open and produces a separate performance record. [Report example](../../README.md#backtest-reports) · [Same-data comparison](../../docs/examples/rebalance-comparison.md)
