from abc import ABC, abstractmethod
import re
import pandas as pd
from typing import Optional
from data_loader import constants
from utils.request_hook import install_user_agent_hooks
from proxy.clash.proxy import ClashController


def format_stock_history_filename(
    symbol: str, name: str, start_date: str, end_date: str
) -> str:
    return re.sub(r"\s+", f"{symbol}_{name}_{start_date}_{end_date}")


def format_stock_financial_abstract_ths_filename(
    symbol: str, start_date: str, end_date: str
) -> str:
    return re.sub(r"\s+", f"{symbol}_financial_abstract_ths_{start_date}_{end_date}")


class DataLoader(ABC):
    """Abstract base class for data loaders"""

    def __init__(self):
        pass

    @abstractmethod
    def get_stock_list(self) -> pd.DataFrame:
        """
        Get stock list

        Returns:
            pd.DataFrame: Stock list data
        Returns:
            pd.DataFrame: Combined stock historical data

        """
        pass

    @abstractmethod
    def get_stock_history(
        self,
        symbol: str,
        name: str = "",
        period: str = constants.PERIOD_DAILY,
        adjust: str = "qfq",
        start_date: str = "",
        end_date: str = "",
        retry_times: int = 3,
        sleep_seconds: float = 0.2,
    ) -> pd.DataFrame:
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
            pd.DataFrame: Stock historical data
        Returns:
            pd.DataFrame: Combined stock historical data

        """
        pass

    @abstractmethod
    def get_stock_histories(
        self,
        stock_list: pd.DataFrame,
        period: str = "daily",
        adjust: str = "qfq",
        start_date: str = "",
        end_date: str = "",
        retry_times: int = 3,
        sleep_seconds: float = 0.2,
    ) -> pd.DataFrame:
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
        Returns:
            pd.DataFrame: Combined stock historical data

        """
        pass

    def get_financial_indicators(
        self, symbol: str, indicator: str = "by_report"
    ) -> pd.DataFrame:
        """
        Get financial indicators for a stock

        Args:
            symbol (str): Stock symbol
            indicator (str): Indicator type, default is "by_report", options are "by_quarter"

        Returns:
            pd.DataFrame: Financial indicators data
        """
        raise NotImplementedError("This method should be implemented by subclasses")


class DataLoaderWrapper(DataLoader):
    """Wrapper class for DataLoader to provide additional functionality"""

    def __init__(
        self,
        data_loader: DataLoader,
        enable_user_agent_hook: bool = True,
        proxy_controller: ClashController = None,
    ):
        """
        Initialize DataLoaderWrapper with a specific DataLoader instance

        Args:
            data_loader (DataLoader): Instance of a specific data loader
        """
        super().__init__()
        self.data_loader = data_loader
        self.proxy_controller = None
        if enable_user_agent_hook:
            install_user_agent_hooks()
        if proxy_controller is not None:
            self.proxy_controller = proxy_controller

    def _preprocess(self):
        """
        Preprocessor to attempt to switch proxy randomly if available
        """
        if self.proxy_controller is not None:
            try:
                self.proxy_controller.change_random_proxy()
            except Exception as e:
                print(f"Fail to switch proxy: {e}")

    def _apply_middleware(self, func, *args, **kwargs):
        """
        Apply middleware to a function call
        """
        self._preprocess()
        result = func(*args, **kwargs)
        self._preprocess()
        return result

    def get_stock_list(self) -> pd.DataFrame:
        return self._apply_middleware(self.data_loader.get_stock_list)

    def get_stock_history(
        self,
        symbol: str,
        name: str = "",
        period: str = constants.PERIOD_DAILY,
        adjust: str = "qfq",
        start_date: str = "",
        end_date: str = "",
        retry_times: int = 3,
        sleep_seconds: float = 0.2,
    ) -> pd.DataFrame:
        return self._apply_middleware(
            self.data_loader.get_stock_history,
            symbol,
            name,
            period,
            adjust,
            start_date,
            end_date,
            retry_times,
            sleep_seconds,
        )

    def get_stock_histories(
        self,
        stock_list: pd.DataFrame,
        period: str = "daily",
        adjust: str = "qfq",
        start_date: str = "",
        end_date: str = "",
        retry_times: int = 3,
        sleep_seconds: float = 0.2,
    ) -> pd.DataFrame:
        return self._apply_middleware(
            self.data_loader.get_stock_histories,
            stock_list,
            period,
            adjust,
            start_date,
            end_date,
            retry_times,
            sleep_seconds,
        )

    def get_financial_indicators(
        self, symbol: str, indicator: str = "by_report"
    ) -> pd.DataFrame:
        return self._apply_middleware(
            self.data_loader.get_financial_indicators,
            symbol,
            indicator,
        )
