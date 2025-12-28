# Quickstart: Contributing Documentation

## Standards
All new code must include Google Style docstrings in English.

## Example
When adding a new strategy:
```python
class MyStrategy(Strategy):
    """Implementation of a Mean Reversion strategy.
    
    This strategy buys when the price is below the 20-day MA.
    """
    
    def on_bar(self, context, bar_dict):
        """Called every trading day to evaluate signals.
        
        Args:
            context: The backtest context providing access to portfolio.
            bar_dict: Daily market data for the current universe.
        """
        # ... logic
```
