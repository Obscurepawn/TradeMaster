# TradeMaster documentation

TradeMaster separates cross-sectional signal research from execution-aware portfolio simulation because the two answer fundamentally different questions: a factor study tests whether a point-in-time observation predicts subsequent forward returns, whereas a backtest routes orders through exchange trading rules, transaction costs, limit rejections, and settlement lag to measure realized account equity. Even under identical stock-selection logic, execution frictions and uninvested cash can cause realized portfolio performance to diverge from frictionless quantile spreads. Use the task guide below to jump directly to runnable walkthroughs, empirical benchmarks, or subsystem contracts.

| I want to… | Start here |
|---|---|
| See cash, fills and a final balance | Run the [four-day rotation](../README.md#run-the-example) to trace a limit-blocked sale, its subsequent buy, and daily equity, or replay a saved event request with the [three-session round trip](reference/execution.md#replay-the-three-session-example). |
| Find performance bottlenecks | Read the [core performance review](reviews/20261105-backtest-performance.md), which profiles data snapshot loading, scheduled Python callbacks, Rust settlement, and order allocation across broad portfolios. |
| Measure a long backtest | Run the [fifteen-year ETF benchmark](examples/broad-etf.md), which compares a daily index-timing signal against buying and holding the underlying ETF while reporting end-to-end wall-clock runtime. |
| Select stocks across the A-share market | Follow the [weekly stock-selection example](examples/all-a-momentum.md) as it ranks historical Shanghai, Shenzhen, and Beijing listings and tracks the resulting orders. |
| Compare strategies on the same market | Run the [Tushare strategy example](examples/tushare-strategies.md) to evaluate weekly momentum, a moving-average crossover, and MACD across five stocks with full inputs, commands, and measured outcomes. |
| Backtest a portfolio of target weights | Consult the [daily snapshot API](reference/snapshot-backtest.md) for decision cutoffs, pre-open sizing, cash reservation, partial fills, and multi-session retries. |
| Download prices or company financials from IndexOne | Follow the [IndexOne guide](reference/indexone.md) for authenticated queries, content-addressed Parquet evidence, and offline cache reuse. |
| Understand a difference between engines | Read the [same-market comparison](examples/rebalance-comparison.md), which aligns dated target weights, executed trades, and daily account balances across both execution engines. |
| Diagnose an order or a report | Use the [execution reference](reference/execution.md) to trace decisions, order intents, fills, rejections, and FIFO lot-ledger postings across Python and Rust. |
| Build an A-share factor study | Walk through the [research workflow](full-a-factor-research.md), then review the [quality review](reviews/20260823-m9-full-a-quality-review.md) for coverage caveats in the published twenty-year study. |
| Define a factor or inspect a candidate | Read the [managed factor guide](factor-management.md) for versioned computation and lineage-tracked storage, or consult the [public library guide](public-factor-library.md) for catalog attribution and implementation status. |
| Reproduce industry rotation | Read the [strategy guide](../python/trademaster/strategies/industry_fundamental/README.md) for portfolio construction rules and the [historical implementation notes](../python/trademaster/strategies/industry_fundamental_top5/README.md) for legacy pricing and accounting assumptions. |
| Change an implementation | Start with the [repository map](reference/repository.md) and [command reference](reference/commands.md), or read the [architecture overview](architecture/overview.md) to trace an end-to-end execution lifecycle. |

## Programmer's reference

| Area | What it owns |
|---|---|
| [Data and research](reference/data-and-research.md) | Catalog coverage, point-in-time (PIT) queries, content-addressed Parquet objects, resumable acquisition plans, dense panel construction, forward-return labels, and research reports. |
| [Factors and strategies](reference/factors-and-strategies.md) | Factor computation, DAG dependency resolution and materialization caching, cross-sectional signal generation, and first-party Python strategy packages. |
| [Execution and results](reference/execution.md) | Event-runner entry points, fixed-point Rust domain types, A-share and configurable trading rules, shared FIFO lot-ledger accounting, performance metrics, and bundle verification. |
| [Commands](reference/commands.md) | CLI flags, required inputs, and artifact outputs for installed console scripts, module entry points, and deterministic request replay. |
| [Repository map](reference/repository.md) | Python and Rust workspace boundaries, test suites, architecture diagrams, milestone specifications, and runnable design fixtures. |

This manual separates tutorials, CLI references, and subsystem contracts following the structure of the [LLVM documentation](https://llvm.org/docs/), [LLVM Command Guide](https://llvm.org/docs/CommandGuide/), and [LLVM Programmer's Manual](https://llvm.org/docs/ProgrammersManual.html).
