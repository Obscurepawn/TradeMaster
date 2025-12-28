# Data Loader Agent Context

## Responsibility
Provides a decoupled and extensible infrastructure for market data acquisition. It separates the orchestration logic (caching, gap filling) from specific remote data source implementations.

## Implementation
- `base.py`: Contains the `DataLoader` orchestrator. It manages the flow of checking `CacheManager`, identifying missing data segments, and calling a `DataSource` to fill those segments.
- `akshare_loader.py`: Implements the `DataSource` interface using the Akshare library. It focuses strictly on remote data fetching (`fetch_daily_bars`, `fetch_index_daily`).
- `cache.py`: DuckDB-backed storage manager. It remains agnostic of which source or loader is using it.
- Orchestration: The `DataLoader` ensures that remote requests are minimized and that any fetched data is automatically cached for future use.

## Testing
- `test_loader.py`: Contains unit tests for both the generic `DataLoader` (using mocks) and the specific `AkshareSource` implementation.
- `test_cache.py`: Verifies DuckDB storage and retrieval operations.