# Tasks: Data Loading Optimization

**Branch**: `005-optimize-data-loading` | **Spec**: `specs/005-005-optimize-data-loading/spec.md`
**Plan**: `specs/005-optimize-data-loading/plan.md`

## Summary
Implement caching for "no data" results to prevent redundant remote fetching for non-trading days.

**Total Tasks**: 6
**Phases**: Setup, Foundational, Implementation, Polish

---

## Phase 1: Setup
- [ ] T001 Verify design artifacts and DuckDB schema plan

---

## Phase 2: Foundational (Cache Updates)
- [ ] T002 Update `CacheManager.init_schema` to create `empty_dates` table in `src/data_loader/cache.py`
- [ ] T003 Implement `load_empty_dates` and `save_empty_dates` in `src/data_loader/cache.py`

---

## Phase 3: Implementation (Loader Logic)
- [ ] T004 Update `AkshareLoader._fetch_remote_and_save` to record empty dates in cache in `src/data_loader/akshare_loader.py`
- [ ] T005 Update `AkshareLoader.get_daily_bars` (and index method) to skip fetching if date is in `empty_dates` cache in `src/data_loader/akshare_loader.py`

---

## Phase 4: Polish
- [ ] T006 Verify optimization by running a backtest over a weekend and checking logs in `scripts/run_backtest.sh`
