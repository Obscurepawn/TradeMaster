# Research: No-Data Caching Strategy

## Decisions

### Decision: Dedicated Table for Empty Dates
- **Choice**: Create a new table `empty_dates (code VARCHAR, date DATE, PRIMARY KEY (code, date))` in DuckDB.
- **Rationale**: 
    - Separation of concerns: Keep the `daily_bars` table strictly for valid OHLCV data.
    - Query efficiency: Easier to check for existence in a specialized table than handling NULLs or sentinel values in the main data table.
    - Cleanliness: Avoids polluting OHLCV numeric columns with markers.
- **Alternatives considered**: 
    - Adding an `is_empty` boolean to `daily_bars`. Rejected because it would require inserting rows with NULL OHLCV values, which might complicate existing aggregate queries or calculations.
    - Using a sentinel value (e.g., -1 in Volume). Rejected as it's less explicit.

### Decision: Bulk Check
- **Choice**: When loading data for a range, fetch both `daily_bars` and `empty_dates` to identify the true "missing" gaps.
