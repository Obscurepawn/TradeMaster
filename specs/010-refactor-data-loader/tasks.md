# Tasks: Refactor DataLoader and Decouple Caching

## Phase 1: Interface & Core Logic (Infrastructure)
- [X] Update `DataSource` interface in `src/contracts/interfaces.py` to use `fetch_` naming and remove orchestration logic.
- [X] Create `src/data_loader/base.py` with the generic `DataLoader` class.
- [X] Move `find_gaps` and orchestration logic from `AkshareLoader` to `DataLoader` in `src/data_loader/base.py`.

## Phase 2: Implementation & Decoupling
- [X] Rename `AkshareLoader` to `AkshareSource` in `src/data_loader/akshare_loader.py` and remove `CacheManager` dependency.
- [X] Update `AkshareSource` to implement the refined `DataSource` interface (only `fetch_` methods).
- [X] Simplify `AkshareSource` internal helpers.

## Phase 3: Integration & Main Entry
- [X] Update `src/main.py` to instantiate `CacheManager`, `AkshareSource`, and then the generic `DataLoader`.
- [X] Ensure all existing tests in `test/data_loader/` are updated to reflect the new class names and responsibilities.
- [X] Verify functionality with a full backtest run.

## Phase 4: Documentation & Cleanup
- [X] Update `Agents.md` files in `data_loader/` and root.
- [X] Remove any leftover redundant logic.
