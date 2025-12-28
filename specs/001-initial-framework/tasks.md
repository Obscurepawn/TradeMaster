# Tasks: TradeMaster Initial Framework

**Branch**: `001-initial-framework` | **Spec**: `/home/obscure/TradeMaster/specs/001-initial-framework/spec.md`
**Plan**: `/home/obscure/TradeMaster/specs/001-initial-framework/plan.md`

## Summary

Implement the TradeMaster backtesting framework with configurable strategies, data acquisition, and result visualization.

**Total Tasks**: 26
**Phases**: Setup, Foundational, US1 (Execution), US2 (Visuals), US5 (Proxy), US3 (Robust Data), US4 (Strategies).

## Dependencies

- **US1 (Execution)** depends on **Foundational** (Interfaces).
- **US2 (Visuals)** depends on **US1** (Result generation).
- **US5 (Proxy)** independent, but utilized by Data Loader.
- **US3 (Robust Data)** enhances **US1** Data Loader.
- **US4 (Strategies)** extends **US1** Strategy Interface.

---

## Phase 1: Setup

**Goal**: Initialize project structure and environment.

- [x] T001 Create project directory structure per plan in `src/` and `test/`
- [x] T002 Create `requirements.txt` with `akshare`, `duckdb`, `matplotlib`, `pyyaml`, `requests`
- [x] T003 Create `Agents.md` in `src/Agents.md` and subdirectories
- [x] T004 Create operational scripts directory `scripts/`

---

## Phase 2: Foundational

**Goal**: Define core interfaces and data structures.

- [x] T005 [P] Create Abstract Base Classes (Strategy, DataSource) in `src/contracts/interfaces.py`
- [x] T006 [P] Define Domain Entities (Trade, Position, BacktestResult) in `src/domain.py`
- [x] T007 Create basic test runner script in `scripts/run_tests.sh`

---

## Phase 3: User Story 1 - Backtesting Configuration & Execution (P1)

**Goal**: Core MVP Loop - Config -> Data -> Backtest -> Result.

**Independent Test**: Run `./scripts/run_backtest.sh` with `config_mvp.yaml`. Verify data download and completion.

- [x] T008 [US1] Implement Config Loader and Schema validation in `src/config/settings.py`
- [x] T009 [US1] Implement Basic Akshare Data Loader (fetch only) in `src/data_loader/akshare_loader.py`
- [x] T010 [US1] Implement Portfolio logic (Position tracking, Cash update) in `src/backtest/portfolio.py`
- [x] T011 [US1] Implement Backtest Engine (Time loop, Strategy calls) in `src/backtest/engine.py`
- [x] T012 [US1] Implement Main CLI entry point to orchestrate workflow in `src/main.py`
- [x] T013 [US1] Create MVP Configuration file `config_mvp.yaml`
- [x] T014 [US1] Create execution script `scripts/run_backtest.sh`

---

## Phase 4: User Story 2 - Interactive Results Visualization (P2)

**Goal**: Generate Equity Curve chart.

**Independent Test**: Check `results/` for `.png` or `.html` file after run.

- [x] T015 [US2] Implement Matplotlib Plotter for Equity Curve in `src/drawing/plotter.py`
- [x] T016 [US2] Integrate Plotter into Main CLI in `src/main.py`

---

## Phase 5: User Story 5 - Anti-Scraping Proxy Layer (P2)

**Goal**: Proxy rotation to prevent bans.

**Independent Test**: Verify `ProxyManager` rotates IP using a mock Clash config.

- [x] T017 [US5] Implement ProxyManager with Clash Config parsing in `src/proxy/manager.py`
- [x] T018 [US5] Implement Random Node Selection logic in `src/proxy/manager.py`
- [x] T019 [US5] Implement Request Hook for User-Agent spoofing in `src/proxy/hook.py`

---

## Phase 6: User Story 3 - Robust Data Acquisition (P3)

**Goal**: Caching and Retries.

**Independent Test**: Run backtest twice; second run should be instant (Cache Hit).

- [x] T020 [US3] Implement DuckDB Cache Manager in `src/data_loader/cache.py`
- [x] T021 [US3] Integrate DuckDB Cache into Akshare Loader in `src/data_loader/akshare_loader.py`
- [x] T022 [US3] Integrate ProxyManager into Data Loader for retries in `src/data_loader/akshare_loader.py`

---

## Phase 7: User Story 4 - Extensible Strategy Development (P4)

**Goal**: Strategy implementation example.

**Independent Test**: Config runs with `PESmallCapStrategy`.

- [x] T023 [US4] Implement PE Small Cap Strategy logic in `src/strategy/pe_small_cap.py`
- [x] T024 [US4] Register Strategy in factory/loader in `src/strategy/base.py`

---

## Phase 8: Polish

**Goal**: Documentation and Cleanup.

- [x] T025 Update root `README.md` with usage instructions
- [x] T026 Finalize `src/Agents.md` with component details