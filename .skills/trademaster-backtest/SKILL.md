---
name: trademaster-backtest
description: Implement and test TradeMaster A-share, ETF, or US-equity strategies, import historical data, replay a backtest, or inspect its HTML report.
---

# TradeMaster Backtesting

Work from the TradeMaster repository root. Read the nearest `AGENTS.md` for any directory you touch, and consult the [spec index](../../specs/INDEX.md) before changing public behavior. Install the Python SDK, its compiled Rust extension, and development tools with `uv sync --locked --extra dev`. The [repository map](../../docs/reference/repository.md) links each package and crate to its test suites.

For US-equity backtests, follow the [US-stock walkthrough](../../README.md#backtest-a-us-stock-strategy) to run the synthetic example or reopen an existing data snapshot. When preparing historical US market data, read [US-stock data preparation](references/data-sources.md#prepare-a-us-stock-data-snapshot) and consult the [trading-rule contract](../../docs/reference/snapshot-backtest.md#configurable-trading-rules) to configure execution policies within the engine's supported scope.

## Implement a strategy

Place reusable Python strategy classes in `python/trademaster/strategies/`. A daily snapshot strategy implements `on_close(day)`, which the Rust engine invokes after valuing the account at each scheduled close. The example below allocates 80% to a single stock whenever its adjusted close exceeds its level five sessions earlier, holding cash otherwise:

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

Replay the saved request with `uv run tm-replay artifacts/five-session-momentum/request.json`. Pass `detail=True` when you need order- and fill-level audit records; for parameter sweeps or repeated trials, reuse the open `Snapshot` instance and keep the default summary mode (`detail=False`) to record daily equity and aggregate totals cleanly.

- **Return the complete target portfolio**: Express each target weight as a nonnegative `Decimal` with at most eight decimal places whose sum does not exceed `1` (leaving any remainder in cash). Omitted holdings receive a target weight of zero, `{}` liquidates to 100% cash, and `None` keeps the active target batch and its remaining retry attempts intact.
- **Rank deterministically across the point-in-time universe**: For cross-sectional selection (for example, `schedule="week_end"`), rank `day.instruments` active on that session and break ties by instrument ID. Read point-in-time features published by the decision cutoff via `day.scores(name)`, bound trailing price lookups with `limit`, and guard against missing observations or indicator warm-up windows. [MACD and full-A examples](../../README.md#write-a-strategy)
- **Use closing account state for execution-aware transitions**: Inspect settled and total share balances via `day.account.quantity(symbol)` / `day.account.sellable_quantity(symbol)` and intervening executions via `day.fills`. Rust fixes whole-lot order quantities from prior closing marks and reserves cash separately before checking trading permissions at the next eligible open, while maintaining lot rounding, settlement locks (`t0`/`t1`), fees, and the FIFO ledger. Because a suspended or limit-locked sale can leave an instrument in the account across multiple callbacks—and sale proceeds fund purchases starting from the open on which the sale fills—stateful rotation strategies can verify liquidation directly from `day.account`. [Account-aware strategy](../../specs/examples/blocked_rotation.py) · [Execution rules](../../docs/reference/snapshot-backtest.md#configurable-trading-rules)

When constructing equal-weight portfolios, quantize weights in units of `0.00000001` (`1e-8`) rather than passing unrounded binary floats such as `1 / 3`. Floor each symbol's share and distribute residual `1e-8` units across the earliest sorted instrument IDs so weights sum to `1.00000000` (for three symbols: `0.33333334`, `0.33333333`, and `0.33333333`):

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

Verify new signal logic first on a minimal synthetic price sequence that crosses its decision threshold, then execute it through `backtest` to assert the decision session, first eligible fill, and closing ledger balances. Instantiate a fresh strategy object per run whenever the strategy holds internal state. The self-contained example below waits until position `A` has actually cleared before targeting `B`:

```bash
uv run python specs/examples/blocked_rotation.py --strategy after-sale
uv run tm-replay artifacts/after-sale/request.json
```

The run purchases 3,600 shares of `B` once `A` sells and finishes with CNY 19,990 in cash. `tm-replay` recomputes the Rust ledger from `request.json` (resolving the snapshot path relative to the request directory) and emits canonical JSON matching `result.json`. Inspect `result.equity` for currency-denominated closing equity and `result.raw["first_issue"]` whenever a run halts with `insufficient_data`. [Snapshot API and units](../../docs/reference/snapshot-backtest.md) · [SDK tests](../../tests/test_snapshot_backtest.py)

## Connect historical data

To generate `artifacts/demo-snapshot/snapshot` for the five-stock Tushare walkthrough, or to ingest vendor CSV/Parquet extracts and point-in-time provider feeds, follow the linked setup guides. Keep data acquisition and snapshot publication outside `on_close` so multiple strategies share one immutable, hash-verified data snapshot. [Five-stock walkthrough](../../docs/examples/tushare-strategies.md#run-the-examples) · [Data sources](references/data-sources.md)

When acquiring IndexOne prices, company financials, security reference records, or FX rates, follow the [IndexOne source](../../docs/reference/indexone.md) guide and retain its `ResearchObjectEvidence` during snapshot publication. That reference covers credential setup (`INDEXONE_API_KEY`), offline cache verification, supported read-only operations, and publication-time semantics.

## View an HTML backtest report

For a saved **event** run bundle, open its self-contained `report.html` directly in a browser (Plotly JS is embedded for offline viewing). Review the run window, benchmark alignment, net return, drawdown profile, and transaction-cost breakdown. For the five-stock Tushare demos, verify the weekly momentum bundle (`artifacts/demo-strategies/cross-sectional-momentum/report.html`) and print its browser URI with:

```bash
uv run python - <<'PY'
from pathlib import Path
from trademaster.e2e import verify_e2e_bundle

run = Path("artifacts/demo-strategies/cross-sectional-momentum")
verify_e2e_bundle(run / "bundle-manifest.json")
print((run / "report.html").resolve().as_uri())
PY
```

To regenerate the Tushare demo bundles from a local research cache and plan SHA-256, run the event demo command and share the resulting `report.html` path. [Five-stock setup and event command](../../docs/examples/tushare-strategies.md#run-the-examples)

For a **daily snapshot** run, `result.save(...)` writes `request.json` and `result.json`; summarize its equity trajectory, fills, costs, and any blocked/rejected attempts directly from that snapshot ledger rather than mixing in event-runner outputs (which size orders at the session open under a separate execution loop). [Report example](../../README.md#backtest-reports) · [Same-data comparison](../../docs/examples/rebalance-comparison.md)
