from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Union

@dataclass
class BacktestConfig:
    """Configuration schema for a backtest execution.

    Attributes:
        start_date: The beginning of the backtest period.
        end_date: The end of the backtest period.
        initial_cash: Starting capital for the portfolio.
        strategy_name: Identifier for the strategy to be executed.
        baseline: Symbols of benchmark indices for performance comparison.
        universe: Optional list of stock symbols to include in the backtest.
    """
    start_date: date
    end_date: date
    initial_cash: float
    strategy_name: str
    baseline: Union[str, List[str]]
    universe: Optional[List[str]] = None
