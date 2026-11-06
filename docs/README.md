# TradeMaster documentation

Start with the question you need to answer. A factor study compares today's signal with later prices. A backtest places orders against market data and measures the account after fees, rejected trades and settlement. The two can give different answers for the same selection rule.

| I want to… | Start here |
|---|---|
| See cash, fills and a final balance | Run the [four-day rotation](../README.md#run-the-example) and follow the blocked sale, the next buy and the daily balance. For a saved event request, use the [three-session round trip](reference/execution.md#replay-the-three-session-example). |
| Find performance bottlenecks | The [core performance review](reviews/20261105-backtest-performance.md) measures snapshot loading, scheduled strategies, Rust settlement and allocation in broad portfolios. |
| Measure a long backtest | The [fifteen-year ETF benchmark](examples/broad-etf.md) compares a daily index signal with buying and holding the same ETF and reports time per full run. |
| Select stocks across the A-share market | The [weekly stock-selection example](examples/all-a-momentum.md) ranks historical Shanghai, Shenzhen and Beijing listings and follows the resulting orders. |
| Compare strategies on the same market | The [TuShare example](examples/tushare-strategies.md) runs weekly momentum, a moving-average crossover and MACD on five stocks. It lists the inputs, commands and measured outcomes. |
| Backtest a portfolio of target weights | The [daily snapshot API](reference/snapshot-backtest.md) explains decision cutoffs, partial investment, cash reserves and retries. |
| Download prices or company financials from IndexOne | The [IndexOne guide](reference/indexone.md) shows authenticated queries, immutable Parquet evidence and offline reuse. |
| Understand a difference between engines | The [same-market comparison](examples/rebalance-comparison.md) lines up dated targets, trades and daily account values. |
| Diagnose an order or a report | The [execution reference](reference/execution.md) follows the decision, order, settlement and account records across Python and Rust. |
| Build an A-share factor study | Follow the [research workflow](full-a-factor-research.md), then consult the [quality review](reviews/20260823-m9-full-a-quality-review.md) for gaps in the published historical run. |
| Define a factor or inspect a candidate | Read the [managed factor guide](factor-management.md) for calculation and storage, or the [public library guide](public-factor-library.md) for source and review status. |
| Reproduce industry rotation | The [strategy guide](../python/trademaster/strategies/industry_fundamental/README.md) explains selection and the [historical implementation notes](../python/trademaster/strategies/industry_fundamental_top5/README.md) explain the price and accounting assumptions. |
| Change an implementation | Use the [repository map](reference/repository.md) and the [command reference](reference/commands.md). The [architecture overview](architecture/overview.md) traces one end-to-end run. |

## Programmer's reference

| Area | What it owns |
|---|---|
| [Data and research](reference/data-and-research.md) | Coverage, point-in-time queries, immutable source objects, acquisition plans, panel construction, labels and research reports. |
| [Factors and strategies](reference/factors-and-strategies.md) | Calculation, managed dependencies and reuse, ranked selections and the Python strategy packages. |
| [Execution and results](reference/execution.md) | Runnable entry points, Rust types and trading rules, the native extension and saved requests, account metrics and artifact verification. |
| [Commands](reference/commands.md) | Invocation, required inputs and outputs for the installed commands, module commands and saved-request replay. |
| [Repository map](reference/repository.md) | Build and package files, test locations, diagrams, specifications and design examples. |

This manual separates walkthroughs, command usage and programmer references using the organization of the [LLVM documentation](https://llvm.org/docs/), [LLVM Command Guide](https://llvm.org/docs/CommandGuide/) and [LLVM Programmer's Manual](https://llvm.org/docs/ProgrammersManual.html).
