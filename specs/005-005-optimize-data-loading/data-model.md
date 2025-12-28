# Data Model: Empty Dates Tracking

## DuckDB Schema Update

### New Table: `empty_dates`
Used to record dates where a remote fetch was attempted but returned no market data.

| Column | Type | Description |
|--------|------|-------------|
| code   | VARCHAR | Security identifier (e.g., 'sh600000') |
| date   | DATE    | The specific date checked |

**Constraints**:
- PRIMARY KEY (code, date)
