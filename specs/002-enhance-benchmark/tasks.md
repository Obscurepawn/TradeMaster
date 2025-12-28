# Tasks: Enhance Baseline Support

**Branch**: `002-enhance-benchmark` | **Spec**: `/home/obscure/TradeMaster/specs/002-enhance-benchmark/spec.md`
**Plan**: `/home/obscure/TradeMaster/specs/002-enhance-benchmark/plan.md`

## Summary

This feature replaces the single `benchmark` parameter with a flexible `baseline` system, supporting multiple comparison curves in the backtest results chart.

**Total Tasks**: 9
**Phases**: Foundational, US1 (Multiple Baseline Comparison), Polish

## Dependencies

- **US1** depends on **Foundational** (Schema/Domain updates).
- **Visualization** (T007) depends on **Engine** updates (T005, T006).

---

## Phase 1: Setup

*No infrastructure changes required.*

---

## Phase 2: Foundational

**Goal**: Update configuration and domain models to support multiple baselines.

- [x] T001 Update `BacktestConfig` in `src/config/schema.py` (Rename `benchmark` to `baseline`, change type to `Union[str, List[str]]`)
- [x] T002 [P] Update `BacktestResult` in `src/domain.py` to add `baselines: Dict[str, List[float]]` field
- [x] T003 Update `load_config` in `src/config/settings.py` to handle the `baseline` parameter renaming and ensure it returns a list

---

## Phase 3: User Story 1 - Multiple Baseline Comparison (P1)

**Goal**: Implement data fetching, processing, and multi-line plotting.

**Independent Test**: Run a backtest with `baseline: [sh000300, sh000905]` and verify `results/equity_curve.png` contains three curves with a legend.

- [x] T004 [US1] Implement `get_index_daily` in `src/data_loader/akshare_loader.py` using `ak.stock_zh_index_daily`
- [x] T005 [US1] Update `BacktestEngine.run` in `src/backtest/engine.py` to iterate through all specified baselines and fetch their data
- [x] T006 [US1] Implement normalization logic in `src/backtest/engine.py` to ensure all curves start at 1.0 (or matching scale)
- [x] T007 [US1] Update `Plotter.plot_result` in `src/drawing/plotter.py` to iterate over `result.baselines` and plot labeled curves with a legend
- [x] T008 Update `config_mvp.yaml` to use the new `baseline` parameter name and list format

---

## Phase 4: Polish

**Goal**: Update documentation.

- [x] T009 Update `Agents.md` in `src/config`, `src/data_loader`, `src/backtest`, and `src/drawing` to reflect baseline changes
