# Backtest Agent Context

## Responsibility
Core engine that orchestrates the trading simulation, managing data flow and strategy execution. All execution events are recorded via the global logging system.

## Implementation
- `engine.py`: Orchestrates the main backtest loop, data pre-fetching, and results aggregation. Fully documented with Google Style docstrings.
- `portfolio.py`: Maintains account state, including cash, positions, and trade history. Uses `Position` and `Trade` domain entities.

## Testing
- `test_engine.py`: Integration tests for the full execution flow.
- `test_portfolio.py`: Unit tests for trade execution logic and portfolio value calculations.
