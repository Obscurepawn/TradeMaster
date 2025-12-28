# Data Loader Agent Context

## Responsibility
Provides a unified interface for fetching market data from external sources and managing a local persistent cache. Logs all remote fetch and cache operations.

## Implementation
- `akshare_loader.py`: Implementation using the Akshare library with incremental loading logic (checking cache first, then fetching gaps). Remote requests are automatically protected by the global anti-scraping hook (headers, proxies, jitter). It implements the `DataSource` interface defined in `src/contracts/interfaces.py`.
- `cache.py`: DuckDB-backed storage manager for daily market bars and index data.
- Documentation follows Google Style standards with full type hint support.

## Testing
- `test_loader.py`: Verifies network interaction logic and data frame formatting.
- `test_cache.py`: Ensures DuckDB operations (Read/Write/Schema) are reliable.
