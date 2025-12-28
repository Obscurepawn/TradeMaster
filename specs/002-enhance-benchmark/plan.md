# Implementation Plan: Enhance Baseline Support

**Branch**: `002-enhance-benchmark` | **Date**: 2025-12-28 | **Spec**: `/home/obscure/TradeMaster/specs/002-enhance-benchmark/spec.md`

## Summary

This enhancement refactors the existing benchmark logic into a more flexible "baseline" system. It renames the configuration parameter, enables multi-baseline support, and updates the visualization engine to overlay multiple comparison curves.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: `matplotlib`, `akshare`, `pandas`
**Storage**: DuckDB (for baseline data caching)
**Testing**: `unittest`
**Constraints**: Baseline data must be normalized to starting value (1.0 or 100) for direct yield comparison.

## Constitution Check

- **Backtesting First**: ✅ Directly improves quantitative comparison.
- **Tech Stack**: ✅ Adheres to Python/Matplotlib stack.
- **Real-World Testing**: ✅ Requires real data fetching for indices.

## Project Structure

### Documentation

```text
specs/002-enhance-benchmark/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── tasks.md
```

### Source Code Changes

```text
src/
├── config/
│   ├── schema.py        # Rename benchmark -> baseline, support List[str]
│   └── settings.py      # Update loader logic
├── data_loader/
│   ├── akshare_loader.py # Implement get_index_daily
│   └── cache.py         # Ensure index data is cached correctly
├── backtest/
│   └── engine.py        # Fetch data for multiple baselines
└── drawing/
    └── plotter.py       # Loop through baselines and plot multiple lines
```

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |