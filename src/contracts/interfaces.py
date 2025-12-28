from abc import ABC, abstractmethod
from datetime import date
from typing import Dict
import pandas as pd

if False:
    # Type checking import to avoid circular dependency at runtime
    from src.backtest.portfolio import BacktestContext


class Strategy(ABC):
    """Abstract base class for all trading strategies.

    Strategies operate on a daily frequency and react to market data bars.
    """

    @abstractmethod
    def on_init(self, context: 'BacktestContext'):
        """Called once before the backtest loop begins.

        Use this method to initialize strategy-specific variables, indicators,
        or register data requirements with the context.

        Args:
            context: The backtest execution context providing access to portfolio
                state and utility methods.
        """
        pass

    @abstractmethod
    def on_bar(self, context: 'BacktestContext', bar_dict: Dict[str, pd.Series]):
        """Called on every trading day bar (End of Day).

        This is the main logic loop for the strategy. It should evaluate market
        conditions and submit orders via the context.

        Args:
            context: Access to account state (cash, positions) and order API.
            bar_dict: Dictionary mapping stock symbols (e.g., 'SH600000') to
                their daily bar data as a pandas Series containing Open, High,
                Low, Close, and Volume.
        """
        pass


class DataSource(ABC):
    """Abstract interface for remote market data sources.

    Implementations are responsible for fetching market data from remote APIs.
    Caching and orchestration are handled by the higher-level DataLoader.
    """

    @abstractmethod
    def fetch_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch daily OHLCV data for a specific stock code from a remote source.

        Args:
            code: The stock symbol or security identifier.
            start_date: The beginning of the date range (inclusive).
            end_date: The end of the date range (inclusive).

        Returns:
            A pandas DataFrame with a DatetimeIndex and columns:
            ['open', 'high', 'low', 'close', 'volume'].
        """
        pass

    @abstractmethod
    def fetch_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch daily data for an index (benchmark) from a remote source.

        Args:
            code: The index symbol (e.g., '000300.SH').
            start_date: The beginning of the date range (inclusive).
            end_date: The end of the date range (inclusive).

        Returns:
            A pandas DataFrame with index data similar to stock bars.
        """
        pass
