# Execution and results

Use the [daily snapshot API](snapshot-backtest.md) for portfolio targets that stay active over several opens. This page covers the event-runner request, shared accounting contracts and reports.

## Event runner

The JSON runner receives instrument specifications, a fee schedule, timestamped market events and scheduled signals. At an eligible open the scheduled strategy resolves its targets, the validator checks each order, the execution model fills or expires accepted orders, and the ledger applies the outcome. Settlement changes which lots can be sold. The close supplies prices for valuation. An order that was submitted is therefore a different record from a fill. [Runnable request](../examples/round-trip.json) · [Event architecture](../architecture/overview.md)

| Boundary | Implementation and entry points |
|---|---|
| Python request and bundle | [e2e package](../../python/trademaster/e2e/__init__.py) defines `RunnerRequest`, `RunnerSignal`, `RunnerEvent`, `RunnerBar` and `E2ERunnerClient.execute()`. It passes canonical request bytes to the native extension and writes the request, result, report inputs and report. `verify_e2e_bundle()` checks saved objects, replays execution and rebuilds the report. [Orchestrator tests](../../tests/test_e2e_orchestrator.py) · [Bundle rules](../../python/trademaster/e2e/AGENTS.md) |
| Fixed-point and accounting records | [tm-core library](../../crates/tm-core/src/lib.rs) defines `Fixed8` for prices and money, integral `Quantity`, `InstrumentSpec`, `MarketBar`, `OrderIntent`, `OrderExecution`, `LedgerEffect` and `AccountSnapshot`. `typed_artifact_record_batch()` materializes typed records and `validate_run_artifact_rows()` checks cross-table behavior. [Crate rules](../../crates/tm-core/AGENTS.md) |
| Rules and state | [tm-engine library](../../crates/tm-engine/src/lib.rs) contains `EventLoop`, `ScheduledSignalStrategy`, `CnAshareOrderValidator`, `DailyBarExecutionModel`, `CnFeeSchedule` and `LotLedger`. `StrategyAdapter` receives a decision view. `OrderValidator` and `ExecutionModel` determine orders and outcomes. `Ledger` owns cash and sellable lots. `TradingCalendar`, `FeeSchedule` and `FxRateProvider` supply separate policies. [Crate rules](../../crates/tm-engine/AGENTS.md) |
| Event and replay interface | [tm-runner](../../crates/tm-runner/src/lib.rs) parses saved settings and serializes event results. [Native replay](../../crates/tm-python/src/replay.rs) exposes these calls to Python. [tm-replay](../../python/trademaster/replay.py) accepts an event or snapshot request file. [Event wire tests](../../crates/tm-runner/tests/wire.rs) · [Snapshot and saved-event replay tests](../../crates/tm-runner/tests/snapshot_replay.rs) · [Crate rules](../../crates/tm-runner/AGENTS.md) |
| Metrics and HTML | [reporting package](../../python/trademaster/reporting/__init__.py) builds `PerformanceSeries`, `StrategyMetrics` and trade summaries from executed results. It handles benchmark alignment and exposure diagnostics separately. `build_html_report()` renders the recorded inputs. [Metrics tests](../../tests/test_reporting_metrics.py) · [HTML tests](../../tests/test_reporting_report.py) · [Reporting rules](../../python/trademaster/reporting/AGENTS.md) |

On the event path, target-weight batches sum to one when invested or zero for an exit. The open contributes to sizing.

To trace a rejected order in the saved JSON, read its `code` and `message` in `rejections`. Match its `order_id` to `orders` for the instrument and side. For a price-limit rejection, use its `event_time` to find the `session_open` event in the request, then read that instrument’s `open_scaled`, `up_limit_scaled` and `down_limit_scaled`. Fills also carry `order_id` and `event_time`, alongside the executed quantity, price and costs.

## Replay the three-session example

From the repository root, replay the supplied JSON request. It starts with CNY 2,000, targets 100 shares at the next open and exits the position on the third session.

```bash
uv run tm-replay docs/examples/round-trip.json > result.json
```

![Three closing balances of CNY 2,000, 2,093.99 and 2,087.32.](../examples/round-trip.svg)

On January 3 the account buys at CNY 10 and holds through the close at 11. On January 6 the shares become sellable and the account exits at 11. The CNY 100 price gain becomes **CNY 87.32 after costs**. To inspect the fills and the final balance with Python, run the following from the same directory. The wire format stores prices and money as integers scaled by 100,000,000.

```bash
python3 - <<'PY'
import json
from decimal import Decimal
from pathlib import Path

result = json.loads(Path("result.json").read_bytes())
unit = Decimal(100_000_000)
for fill in result["fills"]:
    print(fill["side"], fill["quantity"], f'@ {Decimal(fill["price_scaled"]) / unit:.2f}')
print(f'Ending balance: {Decimal(result["nav"][-1]["net_asset_value_scaled"]) / unit:.2f}')
PY
```

```text
buy 100 @ 10.00
sell 100 @ 11.00
Ending balance: 2087.32
```

For a rejection example, copy the request, set `initial_cash_scaled` to `"100000000"` (CNY 1) and replay the copy. The first rejection identifies `order:buy` at `2025-01-03T01:30:00Z` with code `insufficient_cash`. The corresponding order identifies the attempted purchase as 100 shares of `600000.SH`.

Change a target or fee in the [example request](../examples/round-trip.json) and run it again. The JSON result includes orders, fills, rejections, cash, positions and account value at each step.

## Market samples and strategy runs

| Source | What it adds |
|---|---|
| [demo_data.py](../../python/trademaster/e2e/demo_data.py) | `load_demo_market()` reads a completed acquisition plan and rehashes the selected raw objects. `DemoMarket` carries actual sessions, the following open session, bars, status and source hashes. It converts TuShare volume in lots to whole shares. The sample requires continuous quotes, exact limits and unchanged adjustment factors. [Offline loader regressions](../../tests/test_demo_data.py) |
| [demo_runner.py](../../python/trademaster/e2e/demo_runner.py) | `run_demo_strategy()` translates close-based demo decisions into runner inputs and archives the inputs and output report. [Next-open and archive test](../../tests/test_demo_runner.py) |
| [demo_strategies.py](../../python/trademaster/e2e/demo_strategies.py) | `run_demos()` runs three strategies through the event path and writes their HTML reports. `run_snapshot_demos()` uses one shared snapshot and saves a replayable request and result per strategy. [Workflow and data verification tests](../../tests/test_tushare_demo_strategies.py) |
| [compare_rebalance.py](../../python/trademaster/e2e/compare_rebalance.py) | `compare()` verifies the original runner archive, compares dated targets, joins trades and daily balances and times native replay. [Method and results](../examples/rebalance-comparison.md) · [Comparison tests](../../tests/test_rebalance_comparison.py) |
| [real_tushare.py](../../python/trademaster/e2e/real_tushare.py) | `run_real_tushare_e2e()` exercises cross-sectional stock selection and ETF timing using the data portal, Rust and a repeated read of the cache. `DailyLimitRuleEvidence` records the ETF limit rule where relevant. [Live integration tests](../../tests/test_real_tushare_e2e.py) |

## Schemas and Rust regressions

Python's [contracts.py](../../python/trademaster/contracts.py) supplies the public protocols and `RunManifest`. `run_artifact_schemas()` returns the Arrow layouts, `validate_run_artifacts()` checks related tables and `verify_run_manifest()` verifies saved evidence. The canonical [artifact schema](../../crates/tm-core/schemas/run-artifacts-v1.json) defines the tables and their shared constraints. The [manifest schema](../../crates/tm-core/schemas/run-manifest-v1.json) and [wire fixture](../../crates/tm-core/schemas/run-manifest-v1.fixture.json) establish the serialized representation for both languages. [Schema overview](../../schemas/README.md) · [Schema ownership rules](../../schemas/AGENTS.md) · [Canonical-file rules](../../crates/tm-core/schemas/AGENTS.md)

| Change | Start with these checks |
|---|---|
| Shared types or schemas | [Python/Rust schema agreement](../../tests/test_schema_golden.py), [public Python interfaces](../../tests/test_public_interfaces.py), [Rust public constructors](../../crates/tm-core/tests/public_contract.rs), [exact values and account invariants](../../crates/tm-core/tests/contract_behavior.rs), [manifest fixture](../../crates/tm-core/tests/shared_schema.rs), [Arrow conversion](../../crates/tm-core/tests/artifact_arrow.rs) and [typed record materialization](../../crates/tm-core/tests/typed_artifacts.rs). |
| Execution inputs, limits or costs | [A-share validation and fees](../../crates/tm-engine/tests/a_share_rules.rs), [open fills and stale bars](../../crates/tm-engine/tests/daily_execution.rs) and [quantity conservation and ordering](../../crates/tm-engine/tests/strict_execution.rs). |
| Ownership and ordering | [T+1 and FIFO lots](../../crates/tm-engine/tests/lot_ledger.rs), [scheduled targets](../../crates/tm-engine/tests/scheduled_strategy.rs) and [settlement before the next open](../../crates/tm-engine/tests/unified_event_loop.rs). |
| Extension interfaces | [Object safety](../../crates/tm-engine/tests/public_traits.rs) and [implementing runtime traits from another crate](../../crates/tm-engine/tests/implementable_contracts.rs). |


## Older event archives

In a new event bundle, `report-input.json` records `monthly_return_basis` as `previous_month_close`. Older bundles omit that field and measure each month from its first observed equity. Bundle verification rebuilds the HTML using the convention saved with that run. To view the month-end calculation for a historical example, rerun it into a fresh output directory. [Five-stock report commands](../examples/tushare-strategies.md#run-the-examples)

The three TuShare demo bundles described in the [comparison](../examples/rebalance-comparison.md) reproduce their saved result bytes and full reports. Two earlier archives also illustrate what the request and report versions identify. For the January 2025 cross-sectional sample, both the pre-migration executable and the native extension reproduce the original trades and equity. The original archive sorted its signal rows by signal ID, which gives its result file a different hash. The early ETF request reproduces its ledger bytes. Its report input uses an earlier set of fields, so verification stops at the report. The original reports and requests remain available for inspection.
