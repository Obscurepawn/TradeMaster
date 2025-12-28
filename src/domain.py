from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import List, Optional, Dict

class TradeDirection(Enum):
    """Enumeration for trade directions.

    Attributes:
        BUY: Represents a long entry or buying to cover.
        SELL: Represents a short entry or selling to close.
    """
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Position:
    """Represents a held position in a security.

    Attributes:
        code: The security identifier.
        quantity: The number of shares or units held.
        avg_cost: The average price at which the position was acquired.
        current_price: The latest known market price of the security.
    """
    code: str
    quantity: int
    avg_cost: float
    current_price: float

    @property
    def market_value(self) -> float:
        """Calculates the current market value of the position.

        Returns:
            The total value (quantity * current_price).
        """
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        """Calculates the unrealized profit or loss.

        Returns:
            The PnL based on the difference between current price and average cost.
        """
        return (self.current_price - self.avg_cost) * self.quantity


@dataclass
class Trade:
    """Represents a single executed trade.

    Attributes:
        date: The execution date.
        code: The security identifier.
        direction: Whether the trade was a buy or sell.
        quantity: The number of shares traded.
        price: The execution price per share.
        cost: The total financial impact of the trade, including commissions.
        commission: The fees paid for the transaction.
    """
    date: date
    code: str
    direction: TradeDirection
    quantity: int
    price: float
    cost: float
    commission: float


@dataclass
class BacktestResult:
    """Summary of a backtest execution performance.

    Attributes:
        total_return: The cumulative return percentage over the period.
        max_drawdown: The largest peak-to-trough decline observed.
        sharpe_ratio: The risk-adjusted return metric.
        equity_curve: A daily snapshot of the total portfolio value.
        baselines: Comparison data (e.g., benchmark index performance).
        dates: The list of dates corresponding to the equity curve and baselines.
    """
    total_return: float
    max_drawdown: float
    sharpe_ratio: float
    equity_curve: List[float]
    baselines: Dict[str, List[float]]
    dates: List[date]
