from src.contracts.interfaces import Strategy

class BaseStrategy(Strategy):
    """Base class for strategy implementations with common utilities.

    This class can be extended to provide shared functionality across different
    trading strategies, such as position sizing or common technical indicators.
    """
    
    def on_init(self, context):
        """Default initialization.
        
        Args:
            context: The backtest execution context.
        """
        pass

    def on_bar(self, context, bar_dict):
        """Default bar processing logic.

        Args:
            context: Access to account state and order API.
            bar_dict: Dictionary of daily market data bars.
        """
        pass