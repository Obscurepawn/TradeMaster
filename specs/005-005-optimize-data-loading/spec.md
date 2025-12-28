# Feature Specification: Data Loading Optimization (No-Data Caching)

**Feature Branch**: `005-optimize-data-loading`
**Created**: 2025-12-28
**Status**: Draft

## Overview
Optimize the data loading process by caching dates that have no trading data (e.g., weekends, holidays). This prevents the system from making redundant remote API calls for dates already known to be empty.

## User Stories
- **US1**: As a developer, I want the system to remember when a remote fetch returned no data for a specific date, so that subsequent backtests don't waste time and resources re-fetching it.

## Requirements
- **FR-001**: Implement a mechanism to track "checked but empty" dates in the DuckDB cache.
- **FR-002**: Update `AkshareLoader` to record an empty result in the cache after a failed/empty remote fetch.
- **FR-003**: Update `AkshareLoader` to consult the "empty dates" cache before initiating any remote fetch.
- **FR-004**: Ensure the system correctly distinguishes between "data not yet fetched" and "data fetched but confirmed empty".

## Success Criteria
- **SC-001**: Backtest logs show that remote fetching is NOT triggered for weekends/holidays that have been previously checked.
- **SC-002**: The DuckDB database contains a persistent record of checked dates with no data.