# Research: Enhanced Baseline Support

## Decisions & Rationale

### 1. Akshare Index Data API
- **Decision**: Use `ak.stock_zh_index_daily` for fetching A-share indices.
- **Rationale**: It provides clean daily OHLCV for indices like 000300 (HS300).
- **Implementation**: Needs to be integrated into `AkshareLoader.get_index_daily`.

### 2. Parameter Renaming Strategy
- **Decision**: Clean break - rename `benchmark` to `baseline` in `BacktestConfig`.
- **Rationale**: The user specifically requested the rename. Since this is an early-stage framework, a clean break is preferred over supporting both keys.

### 3. Normalization logic
- **Decision**: Each baseline's price series will be divided by its first value in the backtest range.
- **Rationale**: This allows all curves (Strategy and Baselines) to start at 1.0, making relative performance immediately visible.

## Unknowns Resolved
- **Akshare symbol format**: For `stock_zh_index_daily`, the symbol is usually just the 6-digit code (e.g., '000300').
- **Plotting colors**: Matplotlib will automatically cycle colors, but we should ensure a legend is generated to distinguish the strategy from baselines.
