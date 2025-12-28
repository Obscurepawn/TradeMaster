from abc import ABC, abstractmethod
from datetime import date
from typing import Dict, List, Optional
import pandas as pd

class Strategy(ABC):
    """
    Abstract base class for all trading strategies.
    Operates on a Daily frequency.
    """
    
    @abstractmethod
    def on_init(self, context: 'BacktestContext'):
        """
        Called once before the backtest starts.
        Use for initialization of variables.
        """
        pass

    @abstractmethod
    def on_bar(self, context: 'BacktestContext', bar_dict: Dict[str, pd.Series]):
        """
        Called on every trading day (End of Day).
        
        Args:
            context: Access to account state (cash, positions) and order API.
            bar_dict: Dictionary mapping stock codes to their daily bar data (Open, High, Low, Close, Volume).
        """
        pass

class DataSource(ABC):
    """
    Abstract interface for market data providers.
    """
    
    @abstractmethod
    def get_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Fetch daily OHLCV data for a specific stock code.
        Should handle caching internally (check DuckDB first, then fetch remote).
        
        Returns:
            DataFrame with Index=Date, Columns=[open, high, low, close, volume]
        """
        pass

    @abstractmethod
    def get_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch daily data for an index (benchmark)."""
        pass