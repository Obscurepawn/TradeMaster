from abc import ABC, abstractmethod
from datetime import date
import pandas as pd

class DataLoader(ABC):
    """
    Abstract Base Class for Data Acquisition.
    """
    
    @abstractmethod
    def load_bars(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Load historical bar data for a symbol.
        
        Args:
            symbol: Stock symbol.
            start_date: Start date.
            end_date: End date.
            
        Returns:
            pd.DataFrame: Columns [date, open, high, low, close, volume]
        """
        pass

    @abstractmethod
    def get_snapshot(self, target_date: date) -> pd.DataFrame:
        """
        Get a cross-sectional snapshot of indicators for a specific date.
        
        Args:
            target_date: The date for which to retrieve indicators.
            
        Returns:
            pd.DataFrame: Columns [symbol, pe_ratio, market_cap, industry]
        """
        pass
