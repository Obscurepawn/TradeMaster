# Strategy Agent Context

## Responsibility
Defines the abstract interface and concrete implementations for trading strategies. Strategies use the global logger to record initialization and trade signals (BUY/SELL).

## Implementation
- `base.py`: Provides `BaseStrategy` inheriting from the core `Strategy` interface, offering a foundation for concrete implementations.
- `pe_small_cap.py`: A concrete implementation of a Value/Small-Cap strategy using equal-weight position sizing.
- All classes and methods are documented using Google Style docstrings.

## Testing
- `test_strategy.py`: Verifies signal generation logic and bar-processing behavior using mock data.
