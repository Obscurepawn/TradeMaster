# Data Loader Agent Context

## Responsibility
Fetches market data from external sources (Akshare/Tushare) and manages local caching.

## Implementation
- `base.py`: `DataSource` abstract base class.
- `akshare_loader.py`: Implementation using Akshare API. Now supports `get_index_daily` for fetching market baselines.
- `cache.py`: DuckDB interface for saving/loading daily bars (supports both stocks and indices).

## Testing
- `test_loader.py`: Mock network calls to verify data parsing.
- `test_cache.py`: Verify DuckDB reads/writes.