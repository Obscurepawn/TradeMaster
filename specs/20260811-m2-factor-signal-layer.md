# M2 factor and signal layer

Status: completed
Owner: Codex

## Scope

M2 builds factor registration and computation on M1 point-in-time (PIT) data snapshots. It supports two uses. At each rebalance, a cross-sectional strategy ranks the market, selects the top N instruments, and emits target weights. A stock or ETF strategy uses one instrument's time series to identify buy and sell signals and set its target quantity. The M3 Rust engine handles cash, positions, T+1 lots, suspensions, and price limits.

## Public interfaces

- `FactorDefinition` specifies a stable `factor_id/version`, the datasets and fields it reads, the number of lookback sessions, and the meaning of the result.
- `FactorRegistry` registers definitions and looks them up by ID and version. It rejects duplicate definitions and unknown dependencies.
- `FactorExecutor.compute()` accepts an Arrow table that identifies its data snapshot. Rows must describe events that had occurred and data that was available by the specified cutoffs. The output identifies the same snapshot and contains `instrument_id,event_time,factor_id,factor_version,value,is_valid`.
- `CrossSectionalSignalConfig` specifies top N, sort direction, minimum valid sample, equal or explicit weights, and the next eligible execution time.
- `SingleAssetSignalConfig` specifies the instrument, buy and sell thresholds or rules, target quantity, and next eligible execution time.
- Both generators emit rows with `signal_id,strategy_id,instrument_id,signal_time,eligible_execution_time,intent_type,value,reason,snapshot_id`. A signal based on today's close first becomes eligible at the next trading session (T+1), so `eligible_execution_time > signal_time`.

## Factors and generators

- `MomentumFactor` computes N-session momentum from adjusted prices ordered by instrument and event time. It marks insufficient history invalid and keeps each instrument's window separate.
- `ValueFactor` uses PIT `daily_basic` fields `float_market_value` or `total_market_value` to produce a value suitable for ranking.
- `CompositeFactor` supports winsorization, z-scores, component directions and weights, and an explicit policy for missing values.
- `TopNTargetWeightGenerator` ranks a sufficient sample of valid values, breaks ties by `instrument_id`, and assigns equal weights that sum to exactly 1.00000000.
- `ThresholdQuantitySignalGenerator` tests a single instrument for upward or downward crossings and emits a quantity intent. It can only read observations available at decision time.

## Test and implementation sequence

1. Red. Test registry behavior, the fixed Arrow schema, snapshot provenance, cutoffs, and duplicate IDs.
2. Green. Implement the registry, executor, and schema validator.
3. Red. Hand-calculate momentum for three instruments. Cover short windows, sort order, ties, top-N weights, and T+1 timing.
4. Green. Implement Momentum, Value, Composite, and cross-sectional signals.
5. Red. Test a threshold series, ETF identity, duplicate times, future rows, and quantity intents.
6. Green. Implement the single-instrument signal generator.
7. Generate both kinds of signals from a real M1 Parquet/DuckDB snapshot and conduct one independent review.

## Acceptance and results

To pass, the computed values and weights had to match the hand calculations, output tables had to retain their schema and identify their input snapshot, and signals based on closing data had to wait until the next session. Integration tests for stock selection and single-instrument/ETF signals both used Arrow tables from M1 data snapshots. The required checks included pytest, Ruff, strict mypy, and wheel/sdist builds. The broader system review was scheduled after the M6 end-to-end run.

M1 was complete when these tests ran. The M2 integration cases used real Parquet files, the DuckDB catalog, and a PIT snapshot to generate cross-sectional top-N and ETF buy signals. The test provider rejected all fetches, verifying that the local M1 objects supplied the data.

| Stage | Evidence |
|---|---|
| Red 1 | Collection of `tests/test_factor_interfaces.py` failed because `trademaster.factors` did not exist. The implementation added the registry, executor, schema, provenance, and cutoff checks. |
| Red 2 | `tests/test_momentum_and_signals.py` could not import `MomentumFactor`. The fix added per-instrument session lags, stable top-N selection, exact Decimal equal weights, and execution at the next eligible time. |
| Red 3 | `tests/test_value_composite_threshold.py` could not import `CompositeComponent`. The implementation added the market-value factor, cross-sectional z-score composite, and single-instrument threshold crossings. |
| Initial Green | 11 tests passed across four focused files. Ruff and strict mypy passed. With local credentials, 118 Python tests passed, 0 skipped, including the real Tushare `trade_cal` smoke test. The wheel and sdist contained factors, signals, `py.typed`, and the shared schema. |
| Independent review | 0 Critical and 7 High findings in the main interfaces and workflow. The team addressed them together without opening a second review. The new and revised regression tests initially gave 10 failed and 2 passed. They covered execution timing, artifact enums, Protocol conformance, stale crossings, factor identity, `known_at`, and dependency validation. |
| Final Green | 16 focused tests passed. With local credentials, 122 Python tests passed, 0 skipped. Ruff, strict mypy (27 source files), wheel/sdist builds, and `git diff --check` passed. The repository contained 0 exact matches for the credential. |
