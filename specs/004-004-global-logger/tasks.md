# Tasks: Global Logger Implementation

**Branch**: `004-add-global-logger` | **Spec**: `specs/004-004-global-logger/spec.md`
**Plan**: `specs/004-004-global-logger/plan.md`

## Summary

This task list covers the transition from `print` statements to a centralized Python `logging` system. It includes configuration schema updates, the creation of a logging utility, and a project-wide replacement of console output calls.

**Total Tasks**: 14
**Phases**: Setup, Foundational, [US1] Standardized Logging, [US2] Configurable Persistence, Polish

## Dependencies

- **Foundational** (Phase 2) must be complete before any module-specific logging (Phase 3) can be implemented.
- **[US1]** and **[US2]** are largely independent implementation-wise but both depend on the Foundational utility.

---

## Phase 1: Setup

**Goal**: Initialize configuration context.

- [x] T001 Review configuration requirements and verify Technical Context in `specs/004-004-global-logger/plan.md`

---

## Phase 2: Foundational

**Goal**: Establish the logging infrastructure and configuration schema.

- [x] T002 Define `LoggingConfig` dataclass and update `BacktestConfig` in `src/config/schema.py`
- [x] T003 Implement `setup_logging` utility with Stream and File handlers in `src/config/logging_config.py`
- [x] T004 Update `load_config` to parse the `logging` section in `src/config/settings.py`
- [x] T005 Integrate `setup_logging` into the initialization sequence in `src/main.py`

---

## Phase 3: [US1] Standardized Logging

**Goal**: Replace all `print` statements with structured logger calls in core logic.

- [x] T006 [US1] Replace `print` with logging in `src/backtest/engine.py` and `src/backtest/portfolio.py`
- [x] T007 [US1] Replace `print` with logging in `src/strategy/pe_small_cap.py`
- [x] T008 [US1] Replace `print` with logging in `src/data_loader/akshare_loader.py` and `src/data_loader/cache.py`
- [x] T009 [US1] [P] Replace `print` with logging in `src/proxy/manager.py` and `src/drawing/plotter.py`
- [x] T010 [US1] Replace remaining `print` calls in `src/main.py` CLI logic

---

## Phase 4: [US2] Configurable Persistence

**Goal**: Ensure logs are correctly written to the user-specified file path.

- [x] T011 [US2] Add automated directory creation for log file paths in `src/config/logging_config.py`
- [x] T012 [US2] Create integration test `test/config/test_logging.py` to verify file output based on config

---

## Phase 5: Polish

**Goal**: Final verification and context updates.

- [x] T013 Verify that `src/config/logging_config.py` and related changes meet the 70% test coverage requirement using `coverage run`
- [x] T014 Perform a project-wide audit for any remaining `print(` statements in `src/`
- [x] T015 Update `Agents.md` files in `src/backtest/`, `src/strategy/`, `src/data_loader/`, `src/proxy/`, `src/drawing/`, and `src/config/` to reflect logging usage

---

## Implementation Strategy

1. **Foundational First**: We must update the schema and utility first so that `logger.info()` etc. have a configured destination.
2. **Incremental Migration**: Modules will be updated one by one. Each step should be verified by running `scripts/run_backtest.sh` and checking console output.
3. **Validation**: The final audit ensures no legacy `print` calls are left in the production code.
