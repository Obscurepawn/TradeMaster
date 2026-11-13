# M2: Factor Computation and Signal Generation

**Status:** Completed | **Date:** 2026-08-11 | **Scope:** Point-in-time factor definitions, cross-sectional transforms, and T+1 signal generators (`trademaster.factors`, `trademaster.signals`) | **Updated-by:** [M7: Managed Factor Registry, Materialization Store, and Strategy Refactor](20260823-m7-factor-management-strategy-refactor.md)

## Motivation and Scope

Milestone M2 builds point-in-time (PIT) factor computation and signal generation on top of M1 **data snapshots**. It supports two strategy patterns:

1. **Cross-sectional selection:** At each scheduled rebalance, rank the eligible universe by factor score, select the top `N` instruments, and emit equal or explicit portfolio target weights.
2. **Single-instrument timing:** Evaluate one stock or ETF's historical series for threshold crossings and emit target-quantity buy or sell signals.

Order validation, cash allocation, T+1 lot unlocking, suspensions, and daily price limits are delegated to the Rust execution kernel ([M3](20260811-m3-rust-rules-ledger.md)–[M4](20260811-m4-unified-event-runtime.md)).

## Design and Public Interfaces

### Factor Layer (`trademaster.factors`)

- `FactorDefinition` declares a stable `(factor_id, version)` pair, required canonical datasets and fields, session lookback window, and directional semantics.
- `FactorRegistry` indexes definitions by ID and version while rejecting duplicate registrations and unresolved upstream dependencies.
- `FactorExecutor.compute()` consumes an Arrow table bound to an M1 data snapshot whose rows satisfy the snapshot's `as_of` cutoff (`event_time` and `known_at <= as_of`). It emits an Arrow table preserving the same `snapshot_id` with columns `instrument_id, event_time, factor_id, factor_version, value, is_valid`.
- Built-in v1 factors:
  - `MomentumFactor`: computes `N`-session adjusted-price momentum ordered strictly within each instrument's history, marking warm-up rows (`< N` sessions) as `is_valid=False`.
  - `ValueFactor`: derives rankable valuation scores from PIT `daily_basic` (`float_market_value` or `total_market_value`).
  - `CompositeFactor` (`CompositeComponent`): combines multiple factors via configurable winsorization bounds, cross-sectional z-scores, component signs/weights, and explicit missing-value rules.

### Signal Layer (`trademaster.signals`)

- `CrossSectionalSignalConfig` configures `top_n`, sort direction, minimum valid cross-sectional sample size, equal or explicit weights, and next eligible execution timestamp.
- `SingleAssetSignalConfig` configures the target instrument, buy/sell crossing thresholds, target share quantity, and next eligible execution timestamp.
- Signal generators emit rows matching the shared `signals` artifact schema: `signal_id, strategy_id, instrument_id, signal_time, eligible_execution_time, intent_type, value, reason, snapshot_id`.
- **Causality rule:** Any signal computed from session `T` closing prices is eligible no earlier than the next trading session (`T+1`), enforcing `eligible_execution_time > signal_time`.
- Built-in v1 generators:
  - `TopNTargetWeightGenerator`: ranks valid scores when the cross-section meets the minimum sample threshold, breaks ties deterministically by ascending `instrument_id`, and assigns exact `Decimal` equal weights summing to `1.00000000`.
  - `ThresholdQuantitySignalGenerator`: detects upward or downward threshold crossings using only observations available at `signal_time` and emits integer quantity intents.

## Verification and Regression Record

Factor values and portfolio weights were verified against hand-calculated three-instrument reference fixtures before running integration tests over real M1 Parquet/DuckDB data snapshots. In the snapshot integration suite, the test data provider rejected all network calls to verify that both stock top-`N` target weights and ETF threshold signals were computed strictly from local M1 snapshot objects.

| Verification Stage | Scope and Outcome |
|---|---|
| Factor contracts & provenance (`tests/test_factor_interfaces.py`) | Verified `FactorRegistry` duplicate/dependency checks, fixed Arrow output schema, `snapshot_id` propagation, and `known_at` / `event_time` cutoff enforcement. |
| Momentum & cross-sectional weights (`tests/test_momentum_and_signals.py`) | Verified per-instrument lookback isolation, deterministic `instrument_id` tie-breaking, exact `Decimal` weight sums (`1.00000000`), and `eligible_execution_time > signal_time`. |
| Value, composite & threshold signals (`tests/test_value_composite_threshold.py`) | Verified `daily_basic` market-value ranking, winsorized z-score composites, ETF symbol handling, duplicate/future row rejection, and upward/downward threshold crossings (11 focused / 118 total tests passing initially). |
| Independent review & regression fixes | Resolved 7 High findings (0 Critical) in a single pass with 10 new failing-then-passing regression cases covering execution-time bounds, shared artifact enum vocabularies, `Protocol` structural conformance, stale threshold crossings, factor version identity, `known_at` validation, and dependency graph checks. |
| Final milestone checks | 16 focused M2 tests and 122 total Python tests passed (0 skipped, including live Tushare `trade_cal` smoke test); Ruff, strict `mypy` (27 source files), wheel/sdist packaging (`factors`, `signals`, `py.typed`, shared schemas), `git diff --check`, and credential scans (0 matches) all passed. |
