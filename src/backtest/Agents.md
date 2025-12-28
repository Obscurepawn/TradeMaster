# Backtest Agent Context

## Responsibility
Core engine that runs the simulation.

## Implementation
- `engine.py`: Main loop (Date iteration, Data fetching, Strategy execution).
- `portfolio.py`: Tracks Cash, Positions, and Trades.

## Testing
- `test_engine.py`: Integration test of the full loop.
- `test_portfolio.py`: Unit tests for position math.