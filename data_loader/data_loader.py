import pandas as pd
from abc import ABC, abstractmethod
from typing import Optional


class DataLoader(ABC):
    """Abstract base class for data loaders"""

    @abstractmethod
    def get_stock_list(self) -> pd.DataFrame:
        """
        Get stock list

        Returns:
            pd.DataFrame: Stock list data
        """
        pass

    @abstractmethod
    def get_stock_basic_info(self, symbol: str) -> pd.DataFrame:
        """
        Get basic stock information

        Args:
            symbol (str): Stock symbol

        Returns:
            pd.DataFrame: Basic stock information
        """
        pass

    @abstractmethod
    def get_stock_profile_info(self, symbol: str) -> pd.DataFrame:
        """
        Get stock profile information

        Args:
            symbol (str): Stock symbol

        Returns:
            pd.DataFrame: Stock profile information
        """
        pass

    @abstractmethod
    def get_stock_history(self, symbol: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        Get historical data for a single stock

        Args:
            symbol (str): Stock symbol
            **kwargs: Other parameters, such as period, adjust, start_date, end_date, etc.

        Returns:
            Optional[pd.DataFrame]: Stock historical data, returns None if failed to retrieve
        """
        pass

    @abstractmethod
    def get_stock_histories(self, stock_list: pd.DataFrame, **kwargs) -> None:
        """
        Get historical data for multiple stocks

        Args:
            stock_list (pd.DataFrame): Stock list
            **kwargs: Other parameters, such as period, adjust, start_date, end_date, etc.
        """
        pass

    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators

        Args:
            df (pd.DataFrame): Stock data

        Returns:
            pd.DataFrame: Stock data with technical indicators
        """
        # Default implementation, can be overridden by subclasses
        return df

    @abstractmethod
    def get_financial_indicators(self, symbol: str, indicator: str = "by_report") -> pd.DataFrame:
        """
        Get financial indicators for a stock

        Args:
            symbol (str): Stock symbol
            indicator (str): Indicator type, default is "by_report", options are "by_quarter"

        Returns:
            pd.DataFrame: Financial indicators data
        """
        pass

    @abstractmethod
    def get_shareholder_surplus(self, symbol: str) -> pd.DataFrame:
        """
        Get shareholder surplus data
        Shareholder surplus = Net profit + Depreciation and amortization - Capital expenditure

        Args:
            symbol (str): Stock symbol

        Returns:
            pd.DataFrame: Shareholder surplus data with columns ['report_date', 'shareholder_surplus']
        """
        pass
