# Tasks: TradeMaster Initial Framework

**Branch**: `001-initial-framework`

## Summary
Core framework implementation with advanced caching and performance optimizations.

## Completed Tasks

### Phase 1: Setup & Foundational
- [x] T001 Initialize directory structure and requirements.
- [x] T002 Define Abstract interfaces and Domain entities.
- [x] T003 Create test runner and basic test suite (85% coverage).

### Phase 2: Core Engine & Data
- [x] T004 Implement **Batch Pre-fetching** in `BacktestEngine`.
- [x] T005 Implement **Incremental Filling** in `AkshareLoader` (handles partial DuckDB cache hits).
- [x] T006 Implement **Equal Weight** allocation in `PESmallCapStrategy`.
- [x] T007 Rename `benchmark` to `baseline` and support multiple indices.

### Phase 3: Visualization & Proxy
- [x] T008 Implement multi-line plotting with **1.0 Normalization** and legends.
- [x] T009 Implement Proxy Manager with random rotation support.

## Future Tasks
- [ ] T010 Add support for transaction slippage and detailed commission models.
- [ ] T011 Implement fundamental data caching (PE, PB, Market Cap).
