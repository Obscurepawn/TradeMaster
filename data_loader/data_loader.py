from abc import ABC, abstractmethod
import pandas as pd
from typing import Optional
from utils.request_hook import install_hooks
from proxy.clash.proxy import ClashController

class DataLoader(ABC):
    """Abstract base class for data loaders"""

    def __init__(self):
        """Initialize DataLoader with HTTP request hooks installed"""
        # Install HTTP request hooks in the parent class initialization
        install_hooks()
        # Initialize proxy controller as None
        self.proxy_controller = None

    @abstractmethod
    def get_stock_list(self) -> pd.DataFrame:
        """
        Get stock list

        Returns:
            pd.DataFrame: Stock list data
        """
        pass

    @abstractmethod
    def get_stock_history(self, symbol: str, name: str = "", period: str = "daily",
                         adjust: str = "qfq", start_date: str = "", end_date: str = "",
                         retry_times: int = 3, sleep_seconds: float = 0.2) -> Optional[pd.DataFrame]:
        """
        Get historical data for a single stock

        Args:
            symbol (str): Stock symbol
            name (str): Stock name
            period (str): Period, default is "daily", options are "daily", "weekly", "monthly"
            adjust (str): Adjustment type, default is "qfq", options are "qfq", "hfq", "none"
            start_date (str): Start date, format "YYYYMMDD"
            end_date (str): End date, format "YYYYMMDD"
            retry_times (int): Retry times, default 3
            sleep_seconds (float): Sleep seconds between requests, default 0.2

        Returns:
            Optional[pd.DataFrame]: Stock historical data, returns None if failed to retrieve
        """
        pass

    @abstractmethod
    def get_stock_histories(self, stock_list: pd.DataFrame, period: str = "daily",
                           adjust: str = "qfq", start_date: str = "", end_date: str = "",
                           retry_times: int = 3, sleep_seconds: float = 0.2) -> None:
        """
        Get historical data for multiple stocks

        Args:
            stock_list (pd.DataFrame): Stock list
            period (str): Period, default is "daily", options are "daily", "weekly", "monthly"
            adjust (str): Adjustment type, default is "qfq", options are "qfq", "hfq", "none"
            start_date (str): Start date, format "YYYYMMDD"
            end_date (str): End date, format "YYYYMMDD"
            retry_times (int): Retry times, default 3
            sleep_seconds (float): Sleep seconds between requests, default 0.2
        """
        pass

    def set_proxy_controller(self, proxy_controller: ClashController) -> None:
        """
        Set the proxy controller for this data loader

        Args:
            proxy_controller (ClashController): Proxy controller instance
        """
        self.proxy_controller = proxy_controller

    def get_financial_indicators(self, symbol: str, indicator: str = "by_report") -> pd.DataFrame:
        """
        Get financial indicators for a stock

        Args:
            symbol (str): Stock symbol
            indicator (str): Indicator type, default is "by_report", options are "by_quarter"

        Returns:
            pd.DataFrame: Financial indicators data
        """
        raise NotImplementedError("This method should be implemented by subclasses")

    def get_shareholder_surplus(self, symbol: str) -> pd.DataFrame:
        """
        Get shareholder surplus data

        Args:
            symbol (str): Stock symbol

        Returns:
            pd.DataFrame: Shareholder surplus data with columns ['report_date', 'shareholder_surplus']
        """
        raise NotImplementedError("This method should be implemented by subclasses")
