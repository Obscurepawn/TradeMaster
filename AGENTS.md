# TradeMaster Agent Guide

For strategy implementation, historical data imports, TuShare setup or backtest reports, use the repository’s [TradeMaster backtesting skill](.skills/trademaster-backtest/SKILL.md).

## Current system

TradeMaster supports reproducible research and backtesting for A-shares and ETFs. Python
collects Tushare data, runs point-in-time queries, computes factors and signals,
and produces reports. The IndexOne research source also downloads prices,
company financials, securities and FX into immutable Parquet; its observations
need explicit trading evidence before use in a daily data snapshot.
In a daily snapshot backtest, the Rust extension calls Python at scheduled
closes with actual account values and recent fills. Python returns complete
portfolio weights. Rust plans orders from prior closes, checks the next open,
and maintains cash, holdings and dividend receivables. Saved event requests use a separate event loop
and the same lot ledger.

Resumable research jobs build a panel with one row per instrument and trading
session across the A-share universe. Python computes managed factors in
dependency order, stores the values in Parquet, and evaluates them. Strategies
use these values to select instruments and produce signals in Arrow format.
The event runner executes these signals. Its v3 run bundle preserves the results
and their input references so the report can be reproduced.

## Rules for data and backtests

- Store provider and research data in Parquet. DuckDB tracks coverage and
  supports as-of queries. The daily backtest SDK publishes a market JSON
  file and feature Parquet. Bind input files and hashes to the snapshot
  manifest, and publish a new manifest when an input changes. Surface missing
  data, schema mismatches and provider permission failures at the affected
  step. The daily engine reports `insufficient_data` when required market
  evidence is missing.
- A signal that uses a closing price can execute no earlier than the next
  eligible market event.
- Keep order intents, accepted orders, fills, rejections, expiries, ledger
  postings, and snapshots as distinct record types.
- Factor scores may use floating point. Use exact amounts for the money,
  prices, rates and quantities posted to the ledger.
- For cash dividends on raw prices, count units at the record close, include
  the receivable in equity on the ex-date and make payment available after
  the pay-date open. Keep the signal instrument and traded instrument explicit.
- Each factor ID and version identifies its code, parameters, and dependencies.
  When publishing factor values, record the source data snapshots, inputs,
  files for any upstream factors, and output hashes.
- Each report covering all A-shares records its market and factor input
  manifests and evaluation settings. Compounding overlapping forward returns
  counts some sessions twice. Use quantile returns to evaluate factors. Measure
  strategy returns under the trading rules from the Rust engine's account values.
- For each public factor candidate, record the source release, licensing
  restrictions, dependencies, whether it can be computed, and its review
  status. Only candidates linked to a managed definition can produce values.
- For target-weight portfolios, process sells before buys and use a stable
  instrument order. Changing signal IDs must not change the allocation.
- Read secrets from environment variables and keep them out of saved output.
- Use `research/` and `docs/research/` as read-only references.

## Documentation

- Write the project README, all `AGENTS.md` guides, and all specifications in
  English. Use English for diagram labels, legends, and the explanatory text,
  comments, and sample output in new specification demos.
- Use everyday English to describe what happens and how it affects the
  result. Say which price a strategy can see and when an order can first
  trade. State a limitation once, where it affects a run or its interpretation.
- Call the input data for a backtest a **data snapshot**. Give its as-of time
  when explaining what the strategy could observe.
- Describe setting the order quantity and reserving cash as separate steps.
  State when the quantity becomes final and how much cash is set aside.
- Name refactors, diagrams and examples after the behavior they describe.
  Use version numbers when identifying an actual release or file format.
- Use runnable examples for README output. Keep diagrams in sync with the
  implementation they illustrate and label proposed APIs.
- For performance changes, measure the same representative workload before
  and after the change. Record the inputs, environment, metrics, and both
  results so another contributor can reproduce the comparison.

## Development workflow

Before editing, read `specs/INDEX.md`, the active specification, and any
specification covering the change. For changes in behavior, start with a
failing test, make it pass, then refactor.
Record the failing and passing results in the spec.

Use the checks and review scope in the specification for the change. The
completed M2–M6 specifications record the reviews for those milestones.
Preserve unrelated working-tree changes.

<!-- develop-with-specs:start -->
## Maintaining specifications

At each significant change and before handoff, update the relevant file under
`specs/`. Record the task status, decisions, current state, changed files,
verification results, and next step so work can continue without the chat.
<!-- develop-with-specs:end -->
