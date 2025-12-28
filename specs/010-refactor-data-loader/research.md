# Research: Refactor DataLoader and Decouple Caching

## Decision: The Role of `DataSource`
**Rationale**: The `DataSource` interface should be minimal. It should only be responsible for fetching data for a given range from a remote source. It should NOT know about caching.
**Decision**: `DataSource` will have methods like `fetch_daily_bars(code, start, end)` and `fetch_index_daily(code, start, end)`.

## Decision: Unified Gap Detection in `DataLoader`
**Rationale**: The logic for identifying missing dates (checking `daily_bars` table and `empty_dates` table) is identical regardless of the data source.
**Decision**: Move the `find_gaps` logic from `AkshareLoader` to the generic `DataLoader`.

## Decision: Factory for DataLoaders
**Rationale**: To make it easy for users to switch sources.
**Decision**: In `main.py`, we will instantiate the desired `DataSource` and wrap it in a `DataLoader`.

## Comparison of Designs
| Feature | Current | Refactored |
|---------|---------|------------|
| Caching | Embedded in `AkshareLoader` | Handled by `DataLoader` (Strategy-agnostic) |
| Multi-source | Hard (Requires code duplication) | Easy (Inject new `DataSource`) |
| Responsibility | Mixed (Fetch + Cache) | Separate (Fetch vs. Orchestrate) |
