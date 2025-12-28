from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Union

@dataclass
class LoggingConfig:
    """Configuration for the global logger.

    Attributes:
        level: Logging level (e.g., INFO, DEBUG).
        file_path: Optional path to save logs to a file.
        console: Whether to output logs to the console.
    """
    level: str = "INFO"
    file_path: Optional[str] = None
    console: bool = True

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
        logging: Configuration for logging.
    """
    start_date: date
    end_date: date
    initial_cash: float
    strategy_name: str
    baseline: Union[str, List[str]]
    universe: Optional[List[str]] = None
    logging: LoggingConfig = field(default_factory=LoggingConfig)
