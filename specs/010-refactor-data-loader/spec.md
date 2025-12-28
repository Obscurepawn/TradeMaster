# Feature Spec: Refactor DataLoader and Decouple Caching

## User Requirements
- The current implementation exposes `AkshareLoader` directly, which is not extensible for multiple data sources.
- Cache logic is tied to `AkshareLoader`, causing duplication if a new loader is added.
- Refactor the system to use a generic `DataLoader` that can work with any `DataSource` implementation while sharing a centralized cache management logic.

## Current State
- `src/contracts/interfaces.py` defines `DataSource` with `get_daily_bars` and `get_index_daily`.
- `AkshareLoader` implements `DataSource` but internalizes `CacheManager`.
- `CacheManager` handles DuckDB interactions.
- `src/main.py` directly instantiates `AkshareLoader`.

## Goals
1. Refine the `DataSource` interface to strictly handle remote fetching.
2. Implement a generic `DataLoader` class that orchestrates:
    - Checking local cache via `CacheManager`.
    - Identifying missing data gaps.
    - Fetching gaps via a injected `DataSource`.
    - Persisting fetched data back to cache.
3. Decouple `AkshareLoader` from `CacheManager`. It should only know how to talk to the remote API.
4. Update `src/main.py` to use the unified `DataLoader`.
5. Ensure the new architecture is easy to extend with new `DataSource` implementations (e.g., Baostock, Tushare).

## Design Pattern
- **Strategy Pattern**: `DataSource` implementations (Akshare, etc.) are strategies for remote data fetching.
- **Dependency Injection**: `DataLoader` receives a `DataSource` and a `CacheManager`.
- **Proxy/Wrapper Pattern**: `DataLoader` acts as a high-level wrapper providing caching and gap-filling logic around a raw `DataSource`.
