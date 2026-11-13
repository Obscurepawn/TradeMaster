# Signals Guide

## Scope

`trademaster.signals` transforms point-in-time factor observations from a verified data snapshot into deterministic Arrow runtime signal tables via `SignalGenerator.generate(context)`.

## Invariants

- **Account-free boundary**: Signal generation has zero access to account cash, existing holdings, daily price limits, or instrument lot sizes.
- **Deterministic cross-sectional weights**: Rank candidates by factor score with `instrument_id` tie-breaking. Assign unselected universe members an explicit zero target weight so downstream rebalancers exit stale holdings, and express positive allocations as exact 8-decimal weights summing to `1.00000000` across selected instruments.
- **Causal scheduling and provenance**: Read the observation cutoff and first eligible execution timestamp from `SignalContext`, scheduling execution strictly after the observation cutoff. Bind each `SignalGenerator` to a single `factor_id@version`, and propagate the data snapshot ID and provenance hashes into every output row and Arrow schema metadata.

## Verification

```bash
uv run pytest tests/test_momentum_and_signals.py tests/test_factor_signal_data_integration.py
```
